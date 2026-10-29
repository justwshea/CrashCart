# Build Status

Verification was performed in mock mode with Python 3.9. The test suite passes offline. No live API calls or API keys were used. Docker was available, but its network-disabled build could not install dependencies from pip and was stopped when pip attempted DNS.

| Phase | Status | Evidence | Known gaps |
|---|---|---|---|
| A. Scaffold and mock mode | done | `tests/test_phase_a.py`; `tests/test_llm_stream.py`; `LLM_MODE=mock pytest -q`; `python3 -c 'import app.llm'` | None found in the required mock acceptance checks. |
| B. Gate 0 smoke test | partial | `tools/smoke_test.py` exists and is live-mode-only. | Human must configure a key and run the Gate 0 smoke test; model IDs, JSON mode, reasoning controls, vision, and provider concurrency are not live-verified. |
| C. Synthetic incident generator | done | `tests/test_generator.py`; `LLM_MODE=mock python3 tools/make_incident.py --all --seed 7 --out incidents` completed; all bundle estimates are checked within the required range. | Generated incident bundles are ignored and are not committed. |
| D. Ingest, stats, clustering, verifier | done | `tests/test_phase_d.py`; `tests/test_phase_g.py`; `load_bundle` excludes `truth.json`. | None found in the required checks. |
| E. Swarm | done | `tests/test_phase_e.py` checks grounded Xid findings, retry, and tile events. | Live model behavior is unverified pending Phase B. |
| F. Eyes (Omni) | done | `tests/test_phase_f.py` checks fixed mock observations and warning fallback. | The provider's vision model ID and live image support are unverified pending Phase B. |
| G. Reduce, verification loop, exports | done | `tests/test_phase_g.py` checks the mock diagnosis, citation repair, abstention, and safe exports. | None found in the required mock checks. |
| H. Pipeline, API, SSE, replay, limits, upload | done | `tests/test_phase_h.py` checks API run/report/replay/source, source traversal, upload traversal, zip-bomb bounds, rate limits, and error emission. The test also asserts the full normal event sequence including `skeptic`. | `curl` mock run returned a verified rank-13 diagnosis. Docker runtime acceptance could not be performed because its image build was blocked at dependency installation. |
| I. Frontend | done | `tests/test_phase_i.py` checks the self-contained console and every Phase H SSE event registration and handler. | No browser screenshot test is present. |
| J. Evaluation harness | partial | `tests/test_phase_j.py` runs all four systems in mock mode and checks generated report output. `tools/make_report.py` reads only `eval/results.json`. | Live evaluation and measured swarm-versus-sequential timing have not been run. No `eval/results.json` exists. |
| K. Container, deploy, replays | partial | Docker is installed; `docker build --network=none -t crashcart:mock .` was attempted. | Build could not install pinned requirements without network. Human still needs live replay recording and deployment. |
| L. Stretch | partial | The Super skeptic response and `skeptic` SSE event are implemented and covered by the pipeline event test. | Tavily grounding and Serverless Job evaluation are not implemented. |
| M. Documentation | partial | `README.md` contains the requested sections, model table, Mermaid flow, placeholders, and “not yet measured” evaluation text. `FEEDBACK.md` contains the requested feedback headings. `LICENSE` already exists. | Console screenshot has not been captured. README has no measured results because `eval/results.json` is absent. |

## Required checks

1. **Truth isolation:** The production pipeline does not read `truth.json`. `app/ingest.py` excludes it, and `tests/test_phase_g.py::test_pipeline_never_reads_truth_json` fails if the pipeline attempts to read it. Truth reads are confined to the evaluation harness and the sample truth-badge route.
2. **Phase H events:** The normal pipeline emits `run_started`, `baseline`, `tile`, `metrics`, `clusters`, `omni`, `narrative_token`, `verdict`, `skeptic`, and `done`. Eyes failure emits `warning`; pipeline failure emits `error`. `tests/test_phase_h.py` covers normal and error emission, `tests/test_phase_f.py` covers warning emission, and `tests/test_phase_i.py::test_console_registers_every_phase_h_event` checks that the frontend registers and handles every event type.
3. **Path traversal and zip bombs:** `/api/source` resolves files within the incident directory and rejects traversal; upload rejects absolute/parent paths and checks uncompressed archive size. `tests/test_phase_h.py` covers source traversal, upload traversal, and `test_upload_rejects_zip_bomb_by_uncompressed_size`.
4. **Incident generator:** The root evidence markers in `tests/test_generator.py` match the Phase C table's evidence files, including dashboard-only evidence for dashboard faults. The test checks each `truth.json` line against its named file and every bundle estimate against the required range. `test_xid_watchdog_tail_follows_six_hundred_second_pattern` verifies the healthy-rank watchdog at T_f + 600 seconds, the timeout text, traceback count, and rank 13's early end with no watchdog.
5. **Dashboard generator:** `tools/make_dashboard.py` samples at T_f + 120 seconds. The chart and heatmap data both receive each fault's effects; `test_dashboard_applies_faults_at_t_f_plus_120_seconds` checks key examples and `test_every_fault_changes_a_chart_series_and_heatmap_sample` checks every generated fault.
6. **LLM and configuration:** `app/llm.py` retries transient connection and 429 errors, sets and observes the shared 429 pause, removes `<think>` blocks across stream chunks, ignores `reasoning_content`, and estimates usage when stream usage is absent. `app/config.py` clamps `CONCURRENCY` to at least one. Coverage is in `tests/test_llm_stream.py` and `tests/test_phase_a.py`.
7. **README measurements:** `eval/results.json` does not exist. `README.md` says “not yet measured” for evaluation figures and does not include invented results. `tools/make_report.py` reads its results input from `eval/results.json` and writes the generated evaluation outputs.

## Commands and mock API result

- `LLM_MODE=mock pytest -q` — 42 tests pass (Python 3.9).
- `LLM_MODE=mock python3 tools/make_incident.py --all --seed 7 --out incidents` — generated all sample incidents.
- `LLM_MODE=mock INCIDENTS_DIR=./incidents RUNS_DIR=./runs uvicorn app.main:app --host 127.0.0.1 --port 18080` — started the local mock app.
- `curl` requests to `/api/run`, `/api/stream/{id}`, `/api/replay/{id}`, `/api/report/{id}`, and `/api/report/{id}/postmortem.md` completed. The demo report was `gpu_fell_off_bus`, rank 13, with 2/2 citations verified.
- `docker build --network=none -t crashcart:mock .` — attempted; dependency installation could not complete offline and the build was stopped.
