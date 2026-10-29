"""Load incident bundles without exposing evaluation ground truth."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


TIMESTAMP_RE = re.compile(r"^(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?)")
STEP_RE = re.compile(r"\bstep\s+(\d+)\b", re.IGNORECASE)
MAX_CHUNK_TOKENS = 16_000


@dataclass
class SourceFile:
    path: str
    kind: str
    rank: Optional[int] = None
    node: Optional[int] = None
    lines: list[str] = field(default_factory=list)
    timestamps: list[Optional[str]] = field(default_factory=list)

    @property
    def numbered_text(self) -> str:
        return "\n".join(f"L{i:05d}|{line}" for i, line in enumerate(self.lines, 1))


@dataclass
class Chunk:
    id: str
    file: str
    kind: str
    rank: Optional[int]
    node: Optional[int]
    line_start: int
    line_end: int
    text: str
    est_tokens: int


@dataclass
class Bundle:
    root: Path
    files: list[SourceFile]
    est_tokens: int

    @property
    def by_path(self) -> dict[str, SourceFile]:
        return {file.path: file for file in self.files}


@dataclass
class FileStats:
    file: str
    first_ts: Optional[str]
    last_ts: Optional[str]
    last_step: Optional[int]


def parse_timestamp(line: str) -> Optional[str]:
    match = TIMESTAMP_RE.match(line)
    return match.group(1) if match else None


def _kind(path: Path) -> tuple[str, Optional[int], Optional[int]]:
    name = path.name
    match = re.fullmatch(r"rank_(\d+)\.log", name)
    if match:
        rank = int(match.group(1))
        return "rank_log", rank, rank // 8
    match = re.fullmatch(r"dmesg_node-(\d+)\.log", name)
    if match:
        return "dmesg", None, int(match.group(1))
    if name == "scheduler.log":
        return "scheduler", None, None
    if path.suffix.lower() in {".yaml", ".yml"}:
        return "config", None, None
    if path.suffix.lower() == ".png":
        return "dashboard", None, None
    return "config", None, None


def _chunks(source: SourceFile, limit: int = MAX_CHUNK_TOKENS) -> list[Chunk]:
    if source.kind == "dashboard" or not source.lines:
        return []
    result: list[Chunk] = []
    current: list[str] = []
    start_line = 1
    for line_number, line in enumerate(source.lines, 1):
        numbered = f"L{line_number:05d}|{line}"
        candidate = "\n".join(current + [numbered])
        if current and len(candidate) // 4 > limit:
            text = "\n".join(current)
            digest = hashlib.sha1(f"{source.path}:{start_line}:{line_number - 1}".encode("utf-8")).hexdigest()[:10]
            result.append(Chunk(digest, source.path, source.kind, source.rank, source.node,
                                start_line, line_number - 1, text, len(text) // 4))
            current, start_line = [], line_number
        current.append(numbered)
    if current:
        text = "\n".join(current)
        digest = hashlib.sha1(f"{source.path}:{start_line}:{len(source.lines)}".encode("utf-8")).hexdigest()[:10]
        result.append(Chunk(digest, source.path, source.kind, source.rank, source.node,
                            start_line, len(source.lines), text, len(text) // 4))
    return result


def load_bundle(path: str | Path) -> Bundle:
    root = Path(path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    files: list[SourceFile] = []
    for source_path in sorted(root.rglob("*")):
        if not source_path.is_file() or source_path.name.lower() == "truth.json":
            continue
        relative = source_path.relative_to(root).as_posix()
        kind, rank, node = _kind(source_path)
        if kind == "dashboard":
            files.append(SourceFile(relative, kind, rank, node))
            continue
        content = source_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        files.append(SourceFile(relative, kind, rank, node, lines,
                                [parse_timestamp(line) for line in lines]))
    bundle = Bundle(root, files, 0)
    bundle.est_tokens = sum(chunk.est_tokens for file in files for chunk in _chunks(file))
    return bundle


def chunks_for_bundle(bundle: Bundle, max_tokens: int = MAX_CHUNK_TOKENS) -> list[Chunk]:
    return [chunk for source in bundle.files for chunk in _chunks(source, max_tokens)]


def file_stats(bundle: Bundle) -> list[FileStats]:
    result = []
    for source in bundle.files:
        times = [value for value in source.timestamps if value is not None]
        steps = [int(match.group(1)) for line in source.lines if (match := STEP_RE.search(line))]
        result.append(FileStats(source.path, times[0] if times else None,
                                times[-1] if times else None, steps[-1] if steps else None))
    return result


def silence_findings(bundle: Bundle) -> list[dict]:
    rank_stats = [stat for stat in file_stats(bundle)
                  if bundle.by_path[stat.file].kind == "rank_log" and stat.last_ts]
    if not rank_stats:
        return []
    end_values = sorted(datetime.fromisoformat(stat.last_ts.replace("Z", "+00:00"))
                        for stat in rank_stats)
    middle = len(end_values) // 2
    peer_end = end_values[middle] if len(end_values) % 2 else (
        end_values[middle - 1] + (end_values[middle] - end_values[middle - 1]) / 2
    )
    findings = []
    for stat in rank_stats:
        peer_time = peer_end
        last_time = datetime.fromisoformat(stat.last_ts.replace("Z", "+00:00"))
        delta = (peer_time - last_time).total_seconds()
        if delta <= 120:
            continue
        source = bundle.by_path[stat.file]
        nonempty = [(i, line) for i, line in enumerate(source.lines, 1) if line.strip()]
        if not nonempty:
            continue
        line_number, quote = nonempty[-1]
        findings.append({"file": stat.file, "line_start": line_number, "line_end": line_number,
                         "quote": quote, "kind": "silence", "severity": 4,
                         "why": f"log ends {int(delta)} s before peers", "timestamp": stat.last_ts})
    return sorted(findings, key=lambda item: item["file"])
