"""Tool registry — one governed catalog, reused across tiers.

`allowed_tiers` records least-privilege scope (which tier may call a tool). The
scope is recorded now and enforced by the write-gate in Phase 4; record_lookup is
the single tool available to all three tiers.
"""
from __future__ import annotations

from typing import Any, Callable

from main_system.context import Context
from main_system.tools import calculate, knowledge_search, record_lookup


class Tool:
    def __init__(self, name: str, fn: Callable, allowed_tiers: set[int], desc: str):
        self.name = name
        self.fn = fn
        self.allowed_tiers = allowed_tiers
        self.desc = desc

    def call(self, ctx: Context, tier: int, **args: Any) -> dict[str, Any]:
        if tier not in self.allowed_tiers:
            raise PermissionError(f"tier {tier} may not call tool '{self.name}'")
        with ctx.trace.timed("tool", self.name, {"args": args}) as info:
            result = self.fn(ctx, **args)
            info["detail"] = {"args": args, "result_keys": list(result.keys())}
        return result


_TOOLS: dict[str, Tool] = {
    "record_lookup": Tool("record_lookup", record_lookup.run, {1, 2, 3},
                          "Read the systems of record (accounts, invoices, plans)."),
    "knowledge_search": Tool("knowledge_search", knowledge_search.run, {2, 3},
                             "Grounded search over the policy knowledge base."),
    "calculate": Tool("calculate", calculate.run, {2, 3},
                      "Deterministic arithmetic (sum, diff, prorate)."),
}


def get(name: str) -> Tool:
    return _TOOLS[name]


def tools_for_tier(tier: int) -> list[Tool]:
    return [t for t in _TOOLS.values() if tier in t.allowed_tiers]


def catalog() -> list[dict[str, Any]]:
    return [{"name": t.name, "desc": t.desc, "tiers": sorted(t.allowed_tiers)}
            for t in _TOOLS.values()]
