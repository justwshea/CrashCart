#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is required to assemble the draft. Install ffmpeg, then retry." >&2; exit 1; }
mkdir -p "$ROOT/out"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for beat in B1 B2 B3 B4 B5 B6; do
  clip="$ROOT/clips/$beat.webm"
  audio="$ROOT/audio/$beat.mp3"
  [[ -f "$clip" ]] || { echo "Missing clip: $clip" >&2; exit 1; }
  [[ -f "$audio" ]] || { echo "Missing audio: $audio (create it with make_voiceover.py or external TTS)" >&2; exit 1; }
done
for beat in B1 B2 B3 B4 B5 B6; do
  seconds="$(awk -F, -v beat="$beat" '$1==beat {print $4}' "$ROOT/timeline.csv")"
  [[ -n "$seconds" ]] || { echo "Missing duration for $beat in timeline.csv" >&2; exit 1; }
  ffmpeg -v error -y -i "$ROOT/clips/$beat.webm" -i "$ROOT/audio/$beat.mp3" \
    -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,tpad=stop_mode=clone:stop_duration=$seconds" \
    -t "$seconds" -map 0:v:0 -map 1:a:0 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$TMP/$beat.mp4"
  printf "file '%s'\n" "$TMP/$beat.mp4" >> "$TMP/list.txt"
done
ffmpeg -v error -y -f concat -safe 0 -i "$TMP/list.txt" -c copy "$ROOT/out/draft.mp4"
echo "Wrote video/out/draft.mp4"
