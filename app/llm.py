"""OpenAI-compatible LLM client with bounded concurrency and deterministic mock mode."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.config import settings

try:
    from openai import AsyncOpenAI
    from openai import APIStatusError
except ImportError:  # Keeps mock mode importable in a minimal environment.
    AsyncOpenAI = None  # type: ignore[assignment,misc]
    APIStatusError = Exception  # type: ignore[assignment,misc]


@dataclass
class Metrics:
    calls: dict[str, int] = field(default_factory=dict)
    prompt_tokens: dict[str, int] = field(default_factory=dict)
    completion_tokens: dict[str, int] = field(default_factory=dict)
    latency_s: dict[str, float] = field(default_factory=dict)

    def record(self, role: str, prompt: int, completion: int, latency: float) -> None:
        self.calls[role] = self.calls.get(role, 0) + 1
        self.prompt_tokens[role] = self.prompt_tokens.get(role, 0) + prompt
        self.completion_tokens[role] = self.completion_tokens.get(role, 0) + completion
        self.latency_s[role] = self.latency_s.get(role, 0.0) + latency


@dataclass
class StreamDelta:
    text: str = ""
    usage: Any | None = None


metrics = Metrics()
_semaphore = asyncio.Semaphore(max(1, settings.concurrency))
_pause_lock = asyncio.Lock()
_pause_until = 0.0
_client = None


def _client_for_live():
    global _client
    if _client is None:
        if AsyncOpenAI is None:
            raise RuntimeError("openai package is required for live mode")
        _client = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
    return _client


def _usage(response: Any, prompt_fallback: int = 0, completion_fallback: int = 0) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return prompt_fallback, completion_fallback
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)


def _estimate_messages(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages) // 4


async def _respect_pause() -> None:
    while True:
        async with _pause_lock:
            delay = _pause_until - time.monotonic()
        if delay <= 0:
            return
        await asyncio.sleep(delay)


def _retry_delay(exc: Exception, attempt: int) -> float:
    delay = min(20.0, 2**attempt) + random.random()
    if getattr(exc, "status_code", None) == 429:
        headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        try:
            delay = max(delay, float(retry_after))
        except (TypeError, ValueError):
            pass
    return delay


def _retryable(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) in {429, 500, 502, 503, 504} or isinstance(
        exc, (TimeoutError, ConnectionError, OSError)
    ) or type(exc).__name__ in {"APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout"}


class _ThinkStripper:
    """Filter think tags across arbitrary streaming chunk boundaries."""

    def __init__(self) -> None:
        self.buffer = ""
        self.in_think = False

    def feed(self, text: str, final: bool = False) -> str:
        self.buffer += text
        output = []
        while self.buffer:
            marker = "</think>" if self.in_think else "<think>"
            index = self.buffer.lower().find(marker)
            if index >= 0:
                if not self.in_think:
                    output.append(self.buffer[:index])
                self.buffer = self.buffer[index + len(marker):]
                self.in_think = not self.in_think
                continue
            if final:
                if not self.in_think:
                    output.append(self.buffer)
                self.buffer = ""
                break
            hold = min(len(marker) - 1, len(self.buffer))
            while hold > 0 and not self.buffer.lower().endswith(marker[:hold]):
                hold -= 1
            safe_end = len(self.buffer) - hold
            if not self.in_think and safe_end:
                output.append(self.buffer[:safe_end])
            self.buffer = self.buffer[safe_end:]
            break
        return "".join(output)


async def _set_pause(delay: float) -> None:
    global _pause_until
    async with _pause_lock:
        _pause_until = max(_pause_until, time.monotonic() + delay)


_FINDING_RE = re.compile(
    r"Xid|NCCL WARN|Watchdog|OutOfMemory|nan|Traceback|ECC|Link down|not responding|"
    r"Killed process|SIGTERM|illegal memory|version", re.IGNORECASE
)


def _mock_response(role: str, messages: list[dict]) -> dict:
    source = "\n".join(str(m.get("content", "")) for m in messages)
    if role == "swarm":
        findings = []
        for line in source.splitlines():
            match = re.match(r"L(\d+)\|(.*)", line)
            if not match or not _FINDING_RE.search(match.group(2)):
                continue
            number, quote = int(match.group(1)), match.group(2)[:160]
            findings.append({"line_start": number, "line_end": number, "quote": quote,
                             "kind": "hardware" if re.search(r"Xid|ECC|Link down", quote, re.I) else "error",
                             "severity": 5 if re.search(r"Xid|OutOfMemory|illegal memory", quote, re.I) else 3,
                             "why": "Potentially relevant anomaly in log evidence."})
            if len(findings) == 12:
                break
        return {"findings": findings}
    if role == "eyes":
        return {"observations": [{"id": "O1", "claim": "GPU telemetry snapshot is available for comparison.",
                                  "entity": None, "metric": "telemetry", "time_ref": "T_f + 120 s",
                                  "severity": 1}],
                "summary": "Mock mode reports a fixed synthetic dashboard observation."}
    if role == "reduce":
        user_payload = {}
        for message in messages:
            if message.get("role") == "user":
                try:
                    user_payload = json.loads(message.get("content", "{}"))
                except (TypeError, ValueError):
                    user_payload = {}
        clusters = user_payload.get("CLUSTERS", [])
        xid = next((cluster for cluster in clusters
                    if any("Xid" in str(example.get("quote", "")) and "79" in str(example.get("quote", ""))
                           for example in cluster.get("exemplars", []))), None)
        silence = next((cluster for cluster in clusters if cluster.get("kind") == "silence"
                        and any("rank_013.log" in str(example.get("file", ""))
                                for example in cluster.get("exemplars", []))), None)
        if xid:
            root_example = next(example for example in xid["exemplars"] if "Xid" in example["quote"] and "79" in example["quote"])
            evidence = [{"id": "E1", "file": root_example["file"], "line_start": root_example["line_start"],
                         "line_end": root_example["line_end"], "quote": root_example["quote"],
                         "observation_id": None, "supports": "GPU 5 on node-01 suffered Xid 79."}]
            ids = ["E1"]
            if silence:
                quiet_example = next(example for example in silence["exemplars"] if "rank_013.log" in example["file"])
                evidence.append({"id": "E2", "file": quiet_example["file"], "line_start": quiet_example["line_start"],
                                 "line_end": quiet_example["line_end"], "quote": quiet_example["quote"],
                                 "observation_id": None, "supports": "Rank 13 stopped logging before peers timed out."})
                ids.append("E2")
            first_seen = xid.get("first_ts") or ""
            result = {
                "verdict": "diagnosis", "root_cause": {"summary": "GPU 5 on node-01 fell off the bus (Xid 79).",
                    "category": "gpu_fell_off_bus", "node": "node-01", "gpu": 5, "rank": 13,
                    "first_seen_ts": first_seen},
                "confidence": 0.92,
                "symptoms": ["Peer ranks timed out in NCCL collectives."],
                "timeline": [{"ts": first_seen, "event": "Xid 79 reported on node-01 GPU 5.", "evidence_ids": ["E1"]}],
                "evidence": evidence, "ruled_out": [],
                "runbook": [{"step": 1, "command": "echo DRY RUN: inspect node-01 GPU 5", "purpose": "Review device diagnostics."}],
                "missing_data": None,
            }
            return {"narrative": "The earliest localized hardware evidence is Xid 79 on node-01 GPU 5 [E1]. Rank 13 then stops logging while peers time out, consistent with a GPU bus failure [E2].",
                    "result": result}
        result = {
            "verdict": "abstain",
            "root_cause": {"summary": "Mock mode found no grounded root cause.", "category": "other", "node": None, "gpu": None, "rank": None, "first_seen_ts": ""},
            "confidence": 0.0, "symptoms": [], "timeline": [], "evidence": [], "ruled_out": [],
            "runbook": [], "missing_data": "No verified evidence was supplied to the mock responder.",
        }
        return {"narrative": "Mock mode could not identify a grounded root cause.", "result": result}
    if role == "skeptic":
        return {"disagree": False, "alternative": "No independent alternative in mock mode.", "check": "Review source evidence."}
    return {"text": "mock response"}


async def call(role: str, messages: list[dict], **kw: Any) -> tuple[Any, float]:
    """Call a configured role and return (response, measured latency seconds)."""
    if role not in settings.models:
        raise ValueError(f"unknown LLM role: {role}")
    async with _semaphore:
        start = time.perf_counter()
        if settings.mode == "mock":
            await asyncio.sleep(0.2 + random.Random(role + str(len(messages))).random() * 0.6)
            response = _mock_response(role, messages)
            prompt_tokens = _estimate_messages(messages)
            completion_tokens = len(json.dumps(response)) // 4
            elapsed = time.perf_counter() - start
            metrics.record(role, prompt_tokens, completion_tokens, elapsed)
            return response, elapsed
        if not settings.api_key:
            raise RuntimeError("Set LLM_API_KEY or NEBIUS_API_KEY when LLM_MODE=live")
        client = _client_for_live()
        request = {"model": settings.models[role], "messages": messages, **kw}
        extra = settings.extra_body(role)
        if extra:
            request["extra_body"] = extra
        for attempt in range(6):
            await _respect_pause()
            try:
                response = await client.chat.completions.create(**request)
                prompt_tokens, completion_tokens = _usage(response, _estimate_messages(messages))
                elapsed = time.perf_counter() - start
                metrics.record(role, prompt_tokens, completion_tokens, elapsed)
                return response, elapsed
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if not _retryable(exc) or attempt == 5:
                    raise
                delay = _retry_delay(exc, attempt)
                if status == 429:
                    await _set_pause(delay)
                else:
                    await asyncio.sleep(delay)
        raise RuntimeError("unreachable retry state")


async def call_stream(role: str, messages: list[dict], **kw: Any) -> AsyncIterator[StreamDelta]:
    """Stream text deltas; the final item carries usage when the API provides it."""
    if settings.mode == "mock":
        response, _ = await call(role, messages, **kw)
        if isinstance(response, dict) and "narrative" in response:
            text = response["narrative"] + "\n---JSON---\n" + json.dumps(response["result"])
        else:
            text = json.dumps(response)
        for i in range(0, len(text), 48):
            yield StreamDelta(text=text[i:i + 48])
        yield StreamDelta(usage={"estimated": True})
        return
    if role not in settings.models:
        raise ValueError(f"unknown LLM role: {role}")
    if not settings.api_key:
        raise RuntimeError("Set LLM_API_KEY or NEBIUS_API_KEY when LLM_MODE=live")
    client = _client_for_live()
    request = {"model": settings.models[role], "messages": messages, "stream": True,
               "stream_options": {"include_usage": True}, **kw}
    extra = settings.extra_body(role)
    if extra:
        request["extra_body"] = extra
    async with _semaphore:
        start = time.perf_counter()
        prompt_tokens = _estimate_messages(messages)
        for attempt in range(6):
            await _respect_pause()
            completion_chars = 0
            actual_usage = None
            emitted_text = False
            stripper = _ThinkStripper()
            try:
                stream = await client.chat.completions.create(**request)
                async for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage is not None:
                        actual_usage = usage
                    for choice in getattr(chunk, "choices", []) or []:
                        delta = getattr(choice, "delta", None)
                        content = getattr(delta, "content", None) if delta else None
                        # reasoning_content is intentionally ignored.
                        if content:
                            clean = stripper.feed(content)
                            if clean:
                                emitted_text = True
                                completion_chars += len(clean)
                                yield StreamDelta(text=clean)
                tail = stripper.feed("", final=True)
                if tail:
                    emitted_text = True
                    completion_chars += len(tail)
                    yield StreamDelta(text=tail)
                if actual_usage is None:
                    prompt_tokens_out = prompt_tokens
                    completion_tokens_out = completion_chars // 4
                    final_usage = {"prompt_tokens": prompt_tokens_out,
                                   "completion_tokens": completion_tokens_out, "estimated": True}
                else:
                    prompt_tokens_out, completion_tokens_out = _usage(actual_usage, prompt_tokens, completion_chars // 4)
                    final_usage = actual_usage
                metrics.record(role, prompt_tokens_out, completion_tokens_out, time.perf_counter() - start)
                yield StreamDelta(usage=final_usage)
                return
            except Exception as exc:
                if not _retryable(exc) or attempt == 5 or emitted_text:
                    raise
                delay = _retry_delay(exc, attempt)
                if getattr(exc, "status_code", None) == 429:
                    await _set_pause(delay)
                else:
                    await asyncio.sleep(delay)
