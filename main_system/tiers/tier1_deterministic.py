"""Tier 1 — deterministic / rule-based. No model, always available.

Answers fully-specified requests exactly from the systems of record via
record_lookup, and gates high-stakes requests for confirmation (the write-gate
that actually commits arrives in Phase 4).
"""
from __future__ import annotations

from typing import Any

from main_system.context import Context
from main_system.harness.router import Route
from main_system.tools import registry


def handle(ctx: Context, route: Route) -> dict[str, Any]:
    ctx.trace.add("tier", "Tier 1 · deterministic", {"det_intent": route.det_intent})
    rl = registry.get("record_lookup")

    # --- high-stakes: confirm, don't execute -----------------------------
    if route.risk == "high":
        return _high_stakes(ctx, rl)

    intent = route.det_intent

    if intent == "balance":
        r = rl.call(ctx, 1, entity="balance")
        if not r.get("found"):
            return _text("I couldn't find an account on file for you.")
        return _text(f"Your current balance is {r['balance']} "
                     f"(plan {r['plan_code']}, status: {r['status']}).")

    if intent == "plan":
        r = rl.call(ctx, 1, entity="plan")
        if not r.get("found"):
            return _text("I couldn't find a plan on file for you.")
        return _text(f"You're on the {r['plan_name']} plan ({r['plan_code']}) "
                     f"at {r['monthly']}/month.")

    if intent == "invoice":
        r = rl.call(ctx, 1, entity="invoices", limit=1)
        if not r.get("invoices"):
            return _text("You have no invoices on file.")
        inv = r["invoices"][0]
        return _text(f"Your latest invoice {inv['invoice_id']} for {inv['period']} "
                     f"is {inv['amount']} ({inv['status']}).")

    if intent == "payment":
        r = rl.call(ctx, 1, entity="payments", limit=1)
        if not r.get("payments"):
            return _text("I don't see any payments on file.")
        pay = r["payments"][0]
        return _text(f"Your last payment was {pay['amount']} on {pay['paid_on']} "
                     f"via {pay['method']}.")

    return _text("I can help with balances, plans, invoices, and payments. "
                 "What would you like to check?")


def _high_stakes(ctx: Context, rl) -> dict[str, Any]:
    acct = rl.call(ctx, 1, entity="account")
    detail = (f"current plan {acct['plan_code']}, balance {acct['balance']}"
              if acct.get("found") else "your account")
    ctx.trace.add("guard", "confirmation-required",
                  {"note": "high-stakes action held for confirmation"})
    return {
        "text": ("This looks like a high-stakes change to " + detail + ". "
                 "For your protection I won't make account or billing changes "
                 "without an explicit confirmation, and every change is validated "
                 "against policy and logged. Reply 'confirm' to proceed, or tell me "
                 "what you'd like to change."),
        "requires_confirmation": True,
    }


def _text(t: str) -> dict[str, Any]:
    return {"text": t, "requires_confirmation": False}
