"""Parallel, source-grounded log scanning workers."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

from app.config import settings
from app.ingest import Bundle, Chunk, SourceFile, parse_timestamp
from app.llm import call
from app.verify import verify_quote


PROMPT = Path(__file__).parent / "prompts" / "scan.txt"


async def _emit(emit: Callable, event: str, payload: dict) -> None:
    if emit is None:
        return
    value = emit(event, payload)
    if inspect.isawaitable(value):
        await value


def _response_text(response) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        if "findings" in response:
            return json.dumps(response)
        if "content" in response:
            return str(response["content"])
    choices = getattr(response, "choices", None) or []
    if choices:
        message = getattr(choices[0], "message", None)
        return str(getattr(message, "content", "") or "")
    return ""


def _parse_findings(response) -> Optional[list[dict]]:
    if isinstance(response, dict) and isinstance(response.get("findings"), list):
        return response["findings"]
    text = _response_text(response).strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("findings"), list):
        return None
    return parsed["findings"]


def _bundle_for_chunk(chunk: Chunk) -> Bundle:
    lines = [""] * chunk.line_end
    for item in chunk.text.splitlines():
        match = re.match(r"L(\d+)\|(.*)", item)
        if match:
            number = int(match.group(1))
            if 1 <= number <= len(lines):
                lines[number - 1] = match.group(2)
    source = SourceFile(chunk.file, chunk.kind, chunk.rank, chunk.node, lines,
                        [parse_timestamp(line) for line in lines])
    return Bundle(Path("."), [source], chunk.est_tokens)


async def scan(chunk: Chunk, emit: Callable, bundle: Optional[Bundle] = None) -> list[dict]:
    await _emit(emit, "tile", {"id": chunk.id, "state": "running", "hits": 0,
                                "tokens_in": chunk.est_tokens, "tokens_out": 0, "latency_s": 0.0})
    started = time.perf_counter()
    messages = [
        {"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
        {"role": "user", "content": chunk.text},
    ]
    findings: list[dict] = []
    state = "error"
    output_tokens = 0
    try:
        for attempt in range(2):
            request_options = {"temperature": 0, "max_tokens": 700}
            if settings.json_mode:
                request_options["response_format"] = {"type": "json_object"}
            response, _ = await call("swarm", messages, **request_options)
            output_tokens += len(_response_text(response)) // 4
            parsed = _parse_findings(response)
            if parsed is not None:
                source_bundle = bundle or _bundle_for_chunk(chunk)
                for finding in parsed[:12]:
                    if not isinstance(finding, dict):
                        continue
                    try:
                        start = int(finding.get("line_start"))
                        end = int(finding.get("line_end"))
                    except (TypeError, ValueError):
                        continue
                    quote = finding.get("quote")
                    if not isinstance(quote, str):
                        continue
                    if not verify_quote(source_bundle, chunk.file, start, end, quote):
                        continue
                    findings.append({**finding, "file": chunk.file, "line_start": start,
                                     "line_end": end, "quote": quote})
                state = "hit" if findings else "clean"
                break
            if attempt == 1:
                state = "error"
    except Exception:
        state = "error"

    elapsed = time.perf_counter() - started
    await _emit(emit, "tile", {"id": chunk.id, "state": state, "hits": len(findings),
                                "tokens_in": chunk.est_tokens, "tokens_out": output_tokens,
                                "latency_s": elapsed})
    return findings


async def run_swarm(chunks: list[Chunk], emit: Callable, bundle: Optional[Bundle] = None,
                    concurrency: Optional[int] = None) -> list[dict]:
    limiter = asyncio.Semaphore(max(1, concurrency or settings.concurrency))

    async def worker(chunk: Chunk) -> list[dict]:
        async with limiter:
            return await scan(chunk, emit, bundle)

    results = await asyncio.gather(*(worker(chunk) for chunk in chunks))
    return [finding for group in results for finding in group]
