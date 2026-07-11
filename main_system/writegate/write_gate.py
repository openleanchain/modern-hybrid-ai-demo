"""The write-gate — model proposes, deterministic code disposes.

High-stakes actions (refund, cancellation) never execute straight from a model's
say-so. They flow: propose -> validate -> confirm -> commit.

  propose   turn the request + records into a concrete, structured action
  validate  deterministic policy checks (ownership/tenant, refund window, amount
            cap, account status) — the model has no vote here
  stage     store the validated proposal and ask the customer to confirm
  commit    only after an explicit 'confirm', perform the write in a transaction
            and audit it; amounts over the auto-approve cap go to a human instead

Ownership checks double as tenant-isolation enforcement: an action can only touch
rows that belong to the caller's tenant and customer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from main_system.config import CFG
from main_system.context import Context
from main_system.db import database as db
from main_system.observability import metrics

_W = CFG["writegate"]
_WINDOW = int(_W["refund_window_days"])
_CAP = int(_W["auto_approve_cap_cents"])

_CONFIRM = re.compile(r"^\s*(confirm|yes,?\s*(please\s*)?(proceed|go ahead|do it)|"
                      r"go ahead|proceed|approve)\s*[.!]?\s*$", re.I)


def is_confirmation(message: str) -> bool:
    return bool(_CONFIRM.match(message or ""))


def _money(cents: int) -> str:
    return f"USD {cents / 100:,.2f}"


@dataclass
class ProposedAction:
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


# --- propose --------------------------------------------------------------
def _propose(ctx: Context) -> Optional[ProposedAction]:
    t, c, msg = ctx.tenant_id, ctx.customer_id, ctx.message.lower()

    if "refund" in msg or "overcharge" in msg or "overcharged" in msg:
        inv = db.query_one(
            "SELECT invoice_id, amount_cents FROM invoices WHERE tenant_id=? AND "
            "customer_id=? AND status='open' AND (note LIKE '%prorat%' OR "
            "note LIKE '%not prorated%') ORDER BY issued_on DESC LIMIT 1", (t, c))
        if not inv:
            return ProposedAction("refund", {}, "no overcharge on record")
        std = db.query_one("SELECT monthly_cents FROM plans WHERE tenant_id=? AND plan_code='STD'", (t,))
        pro = db.query_one("SELECT monthly_cents FROM plans WHERE tenant_id=? AND plan_code='PRO'", (t,))
        # Mid-cycle change ≈ half old rate + half new rate (demo proration).
        correct = ((std["monthly_cents"] if std else 3000) +
                   (pro["monthly_cents"] if pro else 6000)) // 2
        overcharge = inv["amount_cents"] - correct
        return ProposedAction("refund", {
            "invoice_id": inv["invoice_id"], "billed_cents": inv["amount_cents"],
            "correct_cents": correct, "amount_cents": overcharge}, "proration correction")

    if "cancel" in msg or "close" in msg:
        return ProposedAction("cancel", {}, "customer requested cancellation")

    return None


# --- validate (deterministic; the model has no vote) ----------------------
def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _validate(ctx: Context, action: ProposedAction) -> dict[str, Any]:
    t, c = ctx.tenant_id, ctx.customer_id
    checks: list[dict[str, Any]] = []

    if action.kind == "refund":
        p = action.params
        if not p:
            return {"ok": False, "requires_manager": False,
                    "checks": [_check("overcharge_found", False, "no overcharge on record")]}
        inv = db.query_one(
            "SELECT issued_on, amount_cents FROM invoices WHERE tenant_id=? AND "
            "customer_id=? AND invoice_id=?", (t, c, p["invoice_id"]))
        own = inv is not None
        checks.append(_check("ownership", own,
                             f"invoice {p['invoice_id']} belongs to {c}@{t}"))
        if not own:
            return {"ok": False, "requires_manager": False, "checks": checks}

        days = (date.today() - date.fromisoformat(inv["issued_on"])).days
        win = days <= _WINDOW
        checks.append(_check("refund_window", win, f"{days}d old, window {_WINDOW}d"))

        amt = p["amount_cents"]
        amt_ok = 0 < amt <= inv["amount_cents"]
        checks.append(_check("amount_sane", amt_ok,
                             f"{_money(amt)} of {_money(inv['amount_cents'])}"))

        needs_mgr = amt > _CAP
        checks.append(_check("under_auto_cap", not needs_mgr,
                             f"{_money(amt)} vs cap {_money(_CAP)}"))

        acct = db.query_one("SELECT status FROM accounts WHERE tenant_id=? AND customer_id=?", (t, c))
        active = bool(acct and acct["status"] == "active")
        checks.append(_check("account_active", active, acct["status"] if acct else "no account"))

        ok = own and win and amt_ok and active
        return {"ok": ok, "requires_manager": needs_mgr, "checks": checks}

    if action.kind == "cancel":
        acct = db.query_one("SELECT status FROM accounts WHERE tenant_id=? AND customer_id=?", (t, c))
        active = bool(acct and acct["status"] == "active")
        checks.append(_check("account_active", active, acct["status"] if acct else "no account"))
        return {"ok": active, "requires_manager": False, "checks": checks}

    return {"ok": False, "requires_manager": False,
            "checks": [_check("known_action", False, action.kind)]}


def _fail_lines(v: dict[str, Any]) -> str:
    return "; ".join(f"{c['name']} ({c['detail']})" for c in v["checks"] if not c["passed"])


# --- stage (propose + validate + ask to confirm) --------------------------
def stage(ctx: Context, route) -> dict[str, Any]:
    metrics.inc("writegate_proposed")
    ctx.trace.add("guard", "write-gate: propose", {})
    action = _propose(ctx)

    if action is None:
        db.human_enqueue(ctx.tenant_id, ctx.customer_id, ctx.message, "unrecognized high-stakes request")
        ctx.trace.add("guard", "write-gate: no action -> human", {})
        return {"text": "I want to be careful here — I couldn't turn that into a specific, "
                        "safe change, so I've routed it to a specialist who'll follow up.",
                "requires_confirmation": False}

    v = _validate(ctx, action)
    ctx.trace.add("guard", "write-gate: validate",
                  {"ok": v["ok"], "requires_manager": v["requires_manager"],
                   "checks": v["checks"]})

    if not v["ok"]:
        metrics.inc("writegate_declined")
        db.human_enqueue(ctx.tenant_id, ctx.customer_id, ctx.message,
                         f"validation failed: {_fail_lines(v)}")
        return {"text": f"I can't do that one automatically — it fails a policy check "
                        f"({_fail_lines(v)}). I've routed it to a specialist.",
                "requires_confirmation": False}

    if v["requires_manager"]:
        metrics.inc("writegate_manager")
        db.human_enqueue(ctx.tenant_id, ctx.customer_id, ctx.message,
                         "amount over auto-approve cap")
        return {"text": f"That amount is above what I can approve automatically "
                        f"({_money(_CAP)}). I've sent it for manager review; you'll hear back shortly.",
                "requires_confirmation": False}

    db.pending_put(ctx.session_id, ctx.tenant_id, ctx.customer_id,
                   action.kind, action.params, v)
    metrics.inc("writegate_staged")
    ctx.trace.add("guard", "write-gate: staged (awaiting confirm)", {"kind": action.kind})
    return {"text": _describe(action) + " All policy checks passed. Reply 'confirm' to proceed.",
            "requires_confirmation": True}


def _describe(a: ProposedAction) -> str:
    if a.kind == "refund":
        p = a.params
        return (f"I can apply a {_money(p['amount_cents'])} credit to {p['invoice_id']} "
                f"to correct the missed proration (billed {_money(p['billed_cents'])}, "
                f"should have been {_money(p['correct_cents'])}).")
    if a.kind == "cancel":
        return "I can cancel your subscription; billing stops at the end of the current cycle."
    return f"I can perform: {a.kind}."


# --- commit (only after an explicit confirm) ------------------------------
def commit_pending(ctx: Context) -> dict[str, Any]:
    p = db.pending_get(ctx.session_id)
    if not p:
        return {"text": "I don't have anything staged to confirm. What would you like to do?",
                "requires_confirmation": False}

    # Re-validate fresh at commit time — never trust the earlier check blindly.
    action = ProposedAction(p["kind"], p["params"])
    v = _validate(ctx, action)
    if not v["ok"] or v["requires_manager"]:
        db.pending_clear(ctx.session_id)
        metrics.inc("writegate_declined")
        ctx.trace.add("guard", "write-gate: re-validate failed at commit", {"checks": v["checks"]})
        return {"text": "Something changed and I can no longer approve this automatically. "
                        "I've routed it to a specialist.", "requires_confirmation": False}

    if action.kind == "refund":
        res = db.commit_refund(ctx.tenant_id, ctx.customer_id, p["params"]["invoice_id"],
                               p["params"]["amount_cents"], "proration correction")
        db.audit("writegate", "commit_refund", ctx.tenant_id, ctx.customer_id, p["params"])
        db.pending_clear(ctx.session_id)
        metrics.inc("writegate_committed")
        ctx.trace.add("guard", "write-gate: COMMIT refund", {"new_balance": res["new_balance_cents"]})
        return {"text": f"Done — applied a {_money(p['params']['amount_cents'])} credit to "
                        f"{p['params']['invoice_id']}. Your balance is now "
                        f"{_money(res['new_balance_cents'])}.",
                "requires_confirmation": False}

    if action.kind == "cancel":
        db.commit_cancellation(ctx.tenant_id, ctx.customer_id)
        db.audit("writegate", "commit_cancel", ctx.tenant_id, ctx.customer_id, {})
        db.pending_clear(ctx.session_id)
        metrics.inc("writegate_committed")
        ctx.trace.add("guard", "write-gate: COMMIT cancel", {})
        return {"text": "Your subscription is cancelled. Billing stops at the end of the "
                        "current cycle and there are no further charges.",
                "requires_confirmation": False}

    db.pending_clear(ctx.session_id)
    return {"text": "I couldn't complete that action.", "requires_confirmation": False}
