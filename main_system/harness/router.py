"""Tier router — scores each request on complexity x risk, then dispatches.

Risk is detected by conservative deterministic rules (never left to a model).
Complexity uses the LLM classifier when available and heuristics when it isn't,
and fails safe: low confidence escalates a tier rather than guessing cheap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from main_system.config import CFG
from main_system.context import Context
from main_system.llm import gateway_client as gw
from main_system.skills import library as skills

_R = CFG["router"]
_HIGH = [re.compile(p, re.I) for p in _R["high_stakes_patterns"]]
_DET = {k: [re.compile(p, re.I) for p in v]
        for k, v in _R["deterministic_intents"].items()}
_T3 = [re.compile(p, re.I) for p in _R["complexity_heuristics"]["tier3_signals"]]
_LONG = _R["complexity_heuristics"]["long_request_words"]
_MIN_CONF = _R["min_classifier_confidence"]
_FASTPATH_MAX_WORDS = _R.get("fastpath_max_words", 9)


@dataclass
class Route:
    tier: int
    risk: str = "low"
    complexity: str = "simple"
    confidence: float = 1.0
    reason: str = ""
    intent: str = "general_support"
    det_intent: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)


def _match_any(patterns, text) -> bool:
    return any(p.search(text) for p in patterns)


def _det_intent(text: str) -> Optional[str]:
    for name, patterns in _DET.items():
        if _match_any(patterns, text):
            return name
    return None


def _looks_compound(text: str) -> bool:
    """A fast-path answer only fits a single crisp lookup. Compound or analytical
    phrasing ('why ...', '... and ...', long) should go to the classifier."""
    t = text.lower()
    return (" and " in t) or ("why" in t) or (len(t.split()) > _FASTPATH_MAX_WORDS)


def route(ctx: Context) -> Route:
    msg = ctx.message
    risk = "high" if _match_any(_HIGH, msg) else "low"
    det = _det_intent(msg)

    if risk == "high":
        r = Route(tier=1, risk="high", complexity="n/a", confidence=1.0,
                  det_intent=det, intent="account_change",
                  reason="high-stakes rule matched -> deterministic validated path")
    elif det and not _looks_compound(msg):
        r = Route(tier=1, risk="low", complexity="simple", confidence=1.0,
                  det_intent=det, intent=det,
                  reason=f"deterministic intent '{det}' -> Tier 1")
    else:
        r = _classify(ctx)

    ctx.trace.set_route(r.tier, r.risk, r.complexity, r.confidence, r.reason)
    return r


def _classify(ctx: Context) -> Route:
    """Non-deterministic path: use the classifier, fall back to heuristics."""
    try:
        cls = skills.classify_intent(ctx)
        complexity = cls.get("complexity", "simple")
        conf = float(cls.get("confidence", 0.5))
        intent = cls.get("intent", "general_support")
        tier = 3 if complexity == "complex" else 2
        reason = "classifier -> Tier " + str(tier)
        if conf < _MIN_CONF and tier < 3:      # fail safe: escalate on low confidence
            tier += 1
            reason = f"classifier low-confidence ({conf:.2f}) -> escalated to Tier {tier}"
        return Route(tier=tier, complexity=complexity, confidence=conf,
                     intent=intent, reason=reason, extras={"classified": cls})
    except gw.LLMUnavailable:
        complex_ = _match_any(_T3, ctx.message) or len(ctx.message.split()) > _LONG
        tier = 3 if complex_ else 2
        return Route(tier=tier, complexity="complex" if complex_ else "simple",
                     confidence=0.5, intent="unknown",
                     reason="classifier unavailable -> heuristic",
                     extras={"llm_down": True})
