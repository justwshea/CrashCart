"""Coordinate one incident analysis without loading evaluation truth."""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import Callable, Optional

from app.cluster import cluster_findings
from app.config import settings
from app.eyes import read_dashboard
from app.export import write_exports
from app.ingest import Bundle, chunks_for_bundle, file_stats, silence_findings
from app.llm import call, metrics
from app.reduce import diagnose
from app.swarm import run_swarm


BASELINE_PROMPT = Path(__file__).parent / "prompts" / "tail_baseline.txt"
SKEPTIC_PROMPT = Path(__file__).parent / "prompts" / "skeptic.txt"


async def _emit(emit: Callable, name: str, payload: dict) -> None:
    value = emit(name, payload)
    if inspect.isawaitable(value):
        await value


def _usage_snapshot() -> dict:
    roles = set(metrics.calls) | set(metrics.prompt_tokens) | set(metrics.completion_tokens)
    return {role: {"calls": metrics.calls.get(role, 0),
                   "prompt_tokens": metrics.prompt_tokens.get(role, 0),
                   "completion_tokens": metrics.completion_tokens.get(role, 0),
                   "latency_s": metrics.latency_s.get(role, 0.0)} for role in roles}


def _cost(tokens: dict) -> float:
    price_map = {
        "reduce": (settings.price_reduce_input, settings.price_reduce_output),
        "baseline": (settings.price_reduce_input, settings.price_reduce_output),
        "swarm": (settings.price_swarm_input, settings.price_swarm_output),
        "eyes": (settings.price_eyes_input, settings.price_eyes_output),
        "skeptic": (settings.price_skeptic_input, settings.price_skeptic_output),
    }
    total = 0.0
    for role, values in tokens.items():
        input_price, output_price = price_map.get(role, (0, 0))
        total += values["prompt_tokens"] * input_price / 1_000_000
        total += values["completion_tokens"] * output_price / 1_000_000
    return total


def _file_payload(bundle: Bundle) -> list[dict]:
    chunks = chunks_for_bundle(bundle)
    by_file = {}
    for chunk in chunks:
        by_file.setdefault(chunk.file, []).append(chunk)
    result = []
    for source in bundle.files:
        ids = by_file.get(source.path, [])
        est_tokens = sum(chunk.est_tokens for chunk in ids)
        identifier = ids[0].id if ids else source.path
        result.append({"id": identifier, "file": source.path, "kind": source.kind,
                       "rank": source.rank, "node": source.node, "est_tokens": est_tokens})
    return result


async def run(bundle: Bundle, options: Optional[dict], emit: Callable) -> dict:
    options = options or {}
    started = time.monotonic()
    usage_before = _usage_snapshot()
    incident_id = options.get("incident_id", bundle.root.name)
    run_id = options.get("run_id")
    chunks = chunks_for_bundle(bundle)
    await _emit(emit, "run_started", {"run_id": run_id, "incident_id": incident_id,
                                      "files": _file_payload(bundle), "total_tokens": bundle.est_tokens,
                                      "has_dashboard": any(item.kind == "dashboard" for item in bundle.files)})

    stop_metrics = asyncio.Event()

    async def metrics_loop():
        while not stop_metrics.is_set():
            try:
                await asyncio.wait_for(stop_metrics.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                usage = _usage_snapshot()
                delta = {role: {key: value.get(key, 0) - usage_before.get(role, {}).get(key, 0)
                                for key in ("calls", "prompt_tokens", "completion_tokens", "latency_s")}
                         for role, value in usage.items()}
                elapsed = max(time.monotonic() - started, .001)
                await _emit(emit, "metrics", {"elapsed_s": elapsed,
                    "tokens_processed": sum(role["prompt_tokens"] + role["completion_tokens"] for role in delta.values()),
                    "tokens_per_second": sum(role["prompt_tokens"] + role["completion_tokens"] for role in delta.values()) / elapsed,
                    "cost_usd": _cost(delta), "roles": delta})

    async def baseline_task():
        baseline_enabled = options.get("baseline", True)
        rank0 = bundle.by_path.get("logs/rank_000.log")
        if not baseline_enabled or rank0 is None:
            return {"text": "Baseline disabled or rank_000.log missing.", "category": "other"}
        prompt = BASELINE_PROMPT.read_text(encoding="utf-8")
        tail = "\n".join(rank0.lines[-200:])
        response, latency = await call("baseline", [{"role": "system", "content": prompt},
                                                       {"role": "user", "content": tail}],
                                       temperature=0, max_tokens=600)
        if not isinstance(response, dict):
            response = {"category": "other", "explanation": str(response), "next_steps": []}
        text = response.get("explanation", "")
        await _emit(emit, "baseline", {"text": text, "category": response.get("category", "other"),
                                       "node": response.get("node"), "gpu": response.get("gpu"),
                                       "latency_s": latency})
        return response

    async def eyes_task():
        visual = await read_dashboard(bundle, emit, enabled=options.get("use_eyes", True))
        await _emit(emit, "omni", visual)
        return visual

    metrics_task = asyncio.create_task(metrics_loop())
    try:
        baseline, visual, findings = await asyncio.gather(
            baseline_task(), eyes_task(),
            run_swarm(chunks, emit, bundle, options.get("swarm_concurrency")),
        )
        findings.extend(silence_findings(bundle))
        clusters = cluster_findings(findings, bundle)
        await _emit(emit, "clusters", {"n": len(clusters), "top": clusters[:12]})
        diagnosis = await diagnose(bundle, clusters, visual, emit, file_stats(bundle))
        result = diagnosis["result"]
        skeptic_messages = [
            {"role": "system", "content": SKEPTIC_PROMPT.read_text(encoding="utf-8")},
            {"role": "user", "content": str({"diagnosis": result, "clusters": clusters[:20]})},
        ]
        skeptic, _ = await call("skeptic", skeptic_messages, temperature=0, max_tokens=400)
        if not isinstance(skeptic, dict):
            skeptic = {"disagree": False, "alternative": "No dissent was returned.", "check": "Review source evidence."}
        await _emit(emit, "skeptic", skeptic)
        write_exports(options.get("run_dir", settings.runs_dir), result, diagnosis["narrative"])
        highlight_tiles = sorted({chunk.id for chunk in chunks
                                  if any(item.get("file") == chunk.file for item in result.get("evidence", []))})
        await _emit(emit, "verdict", {"diagnosis": result, "verification": result.get("verification"),
                                      "highlight_tiles": highlight_tiles})
        elapsed = time.monotonic() - started
        usage_after = _usage_snapshot()
        usage_delta = {}
        for role, current in usage_after.items():
            prior = usage_before.get(role, {})
            usage_delta[role] = {key: current.get(key, 0) - prior.get(key, 0)
                                 for key in ("calls", "prompt_tokens", "completion_tokens", "latency_s")}
        cost = _cost(usage_delta)
        await _emit(emit, "metrics", {"elapsed_s": elapsed,
            "tokens_processed": sum(item["prompt_tokens"] + item["completion_tokens"] for item in usage_delta.values()),
            "tokens_per_second": sum(item["prompt_tokens"] + item["completion_tokens"] for item in usage_delta.values()) / max(elapsed, .001),
            "cost_usd": cost, "roles": usage_delta})
        await _emit(emit, "done", {"elapsed_s": elapsed, "cost_usd": cost, "truth_match": None})
        return {"run_id": run_id, "incident_id": incident_id, "baseline": baseline, "omni": visual,
                "findings": findings, "clusters": clusters, "narrative": diagnosis["narrative"],
                "result": result, "skeptic": skeptic, "elapsed_s": elapsed, "cost_usd": cost, "tokens": usage_delta,
                "truth_match": None}
    except Exception as exc:
        await _emit(emit, "error", {"message": str(exc)})
        raise
    finally:
        stop_metrics.set()
        await metrics_task
