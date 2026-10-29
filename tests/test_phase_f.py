import asyncio
import base64

from app.eyes import read_dashboard
from app.ingest import load_bundle
from tools.make_incident import make_incident


def test_eyes_sends_image_and_returns_fixed_mock_observations(monkeypatch, tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    captured = {}

    async def fake_call(role, messages, **kwargs):
        captured.update(role=role, messages=messages, kwargs=kwargs)
        return {"observations": [{"id": "O1", "claim": "Snapshot available", "entity": None,
                                  "metric": "telemetry", "time_ref": "T_f + 120 s", "severity": 8}],
                "summary": "visible"}, 0.01

    monkeypatch.setattr("app.eyes.call", fake_call)
    result = asyncio.run(read_dashboard(bundle))
    assert captured["role"] == "eyes"
    assert captured["kwargs"] == {"temperature": 0, "max_tokens": 600}
    image_part = captured["messages"][1]["content"][1]
    data = image_part["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(data) == (bundle.root / "dashboard.png").read_bytes()
    assert result["observations"][0]["severity"] == 5
    assert result["summary"] == "visible"


def test_eyes_failure_warns_and_returns_empty_observations(monkeypatch, tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))

    async def fail(*args, **kwargs):
        raise RuntimeError("vision offline")

    monkeypatch.setattr("app.eyes.call", fail)
    events = []
    async def emit(name, payload):
        events.append((name, payload))

    result = asyncio.run(read_dashboard(bundle, emit))
    assert result["observations"] == []
    assert events[0][0] == "warning"
    assert "vision offline" in events[0][1]["message"]


def test_eyes_feature_flag_can_skip_image_call(monkeypatch, tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    monkeypatch.setattr("app.eyes.call", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    result = asyncio.run(read_dashboard(bundle, enabled=False))
    assert result["observations"] == []
