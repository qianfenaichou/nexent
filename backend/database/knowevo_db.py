"""
Knowevo knowledge model database layer (12 domain tables + 1 run ledger).

Self-contained module following the upstream per-domain ``*_db.py`` pattern
(see ``backend/database/a2a_agent_db.py``): models live in their own
``DeclarativeBase`` so importing this module never touches the upstream
``db_models`` registry. DDL source of truth: memo 10-A2/A3 section 2
(deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql).

All service-layer queries MUST filter by ``tenant_id`` (multi-tenant
isolation, aligned with upstream database modules).
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase

SCHEMA = "nexent"

# UUID primary keys (memo 10: "id UUID PK" across all 12 tables).
_TENANT_ID_DOC = "Tenant ID for multi-tenancy isolation"


class KnowevoTableBase(DeclarativeBase):
    """Own declarative base; never merged into upstream TableBase registry."""
    pass


class OntologyVersion(KnowevoTableBase):
    """Full ontology snapshot per version; oplog keeps incremental operations."""
    __tablename__ = "ontology_version_t"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_ontology_version_tenant_version"),
        Index("ix_ov_tenant_parent", "tenant_id", "parent_id"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    version = Column(String(20), nullable=False, doc="Semantic version, e.g. v1.0.0")
    parent_id = Column(UUID(as_uuid=True), doc="Parent ontology version id")
    status = Column(String(16), nullable=False, server_default=text("'draft'"),
                    doc="draft | published | deprecated")
    snapshot = Column(JSONB, nullable=False, doc="Full ontology snapshot")
    applied_ops = Column(JSONB, nullable=False, doc="Applied operation log (K0 1.2)")
    metrics = Column(JSONB, doc="K0 four metrics {cov, red, dep, align}")
    created_by = Column(String(64))
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class OntologyChangeProposal(KnowevoTableBase):
    """Two-level review queue entry (K1): one proposed change per row."""
    __tablename__ = "ontology_change_proposal_t"
    __table_args__ = (
        Index("ix_ocp_queue", "tenant_id", "status", "round_id"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    round_id = Column(UUID(as_uuid=True), nullable=False, doc="Evolution round id")
    target = Column(String(64), nullable=False,
                    doc="class:stable_id / rel:rel_type / prop:prop_id")
    op = Column(String(16), nullable=False, doc="CLS_ADD | PROP_ADD | REL_UPD | ...")
    payload = Column(JSONB, nullable=False, doc="Proposal body with evidence anchors")
    confidence = Column(Float, doc="K1 3.3 ranking feature")
    impact = Column(Integer, doc="K1 3.3 ranking feature")
    novelty = Column(Float, doc="K1 3.3 ranking feature")
    trigger_source = Column(String(24), nullable=False,
                            doc="seed_bootstrap | pending_pool | standard_update | manual")
    status = Column(String(16), nullable=False, server_default=text("'pending'"),
                    doc="pending | confirmed | rejected | auto_accepted")
    reviewed_by = Column(String(64))
    reviewed_at = Column(TIMESTAMP(timezone=True))
    reject_reason = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class KgEntity(KnowevoTableBase):
    """Graph entity, bi-temporal (K2 3.1): valid_at/invalid_at are business
    time, retrieved_at/superseded_at are system time."""
    __tablename__ = "kg_entity_t"
    __table_args__ = (
        UniqueConstraint("tenant_id", "stable_id", name="uq_kg_entity_tenant_stable_id"),
        Index("ix_ke_class", "tenant_id", "class_ref", "status"),
        Index("ix_ke_alias", "aliases", postgresql_using="gin",
              postgresql_ops={"aliases": "jsonb_path_ops"}),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    stable_id = Column(String(80), nullable=False,
                       doc="Stable domain identifier, survives renames")
    name = Column(String(256), nullable=False)
    aliases = Column(JSONB, server_default=text("'[]'::jsonb"),
                     doc="[{alias, type: brand|generic|abbr}]")
    class_ref = Column(String(64), nullable=False, doc="Ontology stable_id")
    props = Column(JSONB, server_default=text("'{}'::jsonb"),
                   doc="{prop_name: {value, valid_at, invalid_at, source}}")
    # JSONB float array; cosine similarity computed in service layer
    # (upstream Postgres image ships no pgvector extension - memo 11 #8).
    embedding = Column(JSONB, doc="Float array embedding of name+summary")
    status = Column(String(16), nullable=False, server_default=text("'active'"),
                    doc="active | deprecated | split")
    split_into = Column(JSONB, doc="Both sides of a split, as id pair")
    valid_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    invalid_at = Column(TIMESTAMP(timezone=True))
    retrieved_at = Column(TIMESTAMP(timezone=True))
    superseded_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class KgRelation(KnowevoTableBase):
    """Graph edge; src/dst reference KgEntity.stable_id, bi-temporal."""
    __tablename__ = "kg_relation_t"
    __table_args__ = (
        Index("ix_kr_hop", "tenant_id", "src", "rel_type"),
        Index("ix_kr_hop_rev", "tenant_id", "dst", "rel_type"),
        Index("ix_kr_valid", "valid_at", "invalid_at"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    src = Column(String(80), nullable=False, doc="Source entity stable_id")
    dst = Column(String(80), nullable=False, doc="Target entity stable_id")
    rel_type = Column(String(64), nullable=False)
    claim = Column(Text, nullable=False, doc="Proposition text of the edge")
    props = Column(JSONB, server_default=text("'{}'::jsonb"))
    contested = Column(Boolean, server_default=text("false"))
    valid_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    invalid_at = Column(TIMESTAMP(timezone=True))
    retrieved_at = Column(TIMESTAMP(timezone=True))
    superseded_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class KgEvidence(KnowevoTableBase):
    """Minimal traceability unit; EXTRACTED (from source) or INFERRED."""
    __tablename__ = "kg_evidence_t"
    __table_args__ = (
        Index("ix_kev_refs", "entity_refs", postgresql_using="gin"),
        Index("ix_kev_doc", "doc_id", "doc_version_id"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    doc_id = Column(UUID(as_uuid=True), nullable=False)
    span_loc = Column(JSONB, nullable=False, doc="{chunk_idx, page, bbox?}")
    span_text = Column(Text, nullable=False)
    doc_version_id = Column(UUID(as_uuid=True), doc="Points to doc_asset_t version")
    entity_refs = Column(ARRAY(String(80)), nullable=False)
    edge_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    tag = Column(String(12), nullable=False, doc="EXTRACTED | INFERRED")
    modality = Column(String(12), nullable=False, server_default=text("'text'"),
                      doc="text | table | caption")
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class KgPendingEntity(KnowevoTableBase):
    """Unmappable entity pool; feeds ontology proposals (K1 source)."""
    __tablename__ = "kg_pending_entity_t"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_kg_pending_entity_tenant_name"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    name = Column(String(256), nullable=False)
    mention_count = Column(Integer, nullable=False, server_default=text("1"))
    evidence_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    suggested_class = Column(String(64))
    status = Column(String(16), nullable=False, server_default=text("'pending'"),
                    doc="pending | proposed | resolved | dismissed")
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class DocAsset(KnowevoTableBase):
    """L1 document asset with authority level and version lineage."""
    __tablename__ = "doc_asset_t"
    __table_args__ = (
        UniqueConstraint("tenant_id", "asset_no", name="uq_doc_asset_tenant_asset_no"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    title = Column(String(512), nullable=False)
    modality = Column(String(12), nullable=False)
    asset_no = Column(String(40), nullable=False, doc="Asset number (asset identity)")
    authority_level = Column(Integer, nullable=False, server_default=text("3"),
                             doc="1 national std, 2 guideline, 3 label, 4 popular science")
    doc_type = Column(String(24), nullable=False,
                      doc="guideline | drug_label | lab_report | policy | material_list | edu_graphic")
    source_url = Column(Text)
    source_note = Column(Text, doc="Compliance ledger entry for provenance")
    supersede_of = Column(UUID(as_uuid=True), doc="Version lineage chain")
    parse_status = Column(String(16))
    parse_quality = Column(Float, doc="Parse health check score")
    # "metadata" is a reserved DeclarativeBase attribute name (SQLAlchemy
    # rejects it at mapper configuration), so the column is named meta_data
    # like everywhere else in the upstream codebase.
    meta_data = Column("metadata", JSONB, server_default=text("'{}'::jsonb"))
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class DocVersionDiff(KnowevoTableBase):
    """Three-stage diff output between two document versions (K5.1)."""
    __tablename__ = "doc_version_diff_t"
    __table_args__ = {"schema": SCHEMA}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    old_doc = Column(UUID(as_uuid=True), nullable=False)
    new_doc = Column(UUID(as_uuid=True), nullable=False)
    section_align = Column(JSONB, nullable=False)
    changes = Column(JSONB, nullable=False)
    precision = Column(Float, doc="Calibration value against gold labels")
    recall = Column(Float, doc="Calibration value against gold labels")
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class DecisionCard(KnowevoTableBase):
    """Persisted decision card (K3 section 3 schema)."""
    __tablename__ = "decision_card_t"
    __table_args__ = (
        Index("ix_dc_stamp", "payload", postgresql_using="gin",
              postgresql_ops={"payload": "jsonb_path_ops"}),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    question_id = Column(String(64))
    session_id = Column(UUID(as_uuid=True))
    payload = Column(JSONB, nullable=False, doc="Card JSON with evidence_chain/kg_path")
    knowledge_stamp = Column(JSONB, nullable=False,
                             doc="{ontology_version, kg_cutoff}")
    needs_rerun = Column(Boolean, server_default=text("false"))
    rerun_of = Column(UUID(as_uuid=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class EvolutionRound(KnowevoTableBase):
    """One knowledge evolution round; ledger and timeline data source."""
    __tablename__ = "evolution_round_t"
    __table_args__ = {"schema": SCHEMA}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    trigger_source = Column(String(24), nullable=False,
                            doc="new_docs | standard_update | manual | correction")
    trigger_ref = Column(UUID(as_uuid=True), doc="doc_asset_t.id / proposal.id")
    ops_summary = Column(JSONB, nullable=False,
                         doc="{CLS_ADD:2, REL_UPD:5, edges_superseded:23, ...}")
    cost = Column(JSONB, nullable=False, doc="{tokens, cny, human_minutes}")
    eval_delta = Column(JSONB, doc="{testset_hash, acc_before, acc_after}")
    rollback_of = Column(UUID(as_uuid=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    created_by = Column(String(64))


class SkillTemplate(KnowevoTableBase):
    """Reusable skill workflow template with mining provenance."""
    __tablename__ = "skill_template_t"
    __table_args__ = (
        UniqueConstraint("name", name="uq_skill_template_name"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    name = Column(String(64), nullable=False)
    task_type = Column(String(64), nullable=False)
    domain = Column(String(32), nullable=False)
    version = Column(String(20), nullable=False)
    body_md = Column(Text, nullable=False, doc="Parameterized SKILL.md full text")
    variables = Column(JSONB, nullable=False,
                       doc="{domain, task_type, relation_template, domain_rules}")
    source = Column(JSONB, nullable=False, doc="{pattern, mined_from, induced_at}")
    reuse_count = Column(Integer, server_default=text("0"))
    reuse_success = Column(Float)
    avg_edit_distance = Column(Float)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class EvalRun(KnowevoTableBase):
    """One evaluation run (K4); report page reads this directly."""
    __tablename__ = "eval_run_t"
    __table_args__ = {"schema": SCHEMA}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    testset_hash = Column(String(64), nullable=False)
    config = Column(JSONB, nullable=False,
                    doc="{ablation_level, model_plan, knowledge_stamp}")
    metrics = Column(JSONB, nullable=False,
                     doc="{acc, pass2, pass3, trace_machine, trace_human, p95, tokens, cny}")
    calibration = Column(JSONB)
    judge_agreement = Column(Float, doc="K4 4 calibration / audit agreement")
    task_ref = Column(String(16))
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class KgExtractRun(KnowevoTableBase):
    """T-06 run bookkeeping: one row per extracted span (span-hash unique),
    making the ingest_graph pipeline idempotent across crash/resume. This is
    an internal run ledger, not a domain table - added by T-06 via migration
    v2.5.5_kw_002 (the 12 domain tables above stay untouched)."""
    __tablename__ = "kg_extract_run_t"
    __table_args__ = (
        UniqueConstraint("tenant_id", "span_hash",
                         name="uq_kg_extract_run_tenant_span"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, doc=_TENANT_ID_DOC)
    run_id = Column(UUID(as_uuid=True), nullable=False,
                    doc="One batch run may span many spans")
    span_hash = Column(String(64), nullable=False,
                       doc="sha256[:32] over doc_id:chunk_idx:modality:text")
    channel = Column(String(12), nullable=False, server_default=text("'llm'"),
                     doc="llm | table")
    status = Column(String(16), nullable=False, server_default=text("'done'"))
    tokens_spent = Column(Integer, server_default=text("0"))
    # T-24 extraction diagnostics (migration v2.5.5_kw_009): span-level
    # aggregate that separates "no content" from "no entities" (pitfalls
    # #52/#55). Additive columns with server defaults so pre-kw_009 rows stay
    # readable (0 / 0 / 0, finish_reasons NULL).
    llm_calls = Column(Integer, nullable=False, server_default=text("0"),
                       doc="LLM calls issued for this span")
    empty_content_calls = Column(Integer, nullable=False,
                                 server_default=text("0"),
                                 doc="Calls whose body was blank (#52 symptom)")
    reasoning_tokens = Column(Integer, nullable=False,
                              server_default=text("0"),
                              doc="Summed provider reasoning tokens")
    finish_reasons = Column(JSONB,
                            doc="{finish_reason: count} over this span's calls")
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


# Ordered registry so tests can iterate all tables deterministically.
# 13 entries: the 12 frozen domain tables + the T-06 run ledger.
KNOWEVO_MODELS = [
    OntologyVersion,
    OntologyChangeProposal,
    KgEntity,
    KgRelation,
    KgEvidence,
    KgPendingEntity,
    DocAsset,
    DocVersionDiff,
    DecisionCard,
    EvolutionRound,
    SkillTemplate,
    EvalRun,
    KgExtractRun,
]


def _get_db_session():
    """Import inside the function to avoid a module-level circular import of
    ``database.client`` (same pattern as a2a_agent_db.py). The upstream
    ``get_db_session`` is itself a @contextmanager yielding a session with
    commit/rollback/close handling, so callers use ``with`` directly."""
    from database.client import get_db_session as _gds
    return _gds()


def valid_now(model, as_of: Optional[datetime] = None):
    """Bi-temporal current-view predicate: business time window contains
    ``as_of`` (default: now). Usable directly in ``Query.filter()``.

    A row is current when valid_at <= as_of and (invalid_at is null or
    invalid_at > as_of). Reused by every current-view query so the
    temporal semantics live in exactly one place.
    """
    if as_of is None:
        # DB-side now(): no trust in the caller's Python clock, and the
        # comparison stays a single SQL expression the planner can use.
        as_of = func.now()
    return (model.valid_at <= as_of) & (
        (model.invalid_at.is_(None)) | (model.invalid_at > as_of)
    )


def valid_range_contains(model, as_of: Optional[datetime] = None):
    """Index-usable form of :func:`valid_now` (same semantics, one GiST hit).

    ``valid_now`` is a two-conjunct boolean predicate. A b-tree cannot serve
    its second conjunct -- ``invalid_at IS NULL OR invalid_at > as_of`` mixes a
    NULL test with a range test -- so only the ``valid_at <= as_of`` prefix
    ever uses an index. The identical test expressed as half-open range
    containment is a single ``&&``/``@>`` operator that a GiST index on the
    range expression answers in one probe.

    Equivalence (must hold for the swap to be safe)::

        [valid_at, COALESCE(invalid_at, 'infinity'))  @>  as_of
        <=>  valid_at <= as_of  AND  as_of < COALESCE(invalid_at, 'infinity')
        <=>  valid_at <= as_of  AND  (invalid_at IS NULL OR invalid_at > as_of)

    The expression below is deliberately written to be **textually identical**
    to the index expression created by migration ``v2.5.5_kw_011``
    (``tstzrange(valid_at, COALESCE(invalid_at, 'infinity'::timestamptz), '[)')``).
    PostgreSQL matches an expression index by parsed expression tree, so any
    deviation here silently loses the index.

    PRECONDITION (data invariant, enforced by ``ck_kr_valid_order`` /
    ``ck_ke_valid_order`` in ``kw_011``): ``invalid_at IS NULL OR invalid_at >=
    valid_at``. Unlike the boolean predicate, *constructing* a range with
    ``invalid_at < valid_at`` raises ``range lower bound must be less than or
    equal to range upper bound`` rather than returning false. Run the preflight
    query in the ``kw_011`` header before switching a query over to this form.
    """
    if as_of is None:
        as_of = func.now()
    span = func.tstzrange(
        model.valid_at,
        func.coalesce(model.invalid_at, text("'infinity'::timestamptz")),
        text("'[)'"),
    )
    return span.op("@>")(as_of)


def create_row(model, **values) -> Dict[str, Any]:
    """Insert one row of any Knowevo table and return its primary key."""
    with _get_db_session() as session:
        row = model(**values)
        session.add(row)
        session.flush()
        return {"id": row.id}


def get_by_id(model, row_id, tenant_id):
    """Fetch one row by primary key under tenant isolation."""
    with _get_db_session() as session:
        row = session.query(model).filter(
            model.id == row_id,
            model.tenant_id == tenant_id,
        ).first()
        if row is None:
            return None
        return {"id": row.id}


def list_tenant_rows(model, tenant_id, limit: int = 50) -> List[Dict[str, Any]]:
    """List rows of one Knowevo table for one tenant only."""
    with _get_db_session() as session:
        rows = (
            session.query(model)
            .filter(model.tenant_id == tenant_id)
            .limit(limit)
            .all()
        )
        return [{"id": row.id, "tenant_id": row.tenant_id} for row in rows]
