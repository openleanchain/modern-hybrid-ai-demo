"""The always-on fallback ladder.

When the model is unreachable, the enterprise harness runs this instead of a tier.
Each rung is tried in order; the first that can answer wins, and the rung used is
recorded so the console can show how the answer was produced:

  1. cache     a recent good answer for this exact question
  2. rules     data questions answered from the record via Tier 1 (no model)
  3. template  policy answered from the knowledge base by keyword search (no model)
  4. human     queued for a specialist, with an honest message back

Rungs 2 and 3 need no model at all, which is why the system keeps serving real
answers through an outage rather than just apologising.
"""
from __future__ import annotations

import re
from typing import Any

from main_system.config import CFG
from main_system.context import Context
from main_system.db import database as db
from main_system.harness import router
from main_system.tiers import tier1_deterministic as tier1
from main_system.tools import knowledge_search

_CACHE_ENABLED = CFG["resilience"]["fallback"].get("cache_enabled", True)
_WS = re.compile(r"[^a-z0-9 ]+")


def normalize(msg: str) -> str:
    return _WS.sub(" ", (msg or "").lower()).strip()


def _wrap(text: str, rung: str, confirm: bool = False) -> dict[str, Any]:
    return {"text": text, "requires_confirmation": confirm, "fallback_rung": rung}


def run_ladder(ctx: Context, route: router.Route) -> dict[str, Any]:
    ctx.trace.add("fallback", "ladder engaged", {"reason": "LLM unavailable"})

    # 1) cache — a recent good answer for this exact question
    if _CACHE_ENABLED:
        hit = db.cache_get(ctx.tenant_id, ctx.customer_id, normalize(ctx.message))
        if hit:
            ctx.trace.add("fallback", "cache hit", {"cached_tier": hit["tier"]})
            return _wrap(hit["answer"] +
                         "\n\n(Served from a recent cached answer while the assistant is offline.)",
                         rung="cache")

    # 2) rules — data questions answered straight from the record, no model
    det = router._det_intent(ctx.message)
    if det in ("balance", "plan", "invoice", "payment"):
        ctx.trace.add("fallback", "rules (record_lookup)", {"intent": det})
        res = tier1.handle(ctx, router.Route(tier=1, det_intent=det))
        return _wrap(res["text"], rung="rules",
                     confirm=res.get("requires_confirmation", False))

    # 3) template — policy from the knowledge base by keyword search, no model
    kb = knowledge_search.run(ctx, query=ctx.message)      # embed fast-fails -> FTS
    if kb.get("hits"):
        top = kb["hits"][0]
        ctx.trace.add("fallback", "template (KB keyword)",
                      {"doc": top["doc_id"], "retrieval": kb["mode"]})
        return _wrap(
            f"While the assistant is offline, here's the most relevant policy "
            f"({top['title']}): {top['chunk']} A specialist can follow up if you need more.",
            rung="template")

    # 4) human — queue it, and say so honestly
    db.human_enqueue(ctx.tenant_id, ctx.customer_id, ctx.message, "no deterministic answer")
    ctx.trace.add("fallback", "human handoff", {"queued": True})
    return _wrap(
        "The assistant is temporarily unavailable and I couldn't answer this from "
        "your records. I've queued it for a specialist, who will follow up shortly.",
        rung="human")
