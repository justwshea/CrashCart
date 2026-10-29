import asyncio
import json
from pathlib import Path

from app.cluster import cluster_findings
from app.export import render_exports, safe_runbook_steps
from app.ingest import load_bundle, silence_findings
from app.llm import StreamDelta
from app.reduce import diagnose
from tools.make_incident import make_incident


def demo_context(tmp_path):
    bundle = load_bundle(make_incident("xid79_demo", tmp_path, 7))
    source = bundle.by_path["logs/dmesg_node-01.log"]
    line_number = next(i for i, line in enumerate(source.lines, 1) if "Xid" in line)
    xid = {"file": source.path, "line_start": line_number, "line_end": line_number,
           "quote": source.lines[line_number - 1], "kind": "hardware", "severity": 5}
    clusters = cluster_findings([xid] + silence_findings(bundle), bundle)
    return bundle, clusters


def test_mock_reduce_verifies_demo_diagnosis_and_streams_narrative(tmp_path):
    bundle, clusters = demo_context(tmp_path)
    events = []

    async def emit(name, payload):
        events.append((name, payload))

    result = asyncio.run(diagnose(bundle, clusters, emit=emit))
    diagnosis = result["result"]
    assert diagnosis["verdict"] == "diagnosis"
    assert diagnosis["root_cause"]["category"] == "gpu_fell_off_bus"
    assert (diagnosis["root_cause"]["node"], diagnosis["root_cause"]["gpu"],
            diagnosis["root_cause"]["rank"]) == ("node-01", 5, 13)
    assert diagnosis["verification"] == {"verified": 2, "total": 2, "failed": 0}
    assert all(item["file"] != "truth.json" for item in diagnosis["evidence"])
    assert any(name == "narrative_token" for name, _ in events)


def test_pipeline_never_reads_truth_json(monkeypatch, tmp_path):
    from app.pipeline import run

    bundle, clusters = demo_context(tmp_path)
    original = Path.read_text

    def reject_truth(path, *args, **kwargs):
        if path.name == "truth.json":
            raise AssertionError("pipeline attempted to read truth.json")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_truth)

    async def scenario():
        async def emit(*args):
            return None
        return await run(bundle, {"run_dir": tmp_path / "run"}, emit)

    result = asyncio.run(scenario())
    assert result["result"]["root_cause"]["rank"] == 13


def test_corrupted_citation_is_retried_then_removed_and_forces_abstention(monkeypatch, tmp_path):
    bundle, clusters = demo_context(tmp_path)
    calls = []

    def response(quote):
        payload = {"verdict": "diagnosis", "root_cause": {"summary": "bad citation", "category": "other"},
                   "confidence": 0.5, "symptoms": [], "timeline": [],
                   "evidence": [{"id": "E1", "file": "logs/dmesg_node-01.log", "line_start": 3,
                                 "line_end": 3, "quote": quote, "observation_id": None, "supports": "bad"}],
                   "ruled_out": [], "runbook": [], "missing_data": None}
        return "Narrative [E1]\n---JSON---\n" + json.dumps(payload)

    async def fake_stream(role, messages, **kwargs):
        calls.append(messages)
        text = response("fabricated")
        for index in range(0, len(text), 19):
            yield StreamDelta(text=text[index:index + 19])
        yield StreamDelta(usage={"estimated": True})

    monkeypatch.setattr("app.reduce.call_stream", fake_stream)
    result = asyncio.run(diagnose(bundle, clusters))
    assert len(calls) == 2
    retry_payload = json.loads(calls[1][1]["content"])
    assert retry_payload["verification_failures"]
    assert result["result"]["verdict"] == "abstain"
    assert result["result"]["evidence"] == []
    assert result["result"]["verification"] == {"verified": 0, "total": 1, "failed": 1}


def test_malformed_reduce_json_abstains(tmp_path, monkeypatch):
    bundle, clusters = demo_context(tmp_path)

    async def malformed(*args, **kwargs):
        yield StreamDelta(text="Narrative without JSON")

    monkeypatch.setattr("app.reduce.call_stream", malformed)
    result = asyncio.run(diagnose(bundle, clusters))
    assert result["result"]["verdict"] == "abstain"
    assert result["result"]["verification"]["verified"] == 0


def test_export_adds_dry_run_banners_and_filters_destructive_commands():
    result = {"verdict": "diagnosis", "root_cause": {"summary": "test", "category": "other"},
              "evidence": [], "timeline": [], "ruled_out": [], "runbook": [
                  {"step": 1, "command": "nvidia-smi -q", "purpose": "Inspect GPUs"},
                  {"step": 2, "command": "rm -rf /", "purpose": "Unsafe"},
                  {"step": 3, "command": "reboot node-01", "purpose": "Unsafe without suggestion"},
                  {"step": 4, "command": "Suggestion: reboot node-01", "purpose": "Human review"},
                  {"step": 5, "command": "kill -9 1", "purpose": "Unsafe"},
              ]}
    safe = safe_runbook_steps(result)
    assert len(safe) == 2
    postmortem, runbook = render_exports(result)
    assert "nvidia-smi -q" in postmortem
    assert "rm -rf" not in runbook
    assert "kill -9" not in runbook
    assert runbook.count("DRY-RUN, review before running") == 2
