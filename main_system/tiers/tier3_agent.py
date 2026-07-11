"""Tier 3 — complex agent workflows. Wraps one request in an agent-local harness."""
from __future__ import annotations

from typing import Any

from main_system.context import Context
from main_system.harness.agent_harness import AgentHarness
from main_system.harness.router import Route


def handle(ctx: Context, route: Route) -> dict[str, Any]:
    return AgentHarness(ctx, goal=ctx.message).run()
