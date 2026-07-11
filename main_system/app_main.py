"""Main system — Flask app hosting the console and the chat API.

Run:
    python -m main_system.app_main   # serves on http://127.0.0.1:5000
    python main_system/app_main.py   # direct script launch also works
"""
from __future__ import annotations

import os
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import ServiceUnavailable

from main_system.config import CFG
from main_system.db import database as db
from main_system.harness import enterprise_harness
from main_system.llm import gateway_client as gw
from main_system.skills import registry as skill_registry
from main_system.tools import registry as tool_registry

_LLM_URL = CFG["services"]["llm_service_url"]

app = Flask(__name__)

# Warm a connection so sqlite-vec loads in this process and /api/health is accurate.
try:
    if db.schema_ready():
        db.get_conn()
except Exception:
    pass


def _require_db() -> None:
    if not db.schema_ready():
        raise ServiceUnavailable(
            "Database is not initialized. Run: python -m main_system.db.seed"
        )


def _personas() -> list[dict]:
    _require_db()
    rows = db.query_all(
        "SELECT c.tenant_id, c.customer_id, c.name, t.name AS tenant_name "
        "FROM customers c JOIN tenants t ON t.tenant_id = c.tenant_id "
        "ORDER BY c.tenant_id, c.customer_id")
    return rows


@app.get("/")
def index():
    return render_template(
        "index.html",
        personas=_personas(),
        tools=tool_registry.catalog(),
        skills=skill_registry.catalog(),
    )


@app.get("/api/health")
def health():
    h = gw.health()
    queue_depth = db.human_queue_depth() if db.schema_ready() else None
    return jsonify(llm=h, vec="sqlite-vec" if db.vec_available() else "python-fallback",
                   resilience={"breaker": gw.breaker_state(),
                               "human_queue_depth": queue_depth},
                   db_ready=db.schema_ready())


@app.get("/api/resilience")
def resilience():
    _require_db()
    return jsonify(breaker=gw.breaker_state(), human_queue_depth=db.human_queue_depth(),
                   queue=db.query_all("SELECT ts, customer_id, message, reason FROM "
                                      "human_queue WHERE status='open' ORDER BY id DESC LIMIT 10"))


@app.get("/api/metrics")
def metrics_endpoint():
    from main_system.observability import metrics
    return jsonify(metrics=metrics.snapshot())


@app.post("/api/chaos")
def chaos():
    """Trip or restore the LLM service outage from the console."""
    down = bool((request.get_json(silent=True) or {}).get("down", True))
    try:
        r = requests.post(f"{_LLM_URL}/admin/chaos", json={"down": down}, timeout=3)
        return jsonify(ok=True, down=r.json().get("down"))
    except requests.RequestException as exc:
        # If the process itself is stopped we can't reach it — that's also a valid
        # way to simulate an outage; report it rather than failing.
        return jsonify(ok=False, down=down, error=str(exc)), 502


@app.get("/api/memory/<session_id>")
def memory(session_id: str):
    _require_db()
    row = db.query_one("SELECT tenant_id, customer_id FROM sessions "
                       "WHERE session_id = ?", (session_id,))
    if not row:
        return jsonify(exists=False)
    from main_system.memory.session_memory import SessionMemory
    mem = SessionMemory.load(session_id, row["tenant_id"], row["customer_id"])
    return jsonify(exists=True, memory=mem.snapshot())


@app.post("/api/chat")
def chat():
    _require_db()
    body = request.get_json(force=True)
    tenant = body.get("tenant_id")
    customer = body.get("customer_id")
    message = (body.get("message") or "").strip()
    session = body.get("session_id") or f"sess-{uuid.uuid4().hex[:8]}"
    if not (tenant and customer and message):
        return jsonify(error="tenant_id, customer_id and message are required"), 400

    result = enterprise_harness.handle_message(tenant, customer, session, message)
    result["session_id"] = session
    return jsonify(result)


if __name__ == "__main__":
    if not db.schema_ready():
        print("! Database not found. Run:  python -m main_system.db.seed")
    port = int(os.environ.get("MAIN_PORT", "5000"))
    print(f"[main_system] console on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
