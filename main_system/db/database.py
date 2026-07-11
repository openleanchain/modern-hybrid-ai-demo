"""SQLite access for the main system.

Holds the systems-of-record, the knowledge base + its vectors, session memory,
and the audit trail. sqlite-vec provides KNN; if the extension can't load we fall
back to an in-Python cosine scan so the demo runs anywhere. Either way the
vectors live inside SQLite.
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import threading
from datetime import datetime, timezone
from typing import Any, Optional

_HERE = os.path.dirname(__file__)
DB_PATH = os.path.join(_HERE, "..", "..", "data", "hybrid.db")
SCHEMA_PATH = os.path.join(_HERE, "schema.sql")
REQUIRED_TABLES = (
    "tenants",
    "customers",
    "accounts",
    "plans",
    "plan_changes",
    "invoices",
    "payments",
    "kb_docs",
    "kb_fts",
    "kb_vec",
    "sessions",
    "audit_log",
    "response_cache",
    "human_queue",
    "pending_actions",
    "adjustments",
)

# Thread-local connections: the Flask dev server is threaded and SQLite
# connections are not shareable across threads.
_local = threading.local()
_VEC_OK = False  # whether sqlite-vec loaded (set on first connect)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _try_load_vec(conn)
    _local.conn = conn
    return conn


def _try_load_vec(conn: sqlite3.Connection) -> None:
    global _VEC_OK
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _VEC_OK = True
    except Exception:
        _VEC_OK = False


def vec_available() -> bool:
    return _VEC_OK


def schema_ready() -> bool:
    """Return whether the demo database has the tables the app expects."""
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        return False

    try:
        placeholders = ",".join("?" for _ in REQUIRED_TABLES)
        rows = get_conn().execute(
            f"SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table') "
            f"AND name IN ({placeholders})",
            REQUIRED_TABLES,
        ).fetchall()
    except sqlite3.DatabaseError:
        return False

    found = {r["name"] for r in rows}
    return all(table in found for table in REQUIRED_TABLES)


# --- pack helpers for float32 vectors -------------------------------------
def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def init_db(embed_dim: int) -> None:
    """Create schema and (if available) the vec0 virtual table."""
    conn = get_conn()
    with open(SCHEMA_PATH) as fh:
        conn.executescript(fh.read())
    if _VEC_OK:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS kb_vec "
            f"USING vec0(doc_id TEXT PARTITION KEY, tenant_id TEXT, "
            f"embedding float[{embed_dim}])"
        )
    else:
        # Plain table holding the raw vector blob for the Python cosine path.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_vec "
            "(doc_id TEXT, tenant_id TEXT, embedding BLOB)"
        )
    conn.commit()


def store_kb_vector(doc_id: str, tenant_id: str, embedding: list[float]) -> None:
    conn = get_conn()
    if _VEC_OK:
        conn.execute(
            "INSERT INTO kb_vec (doc_id, tenant_id, embedding) VALUES (?, ?, ?)",
            (doc_id, tenant_id, _pack(embedding)),
        )
    else:
        conn.execute(
            "INSERT INTO kb_vec (doc_id, tenant_id, embedding) VALUES (?, ?, ?)",
            (doc_id, tenant_id, _pack(embedding)),
        )
    conn.commit()


def knn_search(tenant_id: str, query_vec: list[float], k: int) -> list[dict[str, Any]]:
    """Vector search scoped to a tenant. Returns doc rows with a distance."""
    conn = get_conn()
    if _VEC_OK:
        rows = conn.execute(
            "SELECT doc_id, distance FROM kb_vec "
            "WHERE embedding MATCH ? AND tenant_id = ? "
            "AND k = ? ORDER BY distance",
            (_pack(query_vec), tenant_id, k),
        ).fetchall()
        hits = [(r["doc_id"], r["distance"]) for r in rows]
    else:
        import numpy as np

        q = np.asarray(query_vec, dtype="float32")
        qn = q / (np.linalg.norm(q) + 1e-9)
        scored = []
        for r in conn.execute(
            "SELECT doc_id, embedding FROM kb_vec WHERE tenant_id = ?", (tenant_id,)
        ):
            v = np.asarray(_unpack(r["embedding"]), dtype="float32")
            vn = v / (np.linalg.norm(v) + 1e-9)
            scored.append((r["doc_id"], 1.0 - float(qn @ vn)))
        scored.sort(key=lambda x: x[1])
        hits = scored[:k]

    out = []
    for doc_id, dist in hits:
        doc = conn.execute(
            "SELECT doc_id, title, chunk FROM kb_docs WHERE tenant_id = ? AND doc_id = ?",
            (tenant_id, doc_id),
        ).fetchone()
        if doc:
            out.append({"doc_id": doc["doc_id"], "title": doc["title"],
                        "chunk": doc["chunk"], "distance": round(dist, 4)})
    return out


def fts_search(tenant_id: str, query: str, k: int) -> list[dict[str, Any]]:
    """Keyword fallback for knowledge_search when the embedder is unavailable."""
    conn = get_conn()
    # Sanitize into an OR query of bare terms (avoids FTS5 syntax errors).
    terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 2]
    if not terms:
        return []
    match = " OR ".join(terms)
    rows = conn.execute(
        "SELECT doc_id, title, chunk FROM kb_fts "
        "WHERE kb_fts MATCH ? AND tenant_id = ? LIMIT ?",
        (match, tenant_id, k),
    ).fetchall()
    return [{"doc_id": r["doc_id"], "title": r["title"], "chunk": r["chunk"],
             "distance": None} for r in rows]


# --- audit ----------------------------------------------------------------
def audit(actor: str, action: str, tenant_id: Optional[str] = None,
          customer_id: Optional[str] = None, detail: Any = None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_log (ts, tenant_id, customer_id, actor, action, detail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_now(), tenant_id, customer_id, actor, action,
         json.dumps(detail) if detail is not None else None),
    )
    conn.commit()


def query_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
    row = get_conn().execute(sql, params).fetchone()
    return dict(row) if row else None


# --- always-on: response cache + human queue ------------------------------
def cache_put(tenant_id: str, customer_id: str, query_norm: str,
              answer: str, tier: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO response_cache (tenant_id, customer_id, query_norm, answer, tier, ts) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(tenant_id, customer_id, query_norm) "
        "DO UPDATE SET answer=excluded.answer, tier=excluded.tier, ts=excluded.ts",
        (tenant_id, customer_id, query_norm, answer, tier, _now()))
    conn.commit()


def cache_get(tenant_id: str, customer_id: str, query_norm: str) -> Optional[dict[str, Any]]:
    return query_one(
        "SELECT answer, tier, ts FROM response_cache "
        "WHERE tenant_id=? AND customer_id=? AND query_norm=?",
        (tenant_id, customer_id, query_norm))


def human_enqueue(tenant_id: str, customer_id: str, message: str, reason: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO human_queue (ts, tenant_id, customer_id, message, reason) "
        "VALUES (?,?,?,?,?)", (_now(), tenant_id, customer_id, message, reason))
    conn.commit()


def human_queue_depth() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM human_queue WHERE status='open'")
    return row["n"] if row else 0


# --- write-gate: pending proposals + committed adjustments -----------------
def pending_put(session_id: str, tenant_id: str, customer_id: str, kind: str,
                params: dict, validation: dict) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO pending_actions (session_id, tenant_id, customer_id, kind, "
        "params, validation, created_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(session_id) DO UPDATE SET kind=excluded.kind, "
        "params=excluded.params, validation=excluded.validation, "
        "created_at=excluded.created_at",
        (session_id, tenant_id, customer_id, kind, json.dumps(params),
         json.dumps(validation), _now()))
    conn.commit()


def pending_get(session_id: str) -> Optional[dict[str, Any]]:
    row = query_one("SELECT * FROM pending_actions WHERE session_id=?", (session_id,))
    if not row:
        return None
    row["params"] = json.loads(row["params"])
    row["validation"] = json.loads(row["validation"])
    return row


def pending_clear(session_id: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM pending_actions WHERE session_id=?", (session_id,))
    conn.commit()


def commit_refund(tenant_id: str, customer_id: str, invoice_id: str,
                  amount_cents: int, note: str) -> dict[str, Any]:
    """Apply a credit and reduce the balance in a single transaction."""
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO adjustments (ts, tenant_id, customer_id, kind, invoice_id, "
            "amount_cents, note) VALUES (?,?,?,?,?,?,?)",
            (_now(), tenant_id, customer_id, "refund_credit", invoice_id, amount_cents, note))
        conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents - ? "
            "WHERE tenant_id=? AND customer_id=?", (amount_cents, tenant_id, customer_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    new = query_one("SELECT balance_cents FROM accounts WHERE tenant_id=? AND customer_id=?",
                    (tenant_id, customer_id))
    return {"new_balance_cents": new["balance_cents"] if new else None}


def commit_cancellation(tenant_id: str, customer_id: str) -> dict[str, Any]:
    conn = get_conn()
    conn.execute("UPDATE accounts SET status='cancelled' WHERE tenant_id=? AND customer_id=?",
                 (tenant_id, customer_id))
    conn.commit()
    return {"status": "cancelled"}
