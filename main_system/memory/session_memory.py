"""Session memory — a Tiered Batched Rolling Summary.

Design, with the three fixes we set out to make:

1. Count TURNS, not messages. A turn is one user + one assistant exchange.
   Compression triggers on the turn count, so a chatty single reply never skews it.
2. Never fold authoritative figures into the durable summary. The summary holds
   what was *discussed* (intents, references), not live values like balances —
   those are always re-fetched from the systems of record via record_lookup.
3. Checkpoint every turn. The raw turn is persisted the moment it happens, before
   any compression runs, so a crash (or an LLM outage mid-compression) never loses
   the conversation.

Durable state is two fields on the `sessions` row: a running_summary string and a
raw_turns JSON buffer. Everything else is runtime detail for the inspector.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from main_system.config import CFG
from main_system.db import database as db

_MAX_RAW = int(CFG["memory"]["max_raw_turns"])
_BATCH = int(CFG["memory"]["batch_compress"])
_SUMMARY_CAP = int(CFG["memory"].get("summary_max_chars", 800))

# Belt-and-braces: strip currency figures if a summary ever picks them up.
_MONEY = re.compile(r"(USD|EUR|GBP|\$|€|£)\s?\d[\d,]*(\.\d{2})?", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strip_figures(text: str) -> str:
    return _MONEY.sub("[figure omitted]", text or "")


class SessionMemory:
    def __init__(self, session_id: str, tenant_id: str, customer_id: str,
                 running_summary: str = "", raw_turns: Optional[list] = None):
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.customer_id = customer_id
        self.running_summary = running_summary
        self.raw_turns: list[dict[str, Any]] = raw_turns or []
        self.last_compression: Optional[dict[str, Any]] = None

    # --- load / persist ---------------------------------------------------
    @classmethod
    def load(cls, session_id: str, tenant_id: str, customer_id: str) -> "SessionMemory":
        row = db.query_one("SELECT running_summary, raw_turns FROM sessions "
                           "WHERE session_id = ?", (session_id,))
        if not row:
            return cls(session_id, tenant_id, customer_id)
        try:
            turns = json.loads(row["raw_turns"])
        except (json.JSONDecodeError, TypeError):
            turns = []
        return cls(session_id, tenant_id, customer_id,
                   running_summary=row["running_summary"] or "", raw_turns=turns)

    def checkpoint(self) -> None:
        """Persist current state. Cheap and called after every turn (fix #3)."""
        db.get_conn().execute(
            "INSERT INTO sessions (session_id, tenant_id, customer_id, "
            "running_summary, raw_turns, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET running_summary=excluded.running_summary, "
            "raw_turns=excluded.raw_turns, updated_at=excluded.updated_at",
            (self.session_id, self.tenant_id, self.customer_id,
             self.running_summary, json.dumps(self.raw_turns), _now()))
        db.get_conn().commit()

    # --- the write path ---------------------------------------------------
    def append_turn(self, ctx, user_msg: str, assistant_msg: str) -> None:
        self.raw_turns.append({"user": user_msg, "assistant": assistant_msg, "ts": _now()})
        self.checkpoint()                                   # fix #3: persist first
        ctx.trace.add("memory", "turn recorded",
                      {"raw_turns": len(self.raw_turns)})

        if len(self.raw_turns) >= _MAX_RAW:                 # fix #1: count turns
            self._compress(ctx)

    def _compress(self, ctx) -> None:
        from main_system.llm.gateway_client import LLMUnavailable
        from main_system.skills import library as skills

        batch = self.raw_turns[:_BATCH]
        try:
            new_summary = skills.summarize_thread(ctx, batch, prior_summary=self.running_summary)
        except LLMUnavailable:
            # Compression deferred; raw turns stay (already checkpointed). No loss.
            ctx.trace.add("memory", "compression deferred (LLM down)", {})
            return

        self.running_summary = _strip_figures(new_summary)[:_SUMMARY_CAP]  # fix #2
        self.raw_turns = self.raw_turns[_BATCH:]
        self.last_compression = {"folded": len(batch), "kept": len(self.raw_turns)}
        self.checkpoint()
        ctx.trace.add("memory", "compressed batch -> summary",
                      {"folded": len(batch), "raw_turns": len(self.raw_turns)})

    # --- the read path ----------------------------------------------------
    def context_block(self, recent: int = 3) -> dict[str, Any]:
        """What a tier injects into a prompt: the summary plus the last few turns.
        Deliberately carries no authoritative figures."""
        return {"summary": self.running_summary,
                "recent_turns": self.raw_turns[-recent:]}

    def snapshot(self) -> dict[str, Any]:
        """For the inspector panel."""
        return {
            "summary": self.running_summary,
            "raw_turns": [{"user": t["user"], "assistant": t["assistant"]}
                          for t in self.raw_turns],
            "raw_turn_count": len(self.raw_turns),
            "max_raw": _MAX_RAW, "batch": _BATCH,
            "compressed_this_turn": self.last_compression,
            "has_summary": bool(self.running_summary),
        }
