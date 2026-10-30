# Demo video plan

Target runtime: **2:20**. The first fifteen seconds are reserved for a real live run with the real stopwatch visible. Every incident shown is synthetic. Do not use a recorded replay for B1; if a genuine live run cannot be recorded, stop and resolve that before production.

| ID | start | end | duration | what is on screen | what the human does | voiceover file | on-screen caption |
|---|---:|---:|---:|---|---|---|---|
| B1 | 0:00 | 0:15 | 15 s | Split screen: wrong tail answer, then the live console as tiles fill, one turns red, verdict identifies node-01 GPU 5, verified badge and real stopwatch visible. | Select `xid79_demo` and click **Run live** before recording; keep stopwatch in frame. | `voiceover/B1.txt` | Synthetic incident · Live run · Tail answer: “raise the timeout” · Root cause: node-01 GPU 5 · Verified |
| B2 | 0:15 | 0:35 | 20 s | Rank log tail beside the kernel log Xid line. | Open the cited source line; frame both symptom and earlier cause. | `voiceover/B2.txt` | Synthetic incident · Loud symptom / quiet cause |
| B3 | 0:35 | 1:05 | 30 s | Swarm tiles, dashboard reader, final reasoning, model HUD with tokens/sec and estimated cost. | Show parallel worker activity and the HUD; identify the dashboard model ID as unverified. | `voiceover/B3.txt` | Parallel calls · Swarm · Dashboard reader · Final reasoning · Estimated cost |
| B4 | 1:05 | 1:35 | 30 s | Evaluation chart and results table from `eval/chart.png` and `eval/RESULTS.md`. | Show only generated results. If files are absent, use a slate reading “not yet measured.” | `voiceover/B4.txt` | Measured evaluation · [MEASURE] |
| B5 | 1:35 | 1:55 | 20 s | Click an evidence chip to open source; show verified citations, dry-run runbook, postmortem download, and upload control. | Demonstrate each feature without exposing secrets or unrelated browser chrome. | `voiceover/B5.txt` | Verified source · Dry-run only · Upload a bundle |
| B6 | 1:55 | 2:20 | 25 s | Clean product close with project name, stack, repo and demo URL placeholders. | Hold the closing frame. Mention Serverless only if deployment is verified. | `voiceover/B6.txt` | Token Factory + Nemotron · Repo: [REPO_URL] · Demo: [DEMO_URL] |

## Optional beats

B3 may be shortened to a single parallel-swarm shot; B4 may be omitted while evaluation is unmeasured; B5 may be shortened to evidence and runbook. Keep B1 and its genuine live stopwatch shot.

## Alternative cut: about 1:30

| Beat | Timing | Keep |
|---|---:|---|
| B1 | 0:00–0:15 | Full live hook |
| B2 | 0:15–0:27 | Symptom and Xid source |
| B3 | 0:27–0:45 | Parallel workflow and HUD |
| B4 | 0:45–1:00 | Results if measured, otherwise one “not yet measured” card |
| B5 | 1:00–1:15 | Evidence chip and dry-run runbook |
| B6 | 1:15–1:30 | Stack and URL placeholders |

For the short cut, edit voiceover to fit the shortened beat durations and re-run `python video/tools/check_timing.py`.
