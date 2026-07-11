"""Tier 2 — simple intelligent (single-shot LLM call).

Gathers grounding through the shared tools, then produces one drafted reply.
No agent loop: the shared skills and tools do the work, the model phrases once.
"""
from __future__ import annotations

from typing import Any

from main_system.context import Context
from main_system.harness.router import Route
from main_system.skills import library as skills
from main_system.tools import registry


def handle(ctx: Context, route: Route) -> dict[str, Any]:
    ctx.trace.add("tier", "Tier 2 · simple LLM", {"intent": route.intent})
    facts: dict[str, Any] = {}
    rl = registry.get("record_lookup")
    ks = registry.get("knowledge_search")

    intent = route.intent

    # Ground from the systems of record when the question is account-shaped.
    if intent in ("plan_info", "check_balance", "billing_question", "account_change"):
        acct = rl.call(ctx, 2, entity="account")
        if acct.get("found"):
            facts["plan"] = acct["plan_code"]
            facts["balance"] = acct["balance"]

    # Always ground on the knowledge base — this is where RAG earns its keep.
    kb = ks.call(ctx, 2, query=ctx.message)
    if kb.get("hits"):
        facts["policy"] = kb["hits"][0]["chunk"]
        facts["source"] = kb["hits"][0]["title"]
        facts["retrieval"] = kb["mode"]

    reply = skills.draft_reply(ctx, intent=intent, facts=facts, tier=2)
    return {"text": reply, "requires_confirmation": False}
