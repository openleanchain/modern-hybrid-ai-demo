"""knowledge_search — grounding shared by Tier 2 and Tier 3.

Embeds the query via the LLM service and does KNN in sqlite-vec. If the embedder
is unavailable, it degrades to SQLite FTS5 keyword search so grounding still works
during an outage.
"""
from __future__ import annotations

from typing import Any

from main_system.config import CFG
from main_system.context import Context
from main_system.db import database as db
from main_system.llm import gateway_client as gw

_TOP_K = CFG["retrieval"]["top_k"]


def run(ctx: Context, query: str, **_: Any) -> dict[str, Any]:
    db.audit("tool:knowledge_search", "search", ctx.tenant_id, ctx.customer_id,
             {"query": query})
    try:
        vec = gw.embed([query], trace=ctx.trace)[0]
        hits = db.knn_search(ctx.tenant_id, vec, _TOP_K)
        mode = "vector"
    except gw.LLMUnavailable:
        # Embedder down — keyword grounding keeps working.
        hits = db.fts_search(ctx.tenant_id, query, _TOP_K)
        mode = "keyword_fallback"
        ctx.trace.add("fallback", "knowledge_search:fts",
                      {"reason": "embedder unavailable"}, 0.0)
    return {"mode": mode, "hits": hits}
