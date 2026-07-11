"""record_lookup — the one tool every tier shares.

Read-only, tenant-scoped, audit-logged access to the systems of record. Tier 1
calls it directly for an exact answer; Tier 2 calls it to ground a reply; the
Tier 3 agent calls it as a tool. Facts come from here — never from the model.
"""
from __future__ import annotations

from typing import Any

from main_system.context import Context
from main_system.db import database as db


def _money(cents: int, currency: str = "USD") -> str:
    return f"{currency} {cents / 100:,.2f}"


def run(ctx: Context, entity: str, **args: Any) -> dict[str, Any]:
    t, c = ctx.tenant_id, ctx.customer_id
    db.audit("tool:record_lookup", entity, t, c, args or None)

    if entity in ("account", "balance"):
        row = db.query_one(
            "SELECT plan_code, balance_cents, currency, status FROM accounts "
            "WHERE tenant_id = ? AND customer_id = ?", (t, c))
        if not row:
            return {"found": False}
        return {"found": True, "balance": _money(row["balance_cents"], row["currency"]),
                "balance_cents": row["balance_cents"], "plan_code": row["plan_code"],
                "status": row["status"]}

    if entity == "plan":
        row = db.query_one(
            "SELECT p.plan_code, p.plan_name, p.monthly_cents FROM accounts a "
            "JOIN plans p ON p.tenant_id = a.tenant_id AND p.plan_code = a.plan_code "
            "WHERE a.tenant_id = ? AND a.customer_id = ?", (t, c))
        if not row:
            return {"found": False}
        return {"found": True, "plan_code": row["plan_code"], "plan_name": row["plan_name"],
                "monthly": _money(row["monthly_cents"])}

    if entity == "invoices":
        limit = int(args.get("limit", 5))
        rows = db.query_all(
            "SELECT invoice_id, issued_on, period, amount_cents, status, note "
            "FROM invoices WHERE tenant_id = ? AND customer_id = ? "
            "ORDER BY issued_on DESC LIMIT ?", (t, c, limit))
        return {"found": bool(rows), "invoices": [
            {"invoice_id": r["invoice_id"], "issued_on": r["issued_on"],
             "period": r["period"], "amount": _money(r["amount_cents"]),
             "amount_cents": r["amount_cents"], "status": r["status"],
             "note": r["note"]} for r in rows]}

    if entity == "invoice":
        row = db.query_one(
            "SELECT invoice_id, issued_on, period, amount_cents, status, note "
            "FROM invoices WHERE tenant_id = ? AND customer_id = ? AND invoice_id = ?",
            (t, c, args.get("invoice_id")))
        if not row:
            return {"found": False}
        return {"found": True, "invoice_id": row["invoice_id"], "period": row["period"],
                "amount": _money(row["amount_cents"]), "amount_cents": row["amount_cents"],
                "status": row["status"], "note": row["note"]}

    if entity == "payments":
        rows = db.query_all(
            "SELECT payment_id, paid_on, amount_cents, method FROM payments "
            "WHERE tenant_id = ? AND customer_id = ? ORDER BY paid_on DESC LIMIT ?",
            (t, c, int(args.get("limit", 5))))
        return {"found": bool(rows), "payments": [
            {"payment_id": r["payment_id"], "paid_on": r["paid_on"],
             "amount": _money(r["amount_cents"]), "method": r["method"]} for r in rows]}

    if entity == "plan_changes":
        rows = db.query_all(
            "SELECT changed_on, from_plan, to_plan, note FROM plan_changes "
            "WHERE tenant_id = ? AND customer_id = ? ORDER BY changed_on DESC", (t, c))
        return {"found": bool(rows), "plan_changes": rows}

    return {"found": False, "error": f"unknown entity '{entity}'"}
