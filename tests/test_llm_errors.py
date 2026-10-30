import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import llm
from app.config import settings


class RemoteFailure(RuntimeError):
    def __init__(self, status, message="provider error"):
        super().__init__(message)
        self.status_code = status


def live(monkeypatch, key="test-key"):
    monkeypatch.setattr(llm, "settings", replace(settings, mode="live", api_key=key))
    monkeypatch.setattr(llm, "_pause_until", 0.0)


def test_missing_api_key_has_one_actionable_message(monkeypatch):
    live(monkeypatch, key="")
    with pytest.raises(RuntimeError, match=r"Missing Token Factory API key.*\.env"):
        asyncio.run(llm.call("reduce", [{"role": "user", "content": "test"}]))


@pytest.mark.parametrize(("status", "detail", "expected"), [
    (401, "unauthorized", r"rejected the API key \(401\)"),
    (402, "payment required", "no credits or an exhausted quota"),
    (400, "insufficient credit balance", "no credits or an exhausted quota"),
])
def test_auth_and_credit_errors_are_friendly(monkeypatch, status, detail, expected):
    live(monkeypatch)

    class Completions:
        async def create(self, **kwargs):
            raise RemoteFailure(status, detail)

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(llm, "_client_for_live", lambda: client)
    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(llm.call("reduce", [{"role": "user", "content": "test"}]))


def test_rate_limit_after_all_retries_has_friendly_message(monkeypatch):
    live(monkeypatch)
    monkeypatch.setattr(llm, "_retry_delay", lambda exc, attempt: 0)
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(1)
            raise RemoteFailure(429, "rate limited")

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(llm, "_client_for_live", lambda: client)
    with pytest.raises(RuntimeError, match="rate limit persisted after retries"):
        asyncio.run(llm.call("reduce", [{"role": "user", "content": "test"}]))
    assert len(calls) == 6
