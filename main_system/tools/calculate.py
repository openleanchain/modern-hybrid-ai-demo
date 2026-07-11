"""calculate — deterministic arithmetic, shared by Tier 2 and Tier 3.

Keeps numbers out of the model. Supports a few named operations rather than a
free-form eval, so results are exact and auditable.
"""
from __future__ import annotations

from typing import Any

from main_system.context import Context
from main_system.db import database as db


def run(ctx: Context, op: str, **args: Any) -> dict[str, Any]:
    db.audit("tool:calculate", op, ctx.tenant_id, ctx.customer_id, args or None)

    if op == "sum":
        values = [float(v) for v in args.get("values", [])]
        return {"op": op, "result": round(sum(values), 2)}

    if op == "diff":
        return {"op": op, "result": round(float(args["a"]) - float(args["b"]), 2)}

    if op == "prorate":
        # amount owed for days_used out of days_total at a monthly rate
        monthly = float(args["monthly"])
        used = float(args["days_used"])
        total = float(args["days_total"]) or 1.0
        return {"op": op, "result": round(monthly * used / total, 2)}

    return {"op": op, "error": "unknown operation"}
