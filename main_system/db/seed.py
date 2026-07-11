"""Seed the systems of record and the knowledge base.

Run after the LLM service is up (embeddings are fetched through it). If the
service is unreachable, embeddings fall back to a local hashing method so seeding
never hard-fails. Re-seed when you switch LLM modes so KB and query embeddings
match.

    python -m main_system.db.seed
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from main_system.config import CFG
from main_system.db import database as db

EMBED_DIM = int(CFG["llm"]["embed_dim"])

# Dates are relative to today so the refund-window logic works whenever you run it.
TODAY = date.today()
def _ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()
def _period(n: int) -> str:
    return (TODAY - timedelta(days=n)).strftime("%b %Y")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


TENANTS = [("org_a", "Northwind SaaS"), ("org_b", "Contoso Media")]

CUSTOMERS = [
    ("org_a", "CUST-1001", "Ada Lovelace", "ada@northwind.example", "2023-06-01"),
    ("org_a", "CUST-1002", "Alan Turing", "alan@northwind.example", "2024-01-15"),
    ("org_b", "CUST-2001", "Grace Hopper", "grace@contoso.example", "2022-11-20"),
]

PLANS = [
    ("org_a", "STD", "Standard", 3000),
    ("org_a", "PRO", "Professional", 6000),
    ("org_b", "BASIC", "Basic", 2500),
]

# Ada was upgraded mid-cycle; her latest invoice was billed full PRO instead of prorated.
ACCOUNTS = [
    ("org_a", "CUST-1001", "PRO", 6000, "USD", "active"),    # one open PRO invoice (overcharged)
    ("org_a", "CUST-1002", "STD", 0, "USD", "active"),       # clean account
    ("org_b", "CUST-2001", "BASIC", 2500, "USD", "active"),
]

PLAN_CHANGES = [
    ("org_a", "CUST-1001", _ago(18), "STD", "PRO", "Mid-cycle upgrade to Professional"),
]

INVOICES = [
    # Ada — three recent invoices; the latest is the overcharged one
    ("org_a", "INV-1000", "CUST-1001", _ago(78), _period(78), 3000, "paid", "Standard plan"),
    ("org_a", "INV-1001", "CUST-1001", _ago(48), _period(48), 3000, "paid", "Standard plan"),
    ("org_a", "INV-1002", "CUST-1001", _ago(18), _period(18), 6000, "open",
     "Billed full Professional rate; mid-cycle upgrade not prorated"),
    # Alan — clean
    ("org_a", "INV-1010", "CUST-1002", _ago(15), _period(15), 3000, "paid", "Standard plan"),
    # Grace (other tenant)
    ("org_b", "INV-2001", "CUST-2001", _ago(15), _period(15), 2500, "open", "Basic plan"),
]

PAYMENTS = [
    ("org_a", "PAY-1000", "CUST-1001", _ago(76), 3000, "card"),
    ("org_a", "PAY-1001", "CUST-1001", _ago(46), 3000, "card"),
    ("org_a", "PAY-1010", "CUST-1002", _ago(13), 3000, "card"),
]

KB = [
    ("org_a", "refund-policy", "Refund Policy",
     "Refunds are available within 30 days of a charge. Overcharges caused by "
     "billing errors, including missed proration, are refunded in full as account "
     "credit and require a manager review above USD 100."),
    ("org_a", "proration-policy", "Proration Policy",
     "When a plan changes mid-cycle, the invoice for that cycle must be prorated: "
     "the old plan rate applies for days before the change and the new plan rate "
     "for days on and after it. A full new-plan charge for the whole cycle is a "
     "billing error."),
    ("org_a", "cancellation-policy", "Cancellation Policy",
     "Customers may cancel at any time. Cancellation stops future billing at the "
     "end of the current cycle; no partial-month refunds are issued on cancellation "
     "except where required by law."),
    ("org_a", "billing-faq", "Billing FAQ",
     "Invoices are issued on the first of each month. Balances reflect unpaid "
     "invoices. Payment methods on file are charged automatically unless autopay "
     "is disabled."),
    ("org_b", "refund-policy", "Refund Policy",
     "Contoso Media refunds within 14 days of purchase. Credits are applied to the "
     "next invoice."),
    ("org_b", "billing-faq", "Billing FAQ",
     "Contoso Media bills monthly on the anniversary of signup."),
]


def _embed(texts: list[str]) -> list[list[float]]:
    from main_system.llm import gateway_client as gw
    try:
        return gw.embed(texts)
    except gw.LLMUnavailable:
        from llm_service.mock_brain import embed as local_embed
        print("  ! LLM service unreachable — using local hashing embeddings.")
        return [local_embed(t, EMBED_DIM) for t in texts]


def run() -> None:
    print("Initializing database...")
    db.init_db(EMBED_DIM)
    conn = db.get_conn()

    # wipe (idempotent re-seed). FK enforcement is paused so parent tables
    # (e.g. tenants) can be cleared without child-order juggling.
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    for tbl in ("tenants", "customers", "accounts", "plans", "plan_changes",
                "invoices", "payments", "kb_docs", "kb_fts", "kb_vec", "audit_log",
                "response_cache", "human_queue", "pending_actions", "adjustments",
                "sessions"):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executemany("INSERT INTO tenants VALUES (?,?)", TENANTS)
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", CUSTOMERS)
    conn.executemany("INSERT INTO plans VALUES (?,?,?,?)", PLANS)
    conn.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?)", ACCOUNTS)
    conn.executemany("INSERT INTO plan_changes VALUES (?,?,?,?,?,?)", PLAN_CHANGES)
    conn.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?)", INVOICES)
    conn.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", PAYMENTS)
    conn.commit()

    print(f"Embedding {len(KB)} knowledge-base chunks "
          f"(sqlite-vec={'on' if db.vec_available() else 'python-fallback'})...")
    vectors = _embed([chunk for _, _, _, chunk in KB])
    for (tenant, doc_id, title, chunk), vec in zip(KB, vectors):
        conn.execute("INSERT INTO kb_docs VALUES (?,?,?,?)", (tenant, doc_id, title, chunk))
        conn.execute("INSERT INTO kb_fts (doc_id, tenant_id, title, chunk) VALUES (?,?,?,?)",
                     (doc_id, tenant, title, chunk))
        db.store_kb_vector(doc_id, tenant, vec)
    conn.commit()

    print("Seed complete:")
    print(f"  tenants={len(TENANTS)} customers={len(CUSTOMERS)} "
          f"invoices={len(INVOICES)} kb_chunks={len(KB)}")


if __name__ == "__main__":
    run()
