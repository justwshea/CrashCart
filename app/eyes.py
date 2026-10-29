"""Read a telemetry dashboard image with the configured vision role."""

from __future__ import annotations

import base64
import inspect
import json
from pathlib import Path
from typing import Callable, Optional

from app.ingest import Bundle
from app.llm import call


PROMPT = Path(__file__).parent / "prompts" / "dash.txt"


async def _emit(emit: Optional[Callable], event: str, payload: dict) -> None:
    if emit is None:
        return
    value = emit(event, payload)
    if inspect.isawaitable(value):
        await value


def _parse_observations(response) -> Optional[dict]:
    if isinstance(response, dict):
        parsed = response
    else:
        text = response if isinstance(response, str) else ""
        choices = getattr(response, "choices", None) or []
        if choices:
            text = getattr(getattr(choices[0], "message", None), "content", "") or ""
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("observations"), list):
        return None
    observations = []
    for index, item in enumerate(parsed["observations"], 1):
        if not isinstance(item, dict) or not isinstance(item.get("claim"), str):
            continue
        observations.append({
            "id": str(item.get("id") or f"O{index}"),
            "claim": item["claim"],
            "entity": item.get("entity"),
            "metric": str(item.get("metric") or "unknown"),
            "time_ref": item.get("time_ref"),
            "severity": max(1, min(5, int(item.get("severity", 1) or 1))),
        })
    return {"observations": observations, "summary": str(parsed.get("summary", ""))}


async def read_dashboard(bundle: Bundle, emit: Optional[Callable] = None,
                         enabled: bool = True) -> dict:
    if not enabled:
        return {"observations": [], "summary": "Dashboard analysis disabled."}
    source = next((item for item in bundle.files if item.kind == "dashboard"), None)
    if source is None:
        return {"observations": [], "summary": "No dashboard image was supplied."}
    try:
        image = (bundle.root / source.path).read_bytes()
        encoded = base64.b64encode(image).decode("ascii")
        messages = [
            {"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
            {"role": "user", "content": [
                {"type": "text", "text": "Read the visible GPU telemetry values and report concrete observations."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ]},
        ]
        response, _ = await call("eyes", messages, temperature=0, max_tokens=600)
        parsed = _parse_observations(response)
        if parsed is None:
            raise ValueError("vision response did not contain valid observations JSON")
        return parsed
    except Exception as exc:
        await _emit(emit, "warning", {"message": f"Dashboard analysis unavailable: {exc}"})
        return {"observations": [], "summary": "Dashboard analysis failed."}
