import asyncio
import io
import json
import zipfile
from dataclasses import replace
from datetime import datetime

import httpx

from app import events
from app.events import EventRecorder, read_events, replay
from app.limits import RunLimiter
from app import main
from app.config import settings
from tools.make_incident import make_incident


def test_event_record_and_timed_replay(tmp_path):
    async def scenario():
        recorder = EventRecorder("run-1", tmp_path)
        await recorder("run_started", {"id": "x"})
        await asyncio.sleep(.01)
        await recorder("done", {"ok": True})
        return [item async for item in replay("run-1", speed=100, runs_dir=tmp_path)]

    events = asyncio.run(scenario())
    assert [item["event"] for item in events] == ["run_started", "done"]
    assert read_events("run-1", tmp_path)[1]["data"] == {"ok": True}


def test_built_in_sample_replay_is_discoverable_and_streamable(monkeypatch, tmp_path):
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "xid79_demo.jsonl").write_text(
        json.dumps({"t": 0, "event": "run_started", "data": {"incident_id": "xid79_demo"}}) + "\n" +
        json.dumps({"t": .01, "event": "done", "data": {"elapsed_s": .01}}) + "\n"
    )
    monkeypatch.setattr(events, "SAMPLES_DIR", samples)
    monkeypatch.setattr(main, "SAMPLES_DIR", samples)

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/replays")).json() == [{"id": "xid79_demo", "title": "xid79 demo"}]
            response = await client.get("/api/replay/xid79_demo?speed=100")
            assert "event: run_started" in response.text and "event: done" in response.text
    asyncio.run(scenario())


def test_run_limiter_enforces_ip_hour_and_daily_caps():
    limiter = RunLimiter(daily_cap=2, ip_per_hour=1)
    now = datetime(2026, 10, 29, 10, 0)
    assert limiter.reserve("ip-a", now=now) == (True, None)
    assert limiter.reserve("ip-a", now=now)[0] is False
    assert limiter.reserve("ip-b", now=now) == (True, None)
    assert limiter.reserve("ip-c", now=now)[0] is False
    assert limiter.reserve("ip-a", now=datetime(2026, 10, 30, 0, 1)) == (True, None)


def test_api_pipeline_sse_source_truth_and_reports(monkeypatch, tmp_path):
    samples = tmp_path / "incidents"
    make_incident("xid79_demo", samples, 7)
    runs = tmp_path / "runs"
    monkeypatch.setattr(main, "settings", replace(settings, incidents_dir=str(samples), runs_dir=str(runs)))
    monkeypatch.setattr(main, "limiter", RunLimiter(daily_cap=20, ip_per_hour=20))

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/healthz")).json()["ok"] is True
            listed = (await client.get("/api/incidents")).json()
            assert listed[0]["id"] == "xid79_demo"
            assert listed[0]["has_truth"] is True
            created = await client.post("/api/run", json={"incident_id": "xid79_demo", "swarm_concurrency": 48})
            assert created.status_code == 200
            run_id = created.json()["run_id"]
            await main._tasks[run_id]
            report = (await client.get(f"/api/report/{run_id}")).json()
            assert report["result"]["root_cause"]["rank"] == 13
            assert report["truth_match"] is None
            events = main.read_events(run_id, runs)
            assert events[0]["event"] == "run_started"
            assert events[-1]["event"] == "done"
            assert any(event["event"] == "tile" for event in events)
            streamed = await client.get(f"/api/stream/{run_id}")
            assert "event: run_started" in streamed.text and "event: done" in streamed.text
            source = await client.get("/api/source", params={"incident": "xid79_demo",
                "file": "logs/dmesg_node-01.log", "line": 3, "ctx": 1})
            assert source.status_code == 200
            assert any(item["number"] == 3 and "Xid" in item["text"] for item in source.json()["lines"])
            assert (await client.get("/api/dashboard/xid79_demo")).headers["content-type"] == "image/png"
            truth = (await client.get("/api/truth/xid79_demo")).json()
            assert truth["rank"] == 13
            assert (await client.get(f"/api/report/{run_id}/postmortem.md")).status_code == 200
            traversal = await client.get("/api/source", params={"incident": "xid79_demo",
                "file": "../../AGENTS.md", "line": 1})
            assert traversal.status_code == 400
            return events

    events = asyncio.run(scenario())
    assert {item["event"] for item in events} >= {"run_started", "baseline", "tile", "metrics",
        "clusters", "omni", "narrative_token", "verdict", "done"}


def _zip_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def test_upload_validates_paths_size_and_truth(monkeypatch, tmp_path):
    samples = tmp_path / "incidents"
    samples.mkdir()
    monkeypatch.setattr(main, "settings", replace(settings, incidents_dir=str(samples), runs_dir=str(tmp_path / "runs")))

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            valid = _zip_bytes([("logs/scheduler.log", "2026-10-01T00:00:00Z state: failed\n")])
            response = await client.post("/api/upload", files={"file": ("bundle.zip", valid, "application/zip")})
            assert response.status_code == 200
            assert (samples / response.json()["incident_id"] / "logs/scheduler.log").is_file()
            traversal = _zip_bytes([("../escape.log", "nope")])
            assert (await client.post("/api/upload", files={"file": ("bad.zip", traversal)})).status_code == 400
            truth_archive = _zip_bytes([("truth.json", json.dumps({"secret": True}))])
            assert (await client.post("/api/upload", files={"file": ("truth.zip", truth_archive)})).status_code == 400
    asyncio.run(scenario())


def test_api_returns_429_with_replay_id(monkeypatch, tmp_path):
    samples = tmp_path / "incidents"
    make_incident("xid79_demo", samples, 7)
    monkeypatch.setattr(main, "settings", replace(settings, incidents_dir=str(samples), runs_dir=str(tmp_path / "runs")))
    monkeypatch.setattr(main, "limiter", RunLimiter(daily_cap=0, ip_per_hour=10))

    async def scenario():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/run", json={"incident_id": "xid79_demo"})
            assert response.status_code == 429
            assert response.json() == {"error": "limit", "replay_run_id": "xid79_demo"}
    asyncio.run(scenario())
