"""Deterministic, offline stand-in for the models.

Lets the entire system run and be tested with zero API cost. Each task returns
the same *shape* the real model is prompted to produce, so nothing downstream
has to know whether it's talking to a real model or the mock.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


# --- text completion by task ---------------------------------------------
def complete(task: str, payload: dict[str, Any]) -> str:
    fn = _TASKS.get(task, _generic)
    return fn(payload)


def _classify_intent(p: dict[str, Any]) -> str:
    text = (p.get("text") or "").lower()
    complex_signals = ["look wrong", "looks wrong", "investigate", "figure out",
                       "what happened", "three invoices", "3 invoices",
                       "last three", "compare", "propose a fix"]
    complexity = "complex" if any(s in text for s in complex_signals) else "simple"
    if "balance" in text or "how much" in text:
        intent = "check_balance"
    elif "plan" in text:
        intent = "plan_info"
    elif "invoice" in text or "bill" in text:
        intent = "billing_question"
    elif "refund" in text or "cancel" in text:
        intent = "account_change"
    elif "policy" in text or "refund policy" in text:
        intent = "policy_question"
    else:
        intent = "general_support"
    return json.dumps({"intent": intent, "complexity": complexity, "confidence": 0.82})


def _extract_entities(p: dict[str, Any]) -> str:
    text = p.get("text") or ""
    amounts = re.findall(r"\$\s?\d+(?:\.\d{2})?", text)
    ids = re.findall(r"\bINV-\d+\b|\b[A-Z]{2,}-\d+\b", text)
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b", text)
    return json.dumps({"amounts": amounts, "invoice_ids": ids, "dates": dates})


def _summarize_thread(p: dict[str, Any]) -> str:
    turns = p.get("turns") or []
    prior = (p.get("prior_summary") or "").strip()
    topics = []
    for t in turns:
        u = (t.get("user") or "").strip()
        if u:
            topics.append(u[:70])
    # Figures deliberately excluded — memory holds topics/intents, not values.
    money = re.compile(r"(USD|EUR|GBP|\$|€|£)\s?\d[\d,]*(\.\d{2})?", re.I)
    topics = [money.sub("[figure]", t) for t in topics]
    joined = "; ".join(topics)
    fresh = f"The customer discussed: {joined}." if joined else ""
    if prior and fresh:
        return f"{prior} Then, {fresh[0].lower()}{fresh[1:]}"
    return prior or fresh or "No prior conversation."


def _draft_reply(p: dict[str, Any]) -> str:
    facts = p.get("facts") or {}
    intent = p.get("intent") or "your request"
    lines = [f"Here's what I found regarding {intent.replace('_', ' ')}:"]
    for k, v in facts.items():
        lines.append(f"  - {k.replace('_', ' ')}: {v}")
    lines.append("Let me know if you'd like anything else.")
    return "\n".join(lines)


def _agent_step(p: dict[str, Any]) -> str:
    """Heuristic planner for the billing-investigation use case.

    Picks the next tool based on what's already been observed, then finalizes.
    A real gpt-4.1-mini does open-ended planning here; this keeps the demo's
    Tier 3 loop believable offline.
    """
    used = set(p.get("tools_used") or [])
    goal = (p.get("goal") or "").lower()

    if "invoices" not in used:
        return json.dumps({
            "thought": "Pull the customer's recent invoices to see what was billed.",
            "action": "tool", "tool": "record_lookup",
            "args": {"entity": "invoices", "limit": 3},
        })
    if "plan_changes" not in used:
        return json.dumps({
            "thought": "Check plan-change history — a mid-cycle change often explains odd amounts.",
            "action": "tool", "tool": "record_lookup",
            "args": {"entity": "plan_changes"},
        })
    if "policy" not in used:
        return json.dumps({
            "thought": "Confirm the proration rule in the billing policy.",
            "action": "tool", "tool": "knowledge_search",
            "args": {"query": "proration when plan changes mid cycle"},
        })
    return json.dumps({
        "thought": "I have invoices, plan history, and the policy. Compose the explanation and a proposed fix.",
        "action": "final",
        "answer": ("The billing anomaly lines up with a mid-cycle plan change: the "
                   "affected invoices were charged at the new plan rate for the whole "
                   "period instead of being prorated. Proposed fix: issue a prorated "
                   "credit for the overlap. (This is a proposal — it must pass the "
                   "refund-eligibility check and confirmation before anything commits.)"),
    })


def _generic(p: dict[str, Any]) -> str:
    prompt = p.get("prompt") or p.get("text") or ""
    return f"[mock] I understood: {prompt[:160]}"


_TASKS = {
    "classify_intent": _classify_intent,
    "extract_entities": _extract_entities,
    "summarize_thread": _summarize_thread,
    "draft_reply": _draft_reply,
    "agent_step": _agent_step,
    "generic": _generic,
}


# --- deterministic embeddings --------------------------------------------
def embed(text: str, dim: int) -> list[float]:
    """Hashing embedding over character trigrams. Deterministic and offline;
    similar strings land near each other, which is all RAG needs for the demo."""
    vec = [0.0] * dim
    t = text.lower()
    trigrams = [t[i:i + 3] for i in range(max(len(t) - 2, 1))]
    for g in trigrams:
        h = int(hashlib.md5(g.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h // dim) % dim] += 0.5
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]
