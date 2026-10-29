import json
import os
import subprocess
import sys
from pathlib import Path

from tools.make_incident import make_incident
from tools.make_report import make_report, summarize


def test_mock_eval_harness_and_report_end_to_end(tmp_path):
    incidents = tmp_path / "incidents"
    make_incident("xid79_demo", incidents, 7)
    output = tmp_path / "eval" / "results.json"
    env = dict(os.environ, LLM_MODE="mock", INCIDENTS_DIR=str(incidents), RUNS_DIR=str(tmp_path / "runs"))
    subprocess.run([
        sys.executable, "tools/run_eval.py", "--incidents", "xid79_demo",
        "--systems", "crashcart,tail,no_eyes,full_ultra", "--full-limit", "1", "--out", str(output),
    ], check=True, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    results = json.loads(output.read_text())
    assert results["mode"] == "mock"
    assert {row["system"] for row in results["runs"]} == {"crashcart", "tail", "no_eyes", "full_ultra"}
    crashcart = next(row for row in results["runs"] if row["system"] == "crashcart")
    assert crashcart["category_correct"] is True
    assert crashcart["location_correct"] is True
    summary = summarize(results)
    assert len(summary) == 4
    markdown, chart = tmp_path / "eval" / "RESULTS.md", tmp_path / "eval" / "chart.png"
    make_report(output, markdown, chart)
    assert "Citation validity" in markdown.read_text()
    assert chart.is_file() and chart.stat().st_size > 1000
