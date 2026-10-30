#!/usr/bin/env python3
"""Generate captions.srt from voiceover text and timeline.csv."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_common import ROOT, read_timeline, write_srt


if __name__ == "__main__":
    write_srt(read_timeline(), ROOT / "voiceover", ROOT / "captions.srt")
    print("Wrote video/captions.srt")
