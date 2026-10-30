#!/usr/bin/env python3
"""Estimate narration duration using 2.5 words/second plus 0.4s pauses."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_common import ROOT, read_timeline, split_sentences


def estimate(text: str) -> float:
    sentences = split_sentences(text)
    words = len(text.split())
    return words / 2.5 + max(0, len(sentences) - 1) * 0.4


def main() -> int:
    rows = read_timeline()
    total = 0.0
    planned = 0.0
    okay = True
    for row in rows:
        text = (ROOT / "voiceover" / row["voiceover_filename"]).read_text(encoding="utf-8")
        spoken = estimate(text)
        duration = float(row["duration_s"])
        total += spoken
        planned += duration
        if spoken > duration:
            status = "TOO LONG"
            okay = False
        elif spoken < duration * .72:
            status = "TOO SHORT"
            okay = False
        else:
            status = "OK"
        print(f"{row['beat_id']}: {spoken:.1f}s / {duration:.1f}s — {status}")
    status = "OK" if total <= planned and planned < 180 else "TOO LONG"
    print(f"Total: {total:.1f}s estimated speech / {planned:.1f}s planned — {status}")
    return 0 if okay and status == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
