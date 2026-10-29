"""JSONL event recording and time-faithful replay."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from app.config import settings


_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples" / "runs"


class EventRecorder:
    def __init__(self, run_id: str, runs_dir: Optional[str | Path] = None,
                 downstream: Optional[Callable] = None):
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run id")
        self.run_id = run_id
        self.directory = Path(runs_dir or settings.runs_dir) / run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "events.jsonl"
        self.started = time.monotonic()
        self.downstream = downstream
        self._lock = asyncio.Lock()

    async def __call__(self, event: str, payload: dict) -> None:
        record = {"t": time.monotonic() - self.started, "event": event, "data": payload}
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.downstream is not None:
            result = self.downstream(event, payload)
            if inspect.isawaitable(result):
                await result


def read_events(run_id: str, runs_dir: Optional[str | Path] = None) -> list[dict]:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run id")
    path = Path(runs_dir or settings.runs_dir) / run_id / "events.jsonl"
    if not path.is_file():
        path = SAMPLES_DIR / f"{run_id}.jsonl"
        if not path.is_file():
            return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def replay(run_id: str, speed: float = 1.0,
                 runs_dir: Optional[str | Path] = None) -> AsyncIterator[dict]:
    if speed <= 0:
        raise ValueError("speed must be greater than zero")
    previous = None
    for event in read_events(run_id, runs_dir):
        current = float(event.get("t", 0))
        if previous is not None:
            delay = max(0.0, current - previous) / speed
            if delay:
                await asyncio.sleep(delay)
        previous = current
        yield event
