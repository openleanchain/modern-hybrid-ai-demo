"""The request context threaded through the harness, tiers, tools, and skills."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from main_system.observability.trace import Trace


@dataclass
class Context:
    tenant_id: str
    customer_id: str
    session_id: str
    message: str
    trace: Trace
    memory: Optional[Any] = None   # SessionMemory, attached by the enterprise harness
