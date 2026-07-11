"""A lightweight per-request trace.

Every routing decision, tool call, skill call, and model hop appends a step.
The UI inspector renders this so the routing and execution are visible — the
signature of the whole demo.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Trace:
    steps: list[dict[str, Any]] = field(default_factory=list)
    route: dict[str, Any] = field(default_factory=dict)
    tokens: int = 0

    def set_route(self, tier: int, risk: str, complexity: str,
                  confidence: float, reason: str) -> None:
        self.route = {
            "tier": tier, "risk": risk, "complexity": complexity,
            "confidence": round(confidence, 2), "reason": reason,
        }

    def add(self, kind: str, name: str, detail: Any = None, ms: float = 0.0) -> None:
        self.steps.append({
            "kind": kind,            # router | tool | skill | model | tier | fallback | guard
            "name": name,
            "detail": detail,
            "ms": round(ms, 1),
        })

    @contextmanager
    def timed(self, kind: str, name: str, detail: Any = None):
        start = time.perf_counter()
        info: dict[str, Any] = {}
        try:
            yield info
        finally:
            ms = (time.perf_counter() - start) * 1000
            merged = detail if detail is not None else info.get("detail")
            self.add(kind, name, merged, ms)

    def to_dict(self) -> dict[str, Any]:
        return {"route": self.route, "steps": self.steps, "tokens": self.tokens}
