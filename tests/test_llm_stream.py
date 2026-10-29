import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import llm
from app.config import settings


def chunk(content=None, reasoning=None, usage=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice] if content or reasoning else [], usage=usage)


class AsyncChunks:
    def __init__(self, values):
        self.values = values

    def __aiter__(self):
        async def iterate():
            for value in self.values:
                yield value
        return iterate()


def live_settings(monkeypatch):
    monkeypatch.setattr(llm, "settings", replace(settings, mode="live", api_key="test-key"))
    monkeypatch.setattr(llm, "_pause_until", 0.0)
    monkeypatch.setattr(llm, "_client_for_live", lambda: None)


def test_stream_429_sets_shared_pause_and_retries(monkeypatch):
    live_settings(monkeypatch)
    monkeypatch.setattr(llm, "_retry_delay", lambda exc, attempt: 0.01)
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                error = RuntimeError("rate limited")
                error.status_code = 429
                raise error
            return AsyncChunks([chunk("ok")])

    class Client:
        chat = SimpleNamespace(completions=Completions())

    monkeypatch.setattr(llm, "_client_for_live", lambda: Client())
    monkeypatch.setattr(llm, "_respect_pause", llm._respect_pause)

    async def run():
        return [item async for item in llm.call_stream("reduce", [{"content": "prompt"}])]

    result = asyncio.run(run())
    assert len(calls) == 2
    assert result[0].text == "ok"
    assert result[-1].usage["estimated"] is True
    assert llm._pause_until > 0


def test_stream_retries_transient_connection_error(monkeypatch):
    live_settings(monkeypatch)
    monkeypatch.setattr(llm, "_retry_delay", lambda exc, attempt: 0)
    attempts = 0

    class Completions:
        async def create(self, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary connection failure")
            return AsyncChunks([chunk("recovered")])

    monkeypatch.setattr(llm, "_client_for_live", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    ))
    result = asyncio.run(_collect_stream())
    assert attempts == 2
    assert "recovered" in "".join(item.text for item in result)


async def _collect_stream():
    return [item async for item in llm.call_stream("reduce", [{"content": "prompt"}])]


def test_stream_strips_think_and_ignores_reasoning_content(monkeypatch):
    live_settings(monkeypatch)

    class Completions:
        async def create(self, **kwargs):
            return AsyncChunks([
                chunk("Answer <thi", "private thought"),
                chunk("nk>hidden<", "more thought"),
                chunk("/think> confirmed."),
            ])

    monkeypatch.setattr(llm, "_client_for_live", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    ))
    result = asyncio.run(_collect_stream())
    assert "".join(item.text for item in result) == "Answer  confirmed."
    assert result[-1].usage["estimated"] is True
    assert result[-1].usage["completion_tokens"] == len("Answer  confirmed.") // 4


def test_shared_429_pause_is_waited_by_other_callers(monkeypatch):
    llm._pause_until = time.monotonic() + 5
    waited = []

    async def fake_sleep(delay):
        waited.append(delay)
        llm._pause_until = 0

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    asyncio.run(llm._respect_pause())
    assert waited and waited[0] > 0


def test_malformed_json_stream_is_passed_through_for_caller_retry(monkeypatch):
    live_settings(monkeypatch)

    class Completions:
        async def create(self, **kwargs):
            return AsyncChunks([chunk('{"findings": [}')])

    monkeypatch.setattr(llm, "_client_for_live", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    ))
    result = asyncio.run(_collect_stream())
    assert "".join(item.text for item in result) == '{"findings": [}'
    assert result[-1].usage["estimated"] is True
