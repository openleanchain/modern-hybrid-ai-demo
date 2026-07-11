"""Shared skills — reusable LLM procedures, defined once, used by Tier 2 and 3.

A skill wraps a model call plus its prompt and output parsing. Skills raise
LLMUnavailable up to the caller, which decides how to degrade.
"""
from __future__ import annotations

import json
from typing import Any

from main_system.context import Context
from main_system.llm import gateway_client as gw


def _json(text: str, default: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def classify_intent(ctx: Context) -> dict[str, Any]:
    """Intent + complexity for routing. Also the harness's routing classifier."""
    with ctx.trace.timed("skill", "classify_intent") as info:
        text = gw.complete(
            tier=2, task="classify_intent", trace=ctx.trace, text=ctx.message,
            system="Classify the support request. Return JSON with keys "
                   "intent, complexity (simple|complex), confidence (0-1).",
            prompt=ctx.message)
        out = _json(text, {"intent": "general_support",
                           "complexity": "simple", "confidence": 0.5})
        info["detail"] = out
    return out


def extract_entities(ctx: Context, text: str | None = None) -> dict[str, Any]:
    src = text if text is not None else ctx.message
    with ctx.trace.timed("skill", "extract_entities") as info:
        raw = gw.complete(
            tier=2, task="extract_entities", trace=ctx.trace, text=src,
            system="Extract amounts, invoice_ids, dates. Return JSON.",
            prompt=src)
        out = _json(raw, {"amounts": [], "invoice_ids": [], "dates": []})
        info["detail"] = out
    return out


def summarize_thread(ctx: Context, turns: list[dict[str, Any]],
                     prior_summary: str = "") -> str:
    with ctx.trace.timed("skill", "summarize_thread"):
        return gw.complete(
            tier=2, task="summarize_thread", trace=ctx.trace,
            system="Fold the earlier summary and these turns into one updated "
                   "summary in 1-3 sentences. Keep intents and references; do NOT "
                   "record live figures like balances or amounts.",
            prompt="", payload={"turns": turns, "prior_summary": prior_summary})


def draft_reply(ctx: Context, intent: str, facts: dict[str, Any], tier: int = 2) -> str:
    payload = {"intent": intent, "facts": facts}
    if ctx.memory is not None:                     # carry conversational context
        payload["memory"] = ctx.memory.context_block()
    with ctx.trace.timed("skill", "draft_reply", {"tier": tier}):
        return gw.complete(
            tier=tier, task="draft_reply", trace=ctx.trace,
            system="Write a concise, friendly reply using ONLY the given facts. "
                   "Never invent numbers.",
            prompt="", payload=payload)
