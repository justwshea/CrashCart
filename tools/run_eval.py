"""Offline-capable evaluation harness for CrashCart and baseline systems."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.ingest import load_bundle
from app.llm import call, metrics
from app.pipeline import _cost, run as run_pipeline
from app.swarm import run_swarm
from app.ingest import chunks_for_bundle


BASELINE_PROMPT = Path(__file__).resolve().parents[1] / "app" / "prompts" / "tail_baseline.txt"


def _snapshot():
    return {role: {"calls": metrics.calls.get(role, 0), "prompt_tokens": metrics.prompt_tokens.get(role, 0),
                   "completion_tokens": metrics.completion_tokens.get(role, 0), "latency_s": metrics.latency_s.get(role, 0.0)}
            for role in set(metrics.calls) | set(metrics.prompt_tokens) | set(metrics.completion_tokens)}


def _delta(before, after):
    return {role: {key: current.get(key, 0) - before.get(role, {}).get(key, 0)
                   for key in ("calls", "prompt_tokens", "completion_tokens", "latency_s")}
            for role, current in after.items()}


async def _quiet_emit(*args, **kwargs):
    return None


async def _tail(bundle):
    rank0 = bundle.by_path.get("logs/rank_000.log")
    lines = rank0.lines[-200:] if rank0 else []
    response, _ = await call("baseline", [
        {"role": "system", "content": BASELINE_PROMPT.read_text(encoding="utf-8")},
        {"role": "user", "content": "\n".join(lines)},
    ], temperature=0, max_tokens=600)
    return response if isinstance(response, dict) else {"category": "other"}


async def _full_ultra(bundle):
    if bundle.est_tokens > 1_000_000:
        return None
    pieces = []
    for source in bundle.files:
        if source.kind != "dashboard":
            pieces.append(f"\n### {source.path}\n" + "\n".join(source.lines))
    response, _ = await call("reduce", [{"role": "user", "content": "Diagnose this incident.\n" + "\n".join(pieces)}],
                             temperature=0, max_tokens=4000)
    if isinstance(response, dict) and isinstance(response.get("result"), dict):
        return response["result"]
    return {"verdict": "abstain", "root_cause": {"category": "other"}, "evidence": []}


async def _evaluate(system, incident_id, bundle, truth, out_dir):
    before = _snapshot()
    started = time.perf_counter()
    if system == "tail":
        result = await _tail(bundle)
    elif system == "full_ultra":
        result = await _full_ultra(bundle)
        if result is None:
            return {"incident_id": incident_id, "system": system, "skipped": "bundle exceeds 1.0M estimated tokens"}
    else:
        options = {"incident_id": incident_id, "run_id": f"eval-{system}-{incident_id}",
                   "run_dir": out_dir / "runs" / f"{system}-{incident_id}",
                   "use_eyes": system != "no_eyes"}
        report = await run_pipeline(bundle, options, _quiet_emit)
        result = report.get("result", {})
    elapsed = time.perf_counter() - started
    usage = _delta(before, _snapshot())
    root = result.get("root_cause", {}) if isinstance(result, dict) else {}
    category = root.get("category") or result.get("category", "other")
    node, gpu = root.get("node", result.get("node")), root.get("gpu", result.get("gpu"))
    evidence = result.get("evidence", [])
    verification = result.get("verification", {})
    verified = int(verification.get("verified", len(evidence)))
    total = int(verification.get("total", len(evidence)))
    return {
        "incident_id": incident_id, "system": system,
        "category_correct": category == truth.get("category"),
        "location_correct": (node == truth.get("node") and gpu == truth.get("gpu"))
                            if truth.get("node") is not None and truth.get("gpu") is not None else None,
        "category": category, "node": node, "gpu": gpu,
        "elapsed_s": elapsed, "tokens_by_role": usage, "estimated_cost_usd": _cost(usage),
        "verified_citations": verified, "total_citations": total,
        "verdict": result.get("verdict", "diagnosis" if system == "tail" else "abstain"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--incidents", default="all")
    parser.add_argument("--systems", default="crashcart,tail,no_eyes,full_ultra")
    parser.add_argument("--full-limit", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path("eval/results.json"))
    parser.add_argument("--sequential-compare", type=int, default=0)
    args = parser.parse_args()
    incident_root = Path(settings.incidents_dir)
    if args.incidents == "all":
        incident_ids = [path.name for path in sorted(incident_root.iterdir()) if path.is_dir() and (path / "truth.json").is_file()]
    else:
        incident_ids = [value.strip() for value in args.incidents.split(",") if value.strip()]
    systems = [value.strip() for value in args.systems.split(",") if value.strip()]
    allowed = {"crashcart", "tail", "no_eyes", "full_ultra"}
    if not set(systems) <= allowed:
        parser.error("unknown system name")
    args.out.parent.mkdir(parents=True, exist_ok=True)

    async def execute():
        runs = []
        for index, incident_id in enumerate(incident_ids):
            folder = incident_root / incident_id
            truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
            bundle = load_bundle(folder)
            for system in systems:
                if system == "full_ultra" and index >= max(0, args.full_limit):
                    continue
                runs.append(await _evaluate(system, incident_id, bundle, truth, args.out.parent))
        return runs

    runs = asyncio.run(execute())
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": settings.mode,
              "incidents": incident_ids, "systems": systems, "runs": runs}
    if args.sequential_compare:
        async def compare():
            rows = []
            for incident_id in incident_ids[:max(1, args.sequential_compare)]:
                bundle = load_bundle(incident_root / incident_id)
                chunks = chunks_for_bundle(bundle)
                async def quiet(*args, **kwargs):
                    return None
                timings = {}
                for label, concurrency in (("sequential", 1), ("parallel", settings.concurrency)):
                    started = time.perf_counter()
                    await run_swarm(chunks, quiet, bundle, concurrency)
                    timings[label + "_s"] = time.perf_counter() - started
                rows.append({"incident_id": incident_id, **timings})
            return rows
        result["sequential_compare"] = asyncio.run(compare())
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(runs)} evaluation records to {args.out}")


if __name__ == "__main__":
    main()
