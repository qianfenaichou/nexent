-- Knowevo T-24: kg_extract_run_t extraction diagnostics (kw_009).
-- Purpose (pitfalls #52/#55 沉淀机制, product side): every extract-run ledger
-- row must be able to answer "why did this span produce no entities?".
-- kg_extract_run_t already carries tokens_spent; these four additive columns
-- split the two symptoms that were previously indistinguishable:
--   * empty_content_calls > 0 and no entities  -> "no content" (the body was
--     blank: a reasoning chain consumed the whole output budget with
--     finish_reason=length - pitfalls #52 - or a transient empty generation).
--   * empty_content_calls == 0 and no entities -> "the model said no entities"
--     (a parsed but empty body).
-- The counters are span-level aggregates over the calls issued inside one
-- span's extraction window (see services/knowevo/pipeline/ingest_graph.py).
--
-- Idempotent and additive only: ADD COLUMN IF NOT EXISTS, no rename, no drop,
-- and no change to any existing column semantic (kg_extract_run_t column
-- meanings stay frozen). Safe to re-run after a checksum change (migration
-- README). The NOT NULL DEFAULT 0 keeps older rows readable.
BEGIN;

CREATE SCHEMA IF NOT EXISTS nexent;

ALTER TABLE nexent.kg_extract_run_t
    ADD COLUMN IF NOT EXISTS llm_calls INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS empty_content_calls INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS finish_reasons JSONB;

COMMENT ON COLUMN nexent.kg_extract_run_t.llm_calls IS
    'LLM calls issued for this span (T-24, kw_009)';
COMMENT ON COLUMN nexent.kg_extract_run_t.empty_content_calls IS
    'Calls whose body was blank; >0 with 0 entities = "no content" (#52)';
COMMENT ON COLUMN nexent.kg_extract_run_t.reasoning_tokens IS
    'Summed provider reasoning tokens for this span';
COMMENT ON COLUMN nexent.kg_extract_run_t.finish_reasons IS
    'JSONB {finish_reason: count} over this span''s calls';

COMMIT;
