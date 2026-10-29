"""In-memory per-IP hourly and daily run caps."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Optional

from app.config import settings


class RunLimiter:
    def __init__(self, daily_cap: Optional[int] = None, ip_per_hour: Optional[int] = None):
        self.daily_cap = settings.daily_run_cap if daily_cap is None else max(0, daily_cap)
        self.ip_per_hour = settings.ip_runs_per_hour if ip_per_hour is None else max(0, ip_per_hour)
        self._daily_date = date.today()
        self._daily_count = 0
        self._by_ip = defaultdict(deque)
        self._lock = Lock()

    def reserve(self, ip: str, replay_run_id: str = "xid79_demo",
                now: Optional[datetime] = None) -> tuple[bool, Optional[str]]:
        now = now or datetime.now()
        with self._lock:
            if now.date() != self._daily_date:
                self._daily_date = now.date()
                self._daily_count = 0
                self._by_ip.clear()
            recent = self._by_ip[ip]
            cutoff = now - timedelta(hours=1)
            while recent and recent[0] <= cutoff:
                recent.popleft()
            if self._daily_count >= self.daily_cap or len(recent) >= self.ip_per_hour:
                return False, replay_run_id
            self._daily_count += 1
            recent.append(now)
            return True, None


limiter = RunLimiter()
