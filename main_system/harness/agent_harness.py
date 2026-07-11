"""Agent-local harness — the machinery around ONE Tier 3 agent.

It owns the loop, context assembly for each step, tool dispatch, and the budgets
(max iterations / tool calls). It holds no intelligence itself: every decision is
the model's; the harness executes, observes, and enforces limits. Tools and skills
are borrowed from the shared registries, not owned here.
"""
from __future__ import annotations

import json
from typing import Any

from main_system.config import CFG
from main_system.context import Context
from main_system.llm import gateway_client as gw
from main_system.tools import registry

_MAX_ITERS = CFG["agent"]["max_iterations"]
_MAX_TOOLS = CFG["agent"]["max_tool_calls"]

# Map an agent tool call to the key the planner tracks as "already done".
def _used_key(tool: str, args: dict[str, Any]) -> str:
    if tool == "record_lookup":
        return args.get("entity", "record")
    if tool == "knowledge_search":
        return "policy"
    return tool


class AgentHarness:
    def __init__(self, ctx: Context, goal: str):
        self.ctx = ctx
        self.goal = goal
        self.tools_used: list[str] = []
        self.observations: list[dict[str, Any]] = []
        self.tool_calls = 0

    def run(self) -> dict[str, Any]:
        self.ctx.trace.add("tier", "Tier 3 · agent-local harness",
                           {"goal": self.goal[:80], "budget_iters": _MAX_ITERS})

        for i in range(_MAX_ITERS):
            step = self._decide(i)

            if step.get("action") == "final":
                self.ctx.trace.add("tier", "agent:final",
                                   {"iterations": i + 1, "tools": self.tools_used})
                needs_approval = self._is_action_proposal(step)
                if needs_approval:
                    self.ctx.trace.add("guard", "approval checkpoint",
                                       {"reason": "agent proposed a fix"})
                return {"text": step.get("answer", ""),
                        "requires_confirmation": needs_approval,
                        "tools_used": self.tools_used}

            if self.tool_calls >= _MAX_TOOLS:
                break
            self._act(step)

        return {"text": "I gathered what I could but reached my step budget. "
                        "Escalating to a specialist with the details.",
                "requires_confirmation": False, "tools_used": self.tools_used}

    # --- one reasoning step (the model decides the next action) ----------
    def _decide(self, i: int) -> dict[str, Any]:
        payload = {"goal": self.goal, "tools_used": self.tools_used,
                   "observations": self.observations[-4:]}
        if self.ctx.memory is not None:
            payload["memory"] = self.ctx.memory.context_block()
        raw = gw.complete(
            tier=3, task="agent_step", trace=self.ctx.trace,
            system="You are a support investigation agent. Decide the next action. "
                   "Return JSON: {thought, action:'tool'|'final', tool, args, answer}.",
            prompt=self.goal, payload=payload)
        try:
            step = json.loads(raw)
        except json.JSONDecodeError:
            step = {"action": "final", "answer": raw}
        self.ctx.trace.add("tier", f"agent:step {i + 1}",
                           {"thought": step.get("thought"), "action": step.get("action")})
        return step

    # --- execute a tool the model asked for ------------------------------
    def _act(self, step: dict[str, Any]) -> None:
        tool = step.get("tool")
        args = step.get("args", {}) or {}
        try:
            result = registry.get(tool).call(self.ctx, 3, **args)
        except (KeyError, PermissionError, TypeError) as exc:
            result = {"error": str(exc)}
        self.tool_calls += 1
        self.tools_used.append(_used_key(tool, args))
        self.observations.append({"tool": tool, "args": args, "result": result})

    @staticmethod
    def _is_action_proposal(step: dict[str, Any]) -> bool:
        # Phase 4 wires the write-gate; for now a proposed fix asks for confirmation.
        return "proposed" in (step.get("answer", "").lower()) or "fix" in \
            (step.get("answer", "").lower())
