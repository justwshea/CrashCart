from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "video"
sys.path.insert(0, str(VIDEO / "tools"))
from video_common import make_srt_cues, parse_time, read_timeline, write_srt


def test_srt_writer_limits_lines_and_uses_timeline(tmp_path):
    voice = tmp_path / "voice"
    voice.mkdir()
    (voice / "B1.txt").write_text("A short first sentence. This is the second sentence.", encoding="utf-8")
    rows = [{"beat_id": "B1", "start": "0:10", "end": "0:15", "duration_s": "5",
             "voiceover_filename": "B1.txt"}]
    output = tmp_path / "captions.srt"
    write_srt(rows, voice, output)
    srt = output.read_text(encoding="utf-8")
    assert "00:00:10,000 -->" in srt
    cues = [block.splitlines()[2:] for block in srt.strip().split("\n\n")]
    assert all(len(cue) <= 2 and all(len(line) <= 42 for line in cue) for cue in cues)


def test_srt_wrapping_caps_long_word_lines():
    cues = make_srt_cues("x" * 90, 0, 5)
    assert cues
    assert all(len(line) <= 42 for _, _, cue in cues for line in cue.splitlines())


def test_timeline_has_no_overlaps_and_is_under_three_minutes():
    rows = read_timeline()
    assert [row["beat_id"] for row in rows] == ["B1", "B2", "B3", "B4", "B5", "B6"]
    previous_end = 0.0
    for row in rows:
        start, end = parse_time(row["start"]), parse_time(row["end"])
        assert start >= previous_end
        assert end - start == float(row["duration_s"])
        previous_end = end
    assert previous_end < 180


def test_timing_estimator_uses_word_rate_and_sentence_pauses():
    spec = importlib.util.spec_from_file_location("check_timing", VIDEO / "tools" / "check_timing.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.estimate("One two. Three!") == 1.6
