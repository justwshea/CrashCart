#!/usr/bin/env python3
"""Create rough macOS speech tracks or print text for external TTS."""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_common import ROOT, audio_duration, read_timeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("say", "manual"), required=True)
    args = parser.parse_args()
    rows = read_timeline()
    if args.engine == "manual":
        for row in rows:
            text = (ROOT / "voiceover" / row["voiceover_filename"]).read_text(encoding="utf-8").strip()
            print(f"\n=== {row['beat_id']} — save as video/audio/{row['beat_id']}.mp3 ===\n{text}")
        print("Check that your TTS tool license permits public YouTube use.")
        audio_dir = ROOT / "audio"
        candidates = [audio_dir / f"{row['beat_id']}.mp3" for row in rows]
        if all(path.is_file() for path in candidates):
            for row, path in zip(rows, candidates):
                row["measured_duration_s"] = f"{audio_duration(path):.3f}"
            with (ROOT / "timeline.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print("Measured all MP3 tracks and updated timeline.csv")
        else:
            print("After saving all six audio files, run this command again to measure durations.")
        return 0
    if not shutil.which("say"):
        print("The macOS 'say' command is unavailable; use --engine manual.", file=sys.stderr)
        return 2
    audio_dir = ROOT / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        text_path = ROOT / "voiceover" / row["voiceover_filename"]
        out_path = audio_dir / f"{row['beat_id']}.aiff"
        subprocess.run(["say", "-o", str(out_path), "-f", str(text_path)], check=True)
    for row in rows:
        row["measured_duration_s"] = f"{audio_duration(audio_dir / (row['beat_id'] + '.aiff')):.3f}"
    with (ROOT / "timeline.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote rough audio to video/audio/*.aiff and measured durations to timeline.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
