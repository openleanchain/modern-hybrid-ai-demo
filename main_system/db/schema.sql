-- ===========================================================================
-- Modern Hybrid AI System — systems of record (seeded, stand in for ERP/CRM)
-- Every table is tenant-scoped. The main system is the only writer.
-- ===========================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- --- Tenancy -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    tenant_id   TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    since       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, customer_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

-- --- Accounts / plans ----------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    tenant_id     TEXT NOT NULL,
    customer_id   TEXT NOT NULL,
    plan_code     TEXT NOT NULL,
    balance_cents INTEGER NOT NULL,     -- authoritative; never summarized into memory
    currency      TEXT NOT NULL DEFAULT 'USD',
    status        TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (tenant_id, customer_id)
);

CREATE TABLE IF NOT EXISTS plans (
    tenant_id    TEXT NOT NULL,
    plan_code    TEXT NOT NULL,
    plan_name    TEXT NOT NULL,
    monthly_cents INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, plan_code)
);

CREATE TABLE IF NOT EXISTS plan_changes (
    tenant_id     TEXT NOT NULL,
    customer_id   TEXT NOT NULL,
    changed_on    TEXT NOT NULL,
    from_plan     TEXT,
    to_plan       TEXT NOT NULL,
    note          TEXT
);

-- --- Billing -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoices (
    tenant_id    TEXT NOT NULL,
    invoice_id   TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    issued_on    TEXT NOT NULL,
    period       TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status       TEXT NOT NULL,          -- paid | open | disputed
    note         TEXT,
    PRIMARY KEY (tenant_id, invoice_id)
);

CREATE TABLE IF NOT EXISTS payments (
    tenant_id    TEXT NOT NULL,
    payment_id   TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    paid_on      TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    method       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, payment_id)
);

-- --- Knowledge base (RAG grounding) -------------------------------------
CREATE TABLE IF NOT EXISTS kb_docs (
    tenant_id  TEXT NOT NULL,
    doc_id     TEXT NOT NULL,
    title      TEXT NOT NULL,
    chunk      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, doc_id)
);

-- Full-text index so knowledge_search still works when the embedder is down.
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
    doc_id, tenant_id, title, chunk
);

-- --- Conversation state (session + persistent memory; Phase 2 fills these) --
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    customer_id    TEXT NOT NULL,
    running_summary TEXT NOT NULL DEFAULT '',
    raw_turns      TEXT NOT NULL DEFAULT '[]',   -- JSON array of recent turns
    updated_at     TEXT NOT NULL
);

-- --- Audit trail (every lookup and action is recorded) ------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    tenant_id   TEXT,
    customer_id TEXT,
    actor       TEXT NOT NULL,     -- which tier/tool/skill
    action      TEXT NOT NULL,
    detail      TEXT
);

-- --- Always-on: response cache + human handoff queue (Phase 3) -----------
-- Recent good answers, served during an outage as the first fallback rung.
CREATE TABLE IF NOT EXISTS response_cache (
    tenant_id   TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    query_norm  TEXT NOT NULL,
    answer      TEXT NOT NULL,
    tier        INTEGER,
    ts          TEXT NOT NULL,
    PRIMARY KEY (tenant_id, customer_id, query_norm)
);

-- Requests the ladder couldn't answer deterministically go here for a human.
CREATE TABLE IF NOT EXISTS human_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    tenant_id   TEXT,
    customer_id TEXT,
    message     TEXT,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'open'
);

-- --- Write-gate: staged proposals + committed adjustments (Phase 4) ------
-- One pending, validated proposal per session, awaiting confirmation.
CREATE TABLE IF NOT EXISTS pending_actions (
    session_id   TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    kind         TEXT NOT NULL,
    params       TEXT NOT NULL,     -- JSON
    validation   TEXT NOT NULL,     -- JSON
    created_at   TEXT NOT NULL
);

-- Committed financial adjustments (credits/refunds) — the write-gate's output.
CREATE TABLE IF NOT EXISTS adjustments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    kind         TEXT NOT NULL,
    invoice_id   TEXT,
    amount_cents INTEGER NOT NULL,
    note         TEXT
);
