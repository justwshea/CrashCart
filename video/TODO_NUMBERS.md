# Numbers and factual placeholders

No `eval/results.json` or `eval/RESULTS.md` exists in this checkout. No live recording was made. Performance and usage numbers below remain placeholders.

| Number or claim | Where it appears | Source (file and field) | Status |
|---|---|---|---|
| 2:20 total, beat timings 15/20/30/30/20/25 seconds | Plan, narration targets, timeline | `video/timeline.csv`, `duration_s`; production edit targets, not product results | Planning values |
| node one, GPU five | B1 narration/caption | `incidents/xid79_demo/truth.json`: `node`, `gpu`; corroborate with the recorded live run source and verified verdict before publishing | Verify in live shot |
| Tokens per second and estimated cost | B3 narration/caption | Live run report / SSE `metrics`: `tokens_per_second`, `cost_usd` | Capture actual HUD; no values narrated |
| Comparison, dashboard-only and reader on/off results | B4 narration/caption | `eval/results.json`: `runs`, `system`, `category_correct`, `location_correct`, `elapsed_s`, `estimated_cost_usd`, `verified_citations`, `total_citations`; reader comparison only if represented in the generated result | **[MEASURE]** |
| No deployment claim | B6 narration | `README.md`: Nebius Serverless Endpoints not deployed | Confirm before any changed claim |
| “Nemotron Lightning” and “Nemotron Ultra” | B3 narration/caption | `app/config.py`: `model_swarm`, `model_reduce` | Configured defaults; live model availability unverified |
| Incident is synthetic | B1/B2 captions and narration | `video/PLAN.md`, `README.md` synthetic data disclosure; `truth.json` is planted fixture metadata | Include disclosure |
| Real stopwatch in first 15 seconds | B1 plan | Actual live screen recording | **[MEASURE]** real run duration from video; do not substitute a replay |

Replace every `[MEASURE]` in the narration/captions and this list only from the named generated results file or the actual captured live run. If evaluation is not done, leave the on-screen slate “not yet measured.”
