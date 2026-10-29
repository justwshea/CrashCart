"""Stream a final diagnosis, verify every citation, and enforce abstention."""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from app.ingest import Bundle, FileStats, file_stats
from app.llm import StreamDelta, call_stream
from app.verify import verify_quote


PROMPT = Path(__file__).parent / "prompts" / "reduce.txt"
DELIMITER = "---JSON---"
SCHEMA = {
    "verdict": "diagnosis | abstain",
    "root_cause": {"summary": "str", "category": "<enum>", "node": "node-XX|null",
                   "gpu": "int|null", "rank": "int|null", "first_seen_ts": "str"},
    "confidence": 0.0, "symptoms": ["str"],
    "timeline": [{"ts": "str", "event": "str", "evidence_ids": ["E1"]}],
    "evidence": [{"id": "E1", "file": "str", "line_start": 0, "line_end": 0,
                  "quote": "str", "observation_id": "O1|null", "supports": "str"}],
    "ruled_out": [{"hypothesis": "str", "why": "str", "evidence_ids": ["E2"]}],
    "runbook": [{"step": 1, "command": "str", "purpose": "str"}],
    "missing_data": "str|null",
}


async def _emit(emit: Optional[Callable], event: str, payload: dict) -> None:
    if emit is None:
        return
    value = emit(event, payload)
    if inspect.isawaitable(value):
        await value


def build_context(bundle: Bundle, clusters: list[dict], visual: Optional[dict] = None,
                  stats: Optional[list[FileStats]] = None) -> dict:
    stats = stats or file_stats(bundle)
    scheduler = next((item for item in bundle.files if item.kind == "scheduler"), None)
    config = next((item for item in bundle.files if item.kind == "config" and item.path.endswith((".yaml", ".yml"))), None)
    job = {"scheduler": "\n".join(scheduler.lines[-80:]) if scheduler else "",
           "config": "\n".join(config.lines[:80]) if config else ""}
    while len(json.dumps(job, ensure_ascii=False)) // 4 > 3000:
        if len(job["scheduler"]) >= len(job["config"]):
            job["scheduler"] = job["scheduler"][:len(job["scheduler"]) // 2]
        else:
            job["config"] = job["config"][:len(job["config"]) // 2]
    return {
        "JOB": job,
        "FILE_STATS": [asdict(item) for item in stats],
        "CLUSTERS": clusters,
        "VISUAL": visual or {"observations": [], "summary": ""},
    }


def _abstain(message: str) -> dict:
    return {"verdict": "abstain",
            "root_cause": {"summary": "Insufficient verified evidence.", "category": "other",
                           "node": None, "gpu": None, "rank": None, "first_seen_ts": ""},
            "confidence": 0.0, "symptoms": [], "timeline": [], "evidence": [], "ruled_out": [],
            "runbook": [], "missing_data": message,
            "verification": {"verified": 0, "total": 0, "failed": 0}}


async def _stream_once(context: dict, emit: Optional[Callable], failures: list[dict]) -> tuple[str, Optional[dict]]:
    system = PROMPT.read_text(encoding="utf-8") + "\nAppendix B JSON schema:\n" + json.dumps(SCHEMA)
    user_payload = dict(context)
    if failures:
        user_payload["verification_failures"] = failures
        user_payload["repair_instruction"] = "Fix or remove these evidence items; use only exemplar text from CLUSTERS."
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}]
    pending = ""
    json_buffer = ""
    narrative = []
    in_json = False
    async for delta in call_stream("reduce", messages, temperature=0, max_tokens=4000):
        if not isinstance(delta, StreamDelta):
            text = getattr(delta, "text", "")
        else:
            text = delta.text
        if not text:
            continue
        if in_json:
            json_buffer += text
            continue
        pending += text
        marker = pending.find(DELIMITER)
        if marker >= 0:
            before = pending[:marker]
            if before:
                narrative.append(before)
                await _emit(emit, "narrative_token", {"text": before})
            json_buffer = pending[marker + len(DELIMITER):]
            pending = ""
            in_json = True
        else:
            safe_len = max(0, len(pending) - len(DELIMITER) + 1)
            safe = pending[:safe_len]
            pending = pending[safe_len:]
            if safe:
                narrative.append(safe)
                await _emit(emit, "narrative_token", {"text": safe})
    if not in_json:
        tail = pending
        if tail:
            await _emit(emit, "narrative_token", {"text": tail})
        narrative_text = "".join(narrative) + tail
        return narrative_text.strip(), None
    narrative_text = "".join(narrative).strip()
    try:
        parsed = json.loads(json_buffer.strip())
    except (TypeError, ValueError):
        parsed = None
    return narrative_text, parsed if isinstance(parsed, dict) else None


def _verify_evidence(bundle: Bundle, result: dict, visual: dict) -> tuple[list[dict], list[dict]]:
    observations = {str(item.get("id")) for item in visual.get("observations", []) if isinstance(item, dict)}
    valid, failed = [], []
    for evidence in result.get("evidence", []):
        if not isinstance(evidence, dict):
            failed.append({"evidence": evidence, "reason": "not an evidence object"})
            continue
        file = evidence.get("file")
        if file == "dashboard.png":
            ok = evidence.get("observation_id") in observations
        else:
            try:
                ok = verify_quote(bundle, str(file), int(evidence.get("line_start")),
                                  int(evidence.get("line_end")), str(evidence.get("quote", "")))
            except (TypeError, ValueError):
                ok = False
        if ok:
            valid.append(evidence)
        else:
            failed.append({"evidence": evidence, "reason": "citation did not match source or observation"})
    return valid, failed


async def diagnose(bundle: Bundle, clusters: list[dict], visual: Optional[dict] = None,
                   emit: Optional[Callable] = None, stats: Optional[list[FileStats]] = None) -> dict:
    visual = visual or {"observations": [], "summary": ""}
    context = build_context(bundle, clusters, visual, stats)
    narrative, result = await _stream_once(context, emit, [])
    if result is None:
        result = _abstain("The model did not return valid JSON evidence.")

    valid, failed = _verify_evidence(bundle, result, visual)
    if failed and result.get("verdict") != "abstain":
        narrative_retry, retry_result = await _stream_once(context, emit, failed)
        if retry_result is not None:
            narrative = narrative_retry or narrative
            result = retry_result
            valid, failed = _verify_evidence(bundle, result, visual)
    result["evidence"] = valid
    total = len(valid) + len(failed)
    result["verification"] = {"verified": len(valid), "total": total, "failed": len(failed)}
    if not valid:
        result["verdict"] = "abstain"
        result["missing_data"] = result.get("missing_data") or "No root-cause evidence could be verified against the source."
    return {"narrative": narrative, "result": result}
