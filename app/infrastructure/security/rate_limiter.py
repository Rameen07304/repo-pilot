"""
In-process rate limiting — no Redis, no external service, just an
in-memory counter dict. This is fine for a single-container free-tier
deployment (HF Spaces). If this ever scales to multiple instances,
this whole file gets swapped for a Redis-backed version behind the
same interface — nothing else in the app needs to change.

Three independent limiters, matching our design:
  1. Per-IP questions/hour
  2. Per-IP deep-investigation/hour (tighter, since deep mode costs
     up to 5x a normal question)
  3. Global daily LLM request counter (soft cap at e.g. 80%)
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Window:
    """Fixed-window counter: count of events since window_start."""
    window_start: float = field(default_factory=time.time)
    count: int = 0


class SlidingWindowRateLimiter:
    """
    Simple fixed-window limiter (not sliding, for simplicity — good
    enough at this traffic scale; resets exactly on the hour boundary
    per key rather than a true rolling window).
    """

    def __init__(self, *, max_events: int, window_seconds: int = 3600) -> None:
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._windows: dict[str, _Window] = defaultdict(_Window)
        self._lock = Lock()

    def check_and_increment(self, key: str) -> tuple[bool, int]:
        """
        Returns (allowed, remaining). Thread-safe. Call this BEFORE
        doing the expensive work (LLM call), not after — a rejected
        request should cost nothing.
        """
        now = time.time()
        with self._lock:
            window = self._windows[key]

            if now - window.window_start > self._window_seconds:
                # Window expired, reset
                window.window_start = now
                window.count = 0

            if window.count >= self._max_events:
                return False, 0

            window.count += 1
            remaining = self._max_events - window.count
            return True, remaining

    def reset(self, key: str) -> None:
        """Mostly for tests."""
        with self._lock:
            self._windows.pop(key, None)


class DailyQuotaGuard:
    """
    Global (not per-IP) counter for total LLM calls today. Once we
    cross `soft_limit`, the app should start returning a friendly
    "at capacity, resets at midnight UTC" message instead of letting
    every remaining request burn through what's left of the free tier.
    """

    def __init__(self, *, daily_cap: int, soft_limit_percent: int = 80) -> None:
        self._daily_cap = daily_cap
        self._soft_limit = int(daily_cap * soft_limit_percent / 100)
        self._count = 0
        self._day_start = self._today_key()
        self._lock = Lock()

    @staticmethod
    def _today_key() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _maybe_reset(self) -> None:
        today = self._today_key()
        if today != self._day_start:
            self._day_start = today
            self._count = 0

    def is_over_soft_limit(self) -> bool:
        with self._lock:
            self._maybe_reset()
            return self._count >= self._soft_limit

    def record_call(self) -> None:
        with self._lock:
            self._maybe_reset()
            self._count += 1

    @property
    def current_count(self) -> int:
        with self._lock:
            self._maybe_reset()
            return self._count