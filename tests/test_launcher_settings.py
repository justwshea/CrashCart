import asyncio
from dataclasses import replace

from app import swarm
from app.config import _bool_env, settings
from app.ingest import Chunk


def test_gate0_flags_parse_case_insensitively(monkeypatch):
    monkeypatch.setenv("LLM_JSON_MODE", "TRUE")
    monkeypatch.setenv("USE_EYES", "false")
    assert _bool_env("LLM_JSON_MODE") is True
    assert _bool_env("USE_EYES", True) is False


def test_verified_json_mode_is_passed_to_swarm_call(monkeypatch):
    monkeypatch.setattr(swarm, "settings", replace(settings, json_mode=True))
    captured = {}

    async def fake_call(role, messages, **kwargs):
        captured.update(role=role, kwargs=kwargs)
        return {"findings": []}, 0.001

    async def emit(*args):
        return None

    monkeypatch.setattr(swarm, "call", fake_call)
    chunk = Chunk("chunk-id", "logs/rank_000.log", "rank_log", 0, 0, 1, 1,
                  "L00001|ordinary training line", 6)
    asyncio.run(swarm.scan(chunk, emit))
    assert captured["role"] == "swarm"
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}
