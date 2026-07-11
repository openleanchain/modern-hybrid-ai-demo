"""Skill registry — the reusable procedures and who shares them."""
from __future__ import annotations

from typing import Any

_CATALOG = [
    {"name": "classify_intent", "desc": "Intent + complexity (also routes)", "tiers": [2, 3]},
    {"name": "extract_entities", "desc": "Structured fields from text", "tiers": [2, 3]},
    {"name": "summarize_thread", "desc": "Condense conversation context", "tiers": [2, 3]},
    {"name": "draft_reply", "desc": "Grounded customer-facing reply", "tiers": [2, 3]},
]


def catalog() -> list[dict[str, Any]]:
    return _CATALOG
