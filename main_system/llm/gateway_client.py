"""Client-side model gateway, guarded by a circuit breaker.

The single place the main system reaches the LLM subsystem. Each call passes
through a circuit breaker: when the service is failing, the breaker opens and
calls fail fast (raising LLMUnavailable) instead of waiting on the network. The
enterprise harness catches LLMUnavailable and runs the fallback ladder.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import requests

from main_system.config import CFG
from main_system.observability.trace import Trace
from main_system.resilience.circuit_breaker import CircuitBreaker

_URL = CFG["services"]["llm_service_url"]
_TIMEOUT = CFG["services"]["llm_timeout_seconds"]
_CB = CFG["resilience"]["circuit_breaker"]

_breaker = CircuitBreaker("llm_service",
                          failure_threshold=_CB["failure_threshold"],
                          recovery_timeout=_CB["recovery_timeout_seconds"],
                          success_threshold=_CB["success_threshold"])


class LLMUnavailable(Exception):
    """Raised when the LLM service is unreachable, returns an outage code, or the
    circuit breaker is open."""


class _Upstream(Exception):
    """Internal: an actual upstream failure (connection error or 503)."""


def breaker_state() -> dict[str, Any]:
    return _breaker.snapshot()


def _guarded(task: str, tier: Optional[int], do_http: Callable[[], dict],
             trace: Optional[Trace]) -> dict:
    allowed, why = _breaker.allow()
    if not allowed:
        snap = _breaker.snapshot()
        if trace:
            trace.add("breaker", "OPEN — fast-fail", {"recovery_in": snap["recovery_in"]})
        raise LLMUnavailable("circuit open")

    start = time.perf_counter()
    try:
        data = do_http()
    except _Upstream as exc:
        _breaker.on_failure()
        _record(trace, task, tier, start, error=str(exc))
        if trace:
            trace.add("breaker", f"failure -> {_breaker.state}", _breaker.snapshot())
        raise LLMUnavailable(str(exc)) from exc

    _breaker.on_success()
    if why.startswith("half_open") and trace:
        trace.add("breaker", "probe ok -> CLOSED", _breaker.snapshot())
    _record(trace, task, tier, start, model=data.get("model"),
            mode=data.get("mode"), usage=data.get("usage"))
    return data


def _post(path: str, body: dict) -> dict:
    try:
        resp = requests.post(f"{_URL}{path}", json=body, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise _Upstream(str(exc)) from exc
    if resp.status_code == 503:
        raise _Upstream("llm_service returned 503")
    resp.raise_for_status()
    return resp.json()


def complete(tier: int, task: str, *, system: str = "", prompt: str = "",
             text: str = "", payload: Optional[dict[str, Any]] = None,
             trace: Optional[Trace] = None) -> str:
    body = {"tier": str(tier), "task": task, "system": system,
            "prompt": prompt, "text": text, "payload": payload or {}}
    data = _guarded(task, tier, lambda: _post("/v1/complete", body), trace)
    return data["text"]


def embed(texts: list[str], trace: Optional[Trace] = None) -> list[list[float]]:
    data = _guarded("embed", None, lambda: _post("/v1/embed", {"input": texts}), trace)
    return data["embeddings"]


def health() -> dict[str, Any]:
    """Direct probe — deliberately bypasses the breaker so the UI sees reality."""
    try:
        resp = requests.get(f"{_URL}/health", timeout=3)
        return resp.json() | {"reachable": resp.status_code == 200}
    except requests.RequestException as exc:
        return {"reachable": False, "status": "unreachable", "error": str(exc)}


def _record(trace: Optional[Trace], task: str, tier: Optional[int], start: float,
            **info: Any) -> None:
    if trace is None:
        return
    ms = (time.perf_counter() - start) * 1000
    usage = info.get("usage") or {}
    detail = {"task": task, "tier": tier, "model": info.get("model"),
              "mode": info.get("mode")}
    if info.get("error"):
        detail["error"] = info["error"]
    trace.add("model", f"llm:{task}", detail, ms)
    trace.tokens += (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
