-- Knowevo L5 community-summary persistence (kw_012): nexent.kg_summary_t.
--
-- WHAT: one row per (tenant_id, ontology_version, community_id, level) -
-- the deterministic community skeleton (and, when an LLM pass passes the
-- frozen acceptance gates, its summary_text) for ONE community of ONE
-- ontology version. Field-level source of truth: the draft table in
-- competition/docs/tech-optimization-2026-09-28/
-- l5-community-summary-design-2026-09-29.md section 6, plus the T-08
-- follow-up store API (backend/services/knowevo/summary_store.py). The
-- draft filename said v2.5.5_kw_012_knowevo_community_summary.sql; the
-- T-08 task instruction fixed it to this one - same table, same number.
--
-- WHY (design decisions a reader would otherwise have to reverse):
--
-- * Version-scoped key instead of bi-temporal columns. The draft carried
--   UNIQUE (tenant_id, community_id, level) with no version dimension;
--   the store API is version-scoped (save/load take a version_ref),
--   because a community summary is a SNAPSHOT of the graph under one
--   ontology version - the same reason kg_entity_t / kg_relation_t rows
--   are bi-temporal. Here the version label carries that semantics, so
--   adding valid_at/invalid_at would create a SECOND clock that has to be
--   kept consistent with ontology_version_t for no query benefit.
--   UNIQUE is therefore (tenant_id, ontology_version, community_id,
--   level): summary sets of different versions coexist, and re-running
--   the cluster + summarize job overwrites the same logical row
--   (idempotent rebuild, same discipline as the kg_extract_run_t
--   span-hash dedup) instead of appending duplicates.
--
-- * Pitfall #129 family (silent zero output on an unprepared tenant): a
--   tenant with no published ontology version / no ingested graph
--   silently produces ZERO communities and therefore ZERO summary rows -
--   indistinguishable from a broken pipeline. Consumers must answer
--   "which published version does this tenant have" BEFORE generating or
--   loading summaries (same precondition discipline as pitfall #108
--   "which PG are you connected to"). The version-scoped key makes that
--   precondition explicit in the data model: rows exist only for
--   versions someone actually summarized.
--
-- * member_stable_ids as JSONB, not a cross table: a community is a
--   snapshot, not a strong relation (members are stable_id strings in
--   the kg_entity_t domain); read-mostly, write-rarely. Renamed from the
--   draft's member_ids to member_stable_ids to make the domain explicit.
--
-- * summary_kind + the two CHECKs: 'skeleton' rows are the ALWAYS
--   available fallback (LLM_SUMMARY_PROTOCOL.on_gate_fail: discard LLM
--   output, keep the skeleton). The CHECKs make a gate-failed LLM row
--   unrepresentable at the schema level instead of trusting every writer
--   to remember the rule: summary_kind stays in the frozen two-value
--   vocabulary, and a kind='llm' row must carry non-null summary_text
--   with llm_gate_passed TRUE.
--
-- * fingerprint + ix_kgs_fp: the caller compares the stored fingerprint
--   against a freshly computed skeleton fingerprint to decide "graph
--   unchanged -> skip recompute" (design section 6); the index keeps
--   that probe an index hit. The draft's ix_kgs_tenant (tenant_id,
--   level, community_id) is superseded: the load path is always
--   version-scoped and the UNIQUE index already serves the
--   (tenant_id, ontology_version) leftmost prefix.
--
-- * model column: records WHICH model produced summary_text for
--   kind='llm' rows (LLM_SUMMARY_PROTOCOL freezes temperature and token
--   budget, not the model identity; provenance stays queryable).
--
-- Self-contained new table; never modifies existing upstream schema.
-- Every statement is IF NOT EXISTS / no-op on re-run (the runner in
-- deploy/common/run-sql-migrations.sh records the file checksum in
-- nexent.schema_migrations automatically - nothing to register by hand).
BEGIN;

CREATE SCHEMA IF NOT EXISTS nexent;

SET LOCAL search_path TO nexent, public;

CREATE TABLE IF NOT EXISTS nexent.kg_summary_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    ontology_version VARCHAR(20) NOT NULL,   -- ontology_version_t.version label
    community_id VARCHAR(80) NOT NULL,       -- = min(member_stable_ids), kernel contract
    level INT NOT NULL DEFAULT 0,            -- hierarchical level reserved; kernel does level 0
    member_stable_ids JSONB NOT NULL,        -- sorted array of stable_id (community snapshot)
    summary_kind VARCHAR(16) NOT NULL DEFAULT 'skeleton'
        CHECK (summary_kind IN ('skeleton', 'llm')),
    summary_text TEXT,                       -- LLM output when summary_kind='llm'
    model VARCHAR(128),                      -- model identity for kind='llm' provenance
    skeleton_json JSONB NOT NULL,            -- SkeletonSummary snapshot (rebuild shape)
    top_entities JSONB NOT NULL,             -- [[id, name, degree], ...]
    rel_type_counts JSONB NOT NULL,          -- [[rel_type, count], ...]
    bridge_claims JSONB NOT NULL,            -- [claim, ...]
    fingerprint CHAR(64) NOT NULL,           -- sha256 of the kernel skeleton payload
    llm_protocol_version VARCHAR(32),        -- LLM_SUMMARY_PROTOCOL['version']
    llm_gate_passed BOOLEAN,                 -- NULL until an LLM pass runs
    graph_snapshot_at TIMESTAMPTZ,           -- when the member set was computed
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    -- on_gate_fail at the schema level: an 'llm' row must carry its text
    -- and a PASSED gate; a failed gate must have been kept as a skeleton
    -- row instead (LLM_SUMMARY_PROTOCOL.on_gate_fail).
    CONSTRAINT ck_kgs_llm_gate CHECK (
        summary_kind <> 'llm'
        OR (summary_text IS NOT NULL AND llm_gate_passed IS TRUE)
    ),
    UNIQUE (tenant_id, ontology_version, community_id, level)
);

-- Fingerprint probe for the "graph unchanged -> skip recompute" decision
-- (design section 6). The UNIQUE constraint above auto-indexes
-- (tenant_id, ontology_version, community_id, level), which is the load
-- path; this one is the only other access shape.
CREATE INDEX IF NOT EXISTS ix_kgs_fp
    ON nexent.kg_summary_t(tenant_id, fingerprint);

COMMIT;
