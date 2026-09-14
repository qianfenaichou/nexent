-- Knowevo core knowledge model migration (12 tables).
-- Source of truth: memo 10-A2/A3 section 2 (frozen field-level DDL).
-- Self-contained new tables only; never modifies existing upstream schema.
-- embedding column: upstream Postgres has no pgvector extension, so it is
-- stored as a JSONB float array and cosine similarity is computed in the
-- service layer (memo 11 revision, ADR-09 option A promoted).
BEGIN;

-- Self-guard: init.sql creates the schema, but this file must also run
-- standalone (verified against a clean database) and stay idempotent.
CREATE SCHEMA IF NOT EXISTS nexent;

SET LOCAL search_path TO nexent, public;

-- 1. Ontology version (full snapshot in JSONB, oplog keeps increments)
CREATE TABLE IF NOT EXISTS nexent.ontology_version_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    version VARCHAR(20) NOT NULL,
    parent_id UUID REFERENCES nexent.ontology_version_t(id),
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    snapshot JSONB NOT NULL,
    applied_ops JSONB NOT NULL,
    metrics JSONB,
    created_by VARCHAR(64),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, version)
);

-- 2. Ontology change proposal (K1 two-level review queue)
CREATE TABLE IF NOT EXISTS nexent.ontology_change_proposal_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    round_id UUID NOT NULL,
    target VARCHAR(64) NOT NULL,
    op VARCHAR(16) NOT NULL,
    payload JSONB NOT NULL,
    confidence FLOAT,
    impact INT,
    novelty FLOAT,
    trigger_source VARCHAR(24) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    reviewed_by VARCHAR(64),
    reviewed_at TIMESTAMPTZ,
    reject_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ocp_queue ON nexent.ontology_change_proposal_t(tenant_id, status, round_id);

-- 3. KG entity (bi-temporal dual timestamps, K2 3.1)
CREATE TABLE IF NOT EXISTS nexent.kg_entity_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    stable_id VARCHAR(80) NOT NULL,
    name VARCHAR(256) NOT NULL,
    aliases JSONB DEFAULT '[]',
    class_ref VARCHAR(64) NOT NULL,
    props JSONB DEFAULT '{}',
    -- JSONB float array + service-layer cosine (no pgvector in upstream image)
    embedding JSONB,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    split_into JSONB,
    valid_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalid_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ,
    superseded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, stable_id)
);
CREATE INDEX IF NOT EXISTS ix_ke_class ON nexent.kg_entity_t(tenant_id, class_ref, status);
CREATE INDEX IF NOT EXISTS ix_ke_alias ON nexent.kg_entity_t USING gin(aliases jsonb_path_ops);

-- 4. KG relation (bi-temporal; src/dst reference entity stable_id)
CREATE TABLE IF NOT EXISTS nexent.kg_relation_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    src VARCHAR(80) NOT NULL,
    dst VARCHAR(80) NOT NULL,
    rel_type VARCHAR(64) NOT NULL,
    claim TEXT NOT NULL,
    props JSONB DEFAULT '{}',
    contested BOOLEAN DEFAULT false,
    valid_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalid_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ,
    superseded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_kr_hop ON nexent.kg_relation_t(tenant_id, src, rel_type);
CREATE INDEX IF NOT EXISTS ix_kr_hop_rev ON nexent.kg_relation_t(tenant_id, dst, rel_type);
CREATE INDEX IF NOT EXISTS ix_kr_valid ON nexent.kg_relation_t(valid_at, invalid_at);

-- 5. Evidence (minimal unit of traceability; EXTRACTED / INFERRED)
CREATE TABLE IF NOT EXISTS nexent.kg_evidence_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    doc_id UUID NOT NULL,
    span_loc JSONB NOT NULL,
    span_text TEXT NOT NULL,
    doc_version_id UUID,
    entity_refs VARCHAR(80)[] NOT NULL,
    edge_ids UUID[] NOT NULL,
    tag VARCHAR(12) NOT NULL,
    modality VARCHAR(12) NOT NULL DEFAULT 'text',
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_kev_refs ON nexent.kg_evidence_t USING gin(entity_refs);
CREATE INDEX IF NOT EXISTS ix_kev_doc ON nexent.kg_evidence_t(doc_id, doc_version_id);

-- 6. Pending entity pool (unmappable entities feed K1 proposals)
CREATE TABLE IF NOT EXISTS nexent.kg_pending_entity_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    name VARCHAR(256) NOT NULL,
    mention_count INT NOT NULL DEFAULT 1,
    evidence_ids UUID[] NOT NULL,
    suggested_class VARCHAR(64),
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, name)
);

-- 7. Document asset (L1 asset identity and version lineage)
CREATE TABLE IF NOT EXISTS nexent.doc_asset_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    title VARCHAR(512) NOT NULL,
    modality VARCHAR(12) NOT NULL,
    asset_no VARCHAR(40) NOT NULL,
    authority_level INT NOT NULL DEFAULT 3,
    doc_type VARCHAR(24) NOT NULL,
    source_url TEXT,
    source_note TEXT,
    supersede_of UUID REFERENCES nexent.doc_asset_t(id),
    parse_status VARCHAR(16),
    parse_quality FLOAT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, asset_no)
);

-- 8. Document version diff (K5.1 three-stage output)
CREATE TABLE IF NOT EXISTS nexent.doc_version_diff_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    old_doc UUID NOT NULL,
    new_doc UUID NOT NULL,
    section_align JSONB NOT NULL,
    changes JSONB NOT NULL,
    precision FLOAT,
    recall FLOAT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 9. Decision card (K3 section 3 schema persistence)
CREATE TABLE IF NOT EXISTS nexent.decision_card_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    question_id VARCHAR(64),
    session_id UUID,
    payload JSONB NOT NULL,
    knowledge_stamp JSONB NOT NULL,
    needs_rerun BOOLEAN DEFAULT false,
    rerun_of UUID,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dc_stamp ON nexent.decision_card_t USING gin(payload jsonb_path_ops);

-- 10. Evolution round (ledger + timeline data source)
CREATE TABLE IF NOT EXISTS nexent.evolution_round_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    trigger_source VARCHAR(24) NOT NULL,
    trigger_ref UUID,
    ops_summary JSONB NOT NULL,
    cost JSONB NOT NULL,
    eval_delta JSONB,
    rollback_of UUID,
    created_at TIMESTAMPTZ DEFAULT now(),
    created_by VARCHAR(64)
);

-- 11. Skill template library
CREATE TABLE IF NOT EXISTS nexent.skill_template_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    name VARCHAR(64) NOT NULL UNIQUE,
    task_type VARCHAR(64) NOT NULL,
    domain VARCHAR(32) NOT NULL,
    version VARCHAR(20) NOT NULL,
    body_md TEXT NOT NULL,
    variables JSONB NOT NULL,
    source JSONB NOT NULL,
    reuse_count INT DEFAULT 0,
    reuse_success FLOAT,
    avg_edit_distance FLOAT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 12. Evaluation run (K4; report page reads directly)
CREATE TABLE IF NOT EXISTS nexent.eval_run_t (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    testset_hash VARCHAR(64) NOT NULL,
    config JSONB NOT NULL,
    metrics JSONB NOT NULL,
    calibration JSONB,
    judge_agreement FLOAT,
    task_ref VARCHAR(16),
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMIT;
