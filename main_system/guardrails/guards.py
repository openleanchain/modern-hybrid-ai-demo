"""Guardrails around the model.

Input guard: block prompt-injection attempts and over-long messages before they
reach routing.

Output guard: numeric grounding. Every currency figure in a model-written reply
must match a real figure from this customer's records; ungrounded figures are
flagged. This is the deterministic enforcement of "never invent numbers" — the
model can phrase, but it can't make up a balance.
"""
from __future__ import annotations

import re
from typing import Any

from main_system.config import CFG
from main_system.context import Context
from main_system.db import database as db
from main_system.observability import metrics

_G = CFG["guardrails"]
_ENABLED = _G.get("enabled", True)
_INJ = [re.compile(p, re.I) for p in _G["injection_patterns"]]
_MAXLEN = int(_G["max_message_chars"])
_MONEY = re.compile(r"(?:USD|EUR|GBP|\$)\s?\d[\d,]*(?:\.\d{2})?", re.I)


def check_input(ctx: Context) -> dict[str, Any]:
    if not _ENABLED:
        return {"ok": True}
    msg = ctx.message or ""
    if len(msg) > _MAXLEN:
        metrics.inc("guard_input_blocked")
        ctx.trace.add("guard", "input: over length", {"chars": len(msg)})
        return {"ok": False, "reason": "message too long"}
    for p in _INJ:
        if p.search(msg):
            metrics.inc("guard_input_blocked")
            db.audit("guardrail", "input_injection_blocked",
                     ctx.tenant_id, ctx.customer_id, {"pattern": p.pattern})
            ctx.trace.add("guard", "input: injection blocked", {"pattern": p.pattern})
            return {"ok": False, "reason": "injection"}
    return {"ok": True}


def check_output(ctx: Context, text: str) -> dict[str, Any]:
    if not _ENABLED:
        return {"grounded": True, "flags": []}
    figures = {_norm(f) for f in _MONEY.findall(text or "")}
    if not figures:
        return {"grounded": True, "flags": []}
    allowed = _known_figures(ctx)
    ungrounded = sorted(f for f in figures if f not in allowed)
    if ungrounded:
        metrics.inc("guard_output_flag")
        ctx.trace.add("guard", "output: ungrounded figure(s)", {"figures": ungrounded})
        return {"grounded": False, "flags": ungrounded}
    ctx.trace.add("guard", "output: figures grounded", {"count": len(figures)})
    return {"grounded": True, "flags": []}


def _norm(fig: str) -> str:
    n = re.sub(r"[^0-9.]", "", fig)
    try:
        return f"{float(n):.2f}"
    except ValueError:
        return n


def _known_figures(ctx: Context) -> set[str]:
    vals: set[str] = set()
    for r in db.query_all("SELECT balance_cents AS v FROM accounts WHERE tenant_id=? AND customer_id=?",
                          (ctx.tenant_id, ctx.customer_id)):
        vals.add(f"{r['v'] / 100:.2f}")
    for r in db.query_all("SELECT amount_cents AS v FROM invoices WHERE tenant_id=? AND customer_id=?",
                          (ctx.tenant_id, ctx.customer_id)):
        vals.add(f"{r['v'] / 100:.2f}")
    for r in db.query_all("SELECT monthly_cents AS v FROM plans WHERE tenant_id=?",
                          (ctx.tenant_id,)):
        vals.add(f"{r['v'] / 100:.2f}")
    return vals
