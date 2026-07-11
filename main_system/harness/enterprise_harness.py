"""Enterprise harness — the control plane.

A deterministic workflow (not an agent). The full request lifecycle:

  1. input guardrail          block injection / over-long messages
  2. confirmation?            if 'confirm' with a staged action -> write-gate commit
  3. route                    complexity x risk
  4a. high-stakes             -> write-gate (propose -> validate -> stage), no model
  4b. otherwise               -> dispatch to one tier; on LLM outage, fallback ladder
  5. output guardrail         numeric grounding on model-written replies
  6. memory                   record the turn (persist, compress if due)

Write-gate and fallback are both deterministic, so high-stakes actions and
data answers keep working even with the model down.
"""
from __future__ import annotations

from typing import Any

from main_system.context import Context
from main_system.db import database as db
from main_system.guardrails import guards
from main_system.harness import router
from main_system.llm import gateway_client as gw
from main_system.llm.gateway_client import LLMUnavailable
from main_system.memory.session_memory import SessionMemory
from main_system.observability import metrics
from main_system.observability.trace import Trace
from main_system.resilience import fallback
from main_system.tiers import tier1_deterministic as tier1
from main_system.tiers import tier2_simple as tier2
from main_system.tiers import tier3_agent as tier3
from main_system.writegate import write_gate

_DISPATCH = {1: tier1.handle, 2: tier2.handle, 3: tier3.handle}


def handle_message(tenant_id: str, customer_id: str, session_id: str,
                   message: str) -> dict[str, Any]:
    trace = Trace()
    ctx = Context(tenant_id=tenant_id, customer_id=customer_id,
                  session_id=session_id, message=message, trace=trace)
    metrics.inc("requests")

    mem = SessionMemory.load(session_id, tenant_id, customer_id)
    ctx.memory = mem
    trace.add("memory", "loaded",
              {"raw_turns": len(mem.raw_turns), "has_summary": bool(mem.running_summary)})

    # 1. input guardrail
    gin = guards.check_input(ctx)
    if not gin["ok"]:
        trace.set_route(1, "n/a", "n/a", 1.0, f"blocked by guardrail: {gin['reason']}")
        result = {"text": "I can't help with that request. If you have a question about "
                          "your account, billing, or our policies, I'm happy to help.",
                  "requires_confirmation": False}
        return _finish(ctx, mem, result, guard_in=gin)

    served_by_fallback = False
    guard_out: dict[str, Any] = {"grounded": True, "flags": []}

    # 2. confirmation of a staged write-gate action (handles 'nothing staged' too)
    if write_gate.is_confirmation(message):
        has_pending = db.pending_get(session_id) is not None
        trace.set_route(1, "high" if has_pending else "low", "n/a", 1.0,
                        "write-gate: commit staged action" if has_pending
                        else "write-gate: confirmation (nothing staged)")
        trace.add("guard", "write-gate: confirmation received", {"pending": has_pending})
        metrics.inc("tier_1")
        result = write_gate.commit_pending(ctx)
        return _finish(ctx, mem, result, guard_in=gin, guard_out=guard_out,
                       served_by_fallback=False)

    # 3. route
    route = router.route(ctx)
    metrics.inc(f"tier_{route.tier}")

    # 4a. high-stakes -> write-gate (deterministic; works even if the model is down)
    if route.risk == "high":
        result = write_gate.stage(ctx, route)
    else:
        # 4b. dispatch to one tier; on outage, run the fallback ladder
        try:
            result = _DISPATCH[route.tier](ctx, route)
            if route.tier in (2, 3) and not result.get("requires_confirmation"):
                db.cache_put(tenant_id, customer_id, fallback.normalize(message),
                             result["text"], route.tier)
                # 5. output guardrail on model-written replies
                guard_out = guards.check_output(ctx, result["text"])
        except LLMUnavailable:
            served_by_fallback = True
            result = fallback.run_ladder(ctx, route)
            metrics.inc("fallback_used")
            if result.get("fallback_rung"):
                metrics.inc(f"fallback_{result['fallback_rung']}")

    return _finish(ctx, mem, result, guard_in=gin, guard_out=guard_out,
                   served_by_fallback=served_by_fallback)


def _finish(ctx: Context, mem: SessionMemory, result: dict[str, Any],
            guard_in: dict[str, Any], guard_out: dict[str, Any] | None = None,
            served_by_fallback: bool = False) -> dict[str, Any]:
    mem.append_turn(ctx, ctx.message, result["text"])
    return {
        "text": result["text"],
        "requires_confirmation": result.get("requires_confirmation", False),
        "route": ctx.trace.route,
        "trace": ctx.trace.to_dict(),
        "memory": mem.snapshot(),
        "resilience": {
            "breaker": gw.breaker_state(),
            "served_by_fallback": served_by_fallback,
            "fallback_rung": result.get("fallback_rung"),
            "human_queue_depth": db.human_queue_depth(),
        },
        "guard": {"input_ok": guard_in.get("ok", True),
                  "output": guard_out or {"grounded": True, "flags": []}},
    }
