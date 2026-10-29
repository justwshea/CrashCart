import asyncio

from app.ingest import chunks_for_bundle, load_bundle
from app.swarm import run_swarm, scan
from tools.make_incident import make_incident


def test_mock_swarm_finds_grounded_xid_and_emits_tile_states(tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    chunk = next(item for item in chunks_for_bundle(bundle) if item.file == "logs/dmesg_node-01.log")
    events = []

    async def emit(name, payload):
        events.append((name, payload))

    findings = asyncio.run(scan(chunk, emit, bundle))
    assert any("Xid" in item["quote"] for item in findings)
    assert all(item["file"] == chunk.file for item in findings)
    tile_events = [payload for name, payload in events if name == "tile"]
    assert [item["state"] for item in tile_events] == ["running", "hit"]
    assert tile_events[-1]["hits"] >= 1


def test_malformed_json_retries_once_and_rejects_ungrounded_findings(monkeypatch, tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    chunk = next(item for item in chunks_for_bundle(bundle) if item.file == "logs/dmesg_node-01.log")
    responses = ["not json", {"findings": [
        {"line_start": 3, "line_end": 3, "quote": "invented evidence", "kind": "hardware",
         "severity": 5, "why": "not sourced"},
    ]}]
    calls = []

    async def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0), 0.001

    monkeypatch.setattr("app.swarm.call", fake_call)
    events = []
    async def emit(name, payload):
        events.append(payload)

    findings = asyncio.run(scan(chunk, emit, bundle))
    assert len(calls) == 2
    assert findings == []
    assert events[-1]["state"] == "clean"


def test_run_swarm_emits_events_for_each_chunk(tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    chunks = [item for item in chunks_for_bundle(bundle) if item.file.endswith("dmesg_node-01.log")]
    events = []

    async def emit(name, payload):
        events.append((name, payload))

    findings = asyncio.run(run_swarm(chunks, emit, bundle, concurrency=2))
    assert findings
    completed = [payload for name, payload in events if name == "tile" and payload["state"] != "running"]
    assert len(completed) == len(chunks)
