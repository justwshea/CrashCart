# CrashCart demo video kit

The planned cut is 2:20 and must remain under 3:00. The incident data is synthetic. The first fifteen seconds must show a genuine live run and the actual stopwatch; never use a replay for that hero shot. No music, logos, browser bookmarks, third-party marks, API keys, or secrets should appear. Do not claim a public deployment unless it has been verified.

## Steps

1. Run `./run.sh` and confirm the app works. The live hero shot needs a human-provided key and should be captured only after the live setup works.
2. Record clips automatically with the optional Playwright tool, or manually with the macOS screen recorder (`Cmd+Shift+5`) following [PLAN.md](PLAN.md). Install Playwright only if using automation: `python -m pip install playwright` and `python -m playwright install chromium`. `python video/tools/record_clips.py --live` records only the B1 hero shot. `python video/tools/record_clips.py --replay` records the available sample replay shots. The app UI never displays the key; the script does not inspect or log environment secrets. Do not open a terminal containing secrets while recording.
3. Generate narration with `python video/tools/make_voiceover.py --engine say` on macOS for a rough local timing track. Or use `python video/tools/make_voiceover.py --engine manual` to print text for an external TTS tool and save the audio under `video/audio/B1.mp3` through `B6.mp3`. **Check that the TTS tool's license allows public YouTube use.** The `say` output is AI/synthesized speech intended as a rough track; it writes AIFF files.
4. Run `python video/tools/check_timing.py`. Adjust narration or edit timing if any beat is TOO LONG or TOO SHORT. `timeline.csv` has target timings and a measured duration column filled after `say` generation.
5. Assemble in an editor such as CapCut, DaVinci Resolve, or iMovie. Use `timeline.csv` for clip order, target durations, captions, and shot notes; align the audio files with each beat and import `captions.srt` as subtitles. Check the subtitles against the final TTS audio. An optional ffmpeg draft can be built with `bash video/tools/assemble_draft.sh`; it expects six WebM clips and six MP3 voice tracks.
6. Export a 1080p H.264 MP4 under three minutes, with no music. Verify the result end to end and ensure B1 still shows a real live stopwatch.
7. Upload to YouTube as Public and paste the public video link in the Devpost form.

## Pre-recording checklist

- Close notifications; hide the bookmarks bar and other brands; set browser zoom to 100%; use a clean window.
- Make sure no API key, `.env`, or terminal with secrets is visible.
- Run the hero shot live, with the stopwatch visible in the first fifteen seconds.
- Keep the “synthetic incident” label visible and say “synthetic incident” in narration.
- Never label a live hero shot as a replay. Label any recorded replay **REPLAY** on screen.
- Use real metrics and cost only from the live run HUD. Use evaluation figures only from `eval/results.json` or `eval/RESULTS.md`; otherwise say “not yet measured.”

## Shot list

See [PLAN.md](PLAN.md) for the timed beat table and shorter edit. Source line views, verdict, runbook, postmortem and upload control are in the app. The evaluation chart is `eval/chart.png` only after the evaluation report has been generated.

## Files and limitations

- `voiceover/`: one plain text narration file per beat and the ordered combined script.
- `timeline.csv`: edit plan; measured audio duration starts blank until generation.
- `captions.srt`: generated from the scripts and target timeline by `python video/tools/make_captions.py`.
- `tools/record_clips.py`: optional local browser automation; requires Playwright and a running app.
- `tools/assemble_draft.sh`: optional ffmpeg assembly; generated draft is `out/draft.mp4`.
- `TODO_NUMBERS.md`: evidence source and measurement status for spoken and displayed values.

No measured evaluation results or recorded sample replays are present in this checkout. The corresponding shots must show “not yet measured” or be recorded after those artifacts have been generated.
