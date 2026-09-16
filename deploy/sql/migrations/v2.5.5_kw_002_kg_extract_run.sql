-- Knowevo T-06: kg_extract_run_t (run bookkeeping, 13th table).
-- Not a domain table: it makes the offline extraction pipeline idempotent
-- (span-hash dedup so re-running a batch only fills gaps, never duplicates
-- evidence/entities). Owned by T-06 ingest_graph pipeline; source of truth
-- for the run ledger is the pipeline README (kg_extract_run_t span hash).
-- Follows the 001 file's conventions: schema-qualified, idempotent, and
-- never modifies upstream tables.
BEGIN;

CREATE SCHEMA IF NOT EXISTS nexent;

SET LOCAL search_path TO nexent, public;

CREATE TABLE IF NOT EXISTS nexent.kg_extract_run_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    run_id UUID NOT NULL,
    span_hash VARCHAR(64) NOT NULL,
    channel VARCHAR(12) NOT NULL DEFAULT 'llm',
    status VARCHAR(16) NOT NULL DEFAULT 'done',
    tokens_spent INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, span_hash)
);

COMMIT;
