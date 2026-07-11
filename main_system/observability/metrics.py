"""Process-wide metrics — simple, thread-safe counters.

Incremented at the points that matter for operating the system: which tier served
a request, which fallback rung fired, breaker trips, guardrail events, and write-gate
outcomes. Exposed at /api/metrics.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)


def inc(name: str, n: int = 1) -> None:
    with _lock:
        _counters[name] += n


def snapshot() -> dict[str, Any]:
    with _lock:
        return dict(sorted(_counters.items()))


def reset() -> None:
    with _lock:
        _counters.clear()
