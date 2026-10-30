"""Helpers for the offline demo-video production kit."""

from __future__ import annotations

import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TIMELINE = ROOT / "timeline.csv"
BEAT_ORDER = ["B1", "B2", "B3", "B4", "B5", "B6"]


def read_timeline(path: Path = TIMELINE) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_time(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2})", value.strip())
    if not match:
        raise ValueError("time must use M:SS")
    return int(match.group(1)) * 60 + int(match.group(2))


def split_sentences(text: str) -> list[str]:
    text = " ".join(text.split())
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def make_srt_cues(text: str, start: float, duration: float,
                  max_chars: int = 42) -> list[tuple[float, float, str]]:
    """Wrap narration into two-line cues and distribute them over the beat."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    cues = []
    # At most two lines per cue; individual words longer than max_chars are split.
    normalized = []
    for line in lines:
        while len(line) > max_chars:
            normalized.append(line[:max_chars])
            line = line[max_chars:]
        normalized.append(line)
    cue_lines = ["\n".join(normalized[i:i + 2]) for i in range(0, len(normalized), 2)]
    if not cue_lines:
        return []
    step = duration / len(cue_lines)
    for index, cue in enumerate(cue_lines):
        cues.append((start + index * step, start + (index + 1) * step, cue))
    return cues


def srt_timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def write_srt(rows: list[dict[str, str]], voiceover_dir: Path,
              output: Path) -> None:
    blocks = []
    cue_number = 1
    for row in rows:
        start = parse_time(row["start"])
        duration = float(row["duration_s"])
        text = (voiceover_dir / row["voiceover_filename"]).read_text(encoding="utf-8")
        for cue_start, cue_end, cue_text in make_srt_cues(text, start, duration):
            blocks.append(f"{cue_number}\n{srt_timestamp(cue_start)} --> {srt_timestamp(cue_end)}\n{cue_text}")
            cue_number += 1
    output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def audio_duration(path: Path) -> float:
    """Use ffprobe if installed; fall back to Python's standard wave module."""
    import shutil
    import subprocess
    if shutil.which("ffprobe"):
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ], check=True, capture_output=True, text=True)
        return float(result.stdout.strip())
    import wave
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())
