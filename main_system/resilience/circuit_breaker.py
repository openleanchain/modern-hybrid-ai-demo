"""A circuit breaker for the model gateway.

Lives on the client side (the main system), wrapping calls to the LLM service.
When the upstream fails repeatedly the breaker OPENs and calls fail fast — no
network wait, no hammering a service that's already down — until a recovery
window elapses, when it probes once (HALF-OPEN) and either closes or re-opens.

States:
  CLOSED     normal; failures counted, threshold opens the breaker
  OPEN       short-circuit; every call fails fast until recovery_timeout passes
  HALF_OPEN  a single probe is allowed; success closes, failure re-opens
"""
from __future__ import annotations

import threading
import time
from typing import Any


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int,
                 recovery_timeout: float, success_threshold: int):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self._lock = threading.Lock()
        self.state = "closed"
        self.failures = 0
        self.successes = 0
        self.opened_at = 0.0
        self.transitions = 0

    # --- gate: may this call proceed? -----------------------------------
    def allow(self) -> tuple[bool, str]:
        with self._lock:
            if self.state == "closed":
                return True, "closed"
            if self.state == "open":
                if time.monotonic() - self.opened_at >= self.recovery_timeout:
                    self.state = "half_open"
                    self.successes = 0
                    self.transitions += 1
                    return True, "half_open probe"
                return False, "open"
            return True, "half_open probe"      # half_open: allow the probe

    # --- outcomes --------------------------------------------------------
    def on_success(self) -> None:
        with self._lock:
            if self.state == "half_open":
                self.successes += 1
                if self.successes >= self.success_threshold:
                    self._close()
            else:
                self.failures = 0

    def on_failure(self) -> None:
        with self._lock:
            if self.state == "half_open":
                self._open()                    # probe failed → back to open
            else:
                self.failures += 1
                if self.failures >= self.failure_threshold:
                    self._open()

    def _open(self) -> None:
        if self.state != "open":
            self.transitions += 1
        self.state = "open"
        self.opened_at = time.monotonic()

    def _close(self) -> None:
        self.state = "closed"
        self.failures = 0
        self.successes = 0
        self.transitions += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            remaining = 0.0
            if self.state == "open":
                remaining = max(0.0, self.recovery_timeout -
                                (time.monotonic() - self.opened_at))
            return {"state": self.state, "failures": self.failures,
                    "failure_threshold": self.failure_threshold,
                    "recovery_in": round(remaining, 1)}
