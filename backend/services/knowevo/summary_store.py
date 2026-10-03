"""Community-summary persistence store (follow-up, 2026-09-30).

The knowevo-layer persistence for the kernel in ``community_summary.py``:
one row per (tenant_id, ontology_version, community_id, level) in
``nexent.kg_summary_t`` (migration ``v2.5.5_kw_012_kg_summary.sql`` - the
DDL source of truth; this module never creates tables). ``save_summaries``
is the write side of the cluster + summarize job (idempotent: the same
(tenant, version, community) is ONE logical summary and is overwritten);
``load_summaries`` is the read side that future global-route wiring 
consumes before calling ``score_communities`` / ``entities_from_hits``.

Scope discipline (same honest layering as the kernel):
- persistence only - no clustering, no LLM calls, no retrieval wiring, no
  schema.Route branch; nothing running imports this module yet, so default
  retrieval/routing behaviour is unchanged;
- session handling mirrors graph_store / doc_asset_service: one session
  per call via the shared context manager (single commit on exit), and
  exceptions are never swallowed - a failed write raises out of the store;
- the store does NOT recompute kernel fingerprints on load. Comparing a
  stored fingerprint against a freshly computed skeleton (to skip recompute
  when the graph is unchanged) is the CALLER's decision, per the design's
  fingerprint section; duplicating the frozen payload recipe here would be
  a second copy of the kernel's identity function.

Versioning: ``version_ref`` is the ontology version label
(``ontology_version_t.version``, VARCHAR(20)) - a summary set is a
snapshot of the graph under one version, so sets of different versions
coexist and never overwrite each other. Preconditions discipline: a tenant with no published version / no
ingested graph
silently yields zero communities and zero rows; callers must answer
"which published version does this tenant have" BEFORE saving or loading.

Contract: knowevo/backend/services/knowevo/summary_store.py.md (frozen);
design source: the community-summary contract, section 6.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from database.knowevo_db import (
    SCHEMA,
    KnowevoTableBase,
    _get_db_session,
)
from services.knowevo.community_summary import SkeletonSummary
from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    Boolean,
    Column,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

# The frozen summary-kind vocabulary (design section 6; the migration
# CHECK enforces the same two values at the schema level).
SUMMARY_KIND_SKELETON = "skeleton"
SUMMARY_KIND_LLM = "llm"
SUMMARY_KINDS = (SUMMARY_KIND_SKELETON, SUMMARY_KIND_LLM)

# Column widths mirrored from the migration, validated early so a bad key
# fails with a pointed ValueError instead of a PG DataError after a flush.
VERSION_REF_MAX_LEN = 20  # ontology_version_t.version
COMMUNITY_ID_MAX_LEN = 80  # kg_entity_t.stable_id domain

_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_nonempty_str(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be str, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{what} must be a non-empty string")
    return value


def _require_int_min(value: Any, what: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{what} must be >= {minimum}, got {value}")
    return value


class KgSummary(KnowevoTableBase):
    """Community-summary row (``kg_summary_t``, migration kw_012).

    Defined here rather than in knowevo_db so the summary persistence
    layer stays one self-owned file (the migration remains the DDL source
    of truth); the shared knowevo DeclarativeBase keeps the table on the
    same schema/registry conventions as the rest of the knowevo layer.
    Not added to KNOWEVO_MODELS: that list is the 12 frozen domain tables
    plus the run ledger, and this module is not wired into any
    create-all path.
    """

    __tablename__ = "kg_summary_t"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "ontology_version", "community_id", "level",
            name="uq_kg_summary_tenant_version_community_level",
        ),
        Index("ix_kgs_fp", "tenant_id", "fingerprint"),
        {"schema": SCHEMA},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False,
                       doc="Tenant ID for multi-tenancy isolation")
    ontology_version = Column(String(20), nullable=False,
                              doc="ontology_version_t.version label of the snapshot")
    community_id = Column(String(80), nullable=False,
                          doc="min(member_stable_ids), kernel determinism contract")
    level = Column(Integer, nullable=False, doc="Hierarchical level (kernel does 0)")
    member_stable_ids = Column(JSONB, nullable=False,
                               doc="Sorted array of stable_id (community snapshot)")
    summary_kind = Column(String(16), nullable=False, server_default="skeleton",
                          doc="skeleton | llm")
    summary_text = Column(Text, doc="LLM output when summary_kind='llm'")
    model = Column(String(128), doc="Model identity for kind='llm' provenance")
    skeleton_json = Column(JSONB, nullable=False, doc="SkeletonSummary snapshot")
    top_entities = Column(JSONB, nullable=False, doc="[[id, name, degree], ...]")
    rel_type_counts = Column(JSONB, nullable=False, doc="[[rel_type, count], ...]")
    bridge_claims = Column(JSONB, nullable=False, doc="[claim, ...]")
    fingerprint = Column(CHAR(64), nullable=False,
                         doc="sha256 of the kernel skeleton payload")
    llm_protocol_version = Column(String(32), doc="LLM_SUMMARY_PROTOCOL['version']")
    llm_gate_passed = Column(Boolean, doc="NULL until an LLM pass runs")
    graph_snapshot_at = Column(TIMESTAMP(timezone=True),
                               doc="When the member set was computed")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


@dataclass(frozen=True)
class SummaryRecord:
    """One persisted community summary: the kernel skeleton plus the member
    snapshot and (when present) the gate-passed LLM text.

    This is the round-trip shape of the store: ``save_summaries`` consumes
    records, ``load_summaries`` returns them. ``member_ids`` is stored
    beside the skeleton because SkeletonSummary does not carry the member
    set, and rebuilding the kernel ``Community`` for entities_from_hits
    needs it; the invariants below guarantee that rebuild is exact
    (sorted members, community_id = min).

    Gate discipline (LLM_SUMMARY_PROTOCOL.on_gate_fail, enforced here AND
    by the migration CHECK): a kind='llm' row must carry non-empty text
    with ``llm_gate_passed=True``; a FAILED gate must have been persisted
    as a skeleton row instead. Skeleton rows carry no LLM-side fields at
    all - one semantics per column.
    """

    skeleton: SkeletonSummary
    member_ids: tuple[str, ...]
    level: int = 0
    summary_kind: str = SUMMARY_KIND_SKELETON
    summary_text: str | None = None
    model: str | None = None
    llm_protocol_version: str | None = None
    llm_gate_passed: bool | None = None
    graph_snapshot_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.skeleton, SkeletonSummary):
            raise TypeError(
                f"skeleton must be SkeletonSummary, got {type(self.skeleton).__name__}")
        if not self.member_ids:
            raise ValueError("member_ids must be non-empty")
        for m in self.member_ids:
            _require_nonempty_str(m, "member id")
        if tuple(sorted(self.member_ids)) != self.member_ids:
            raise ValueError("member_ids must be sorted ascending")
        if len(self.skeleton.community_id) > COMMUNITY_ID_MAX_LEN:
            raise ValueError(
                f"community_id longer than {COMMUNITY_ID_MAX_LEN} chars "
                f"(kg_summary_t.community_id width)")
        if self.skeleton.community_id != self.member_ids[0]:
            raise ValueError("skeleton.community_id must equal min(member_ids)")
        _require_int_min(self.level, "level", 0)
        if self.summary_kind not in SUMMARY_KINDS:
            raise ValueError(
                f"summary_kind must be one of {SUMMARY_KINDS}, got {self.summary_kind!r}")
        if self.summary_kind == SUMMARY_KIND_SKELETON:
            for field in ("summary_text", "model", "llm_protocol_version",
                          "llm_gate_passed"):
                if getattr(self, field) is not None:
                    raise ValueError(
                        f"{field} must be None for summary_kind='skeleton' "
                        f"(LLM-side columns have exactly one semantics)")
        else:
            _require_nonempty_str(self.summary_text, "summary_text (kind='llm')")
            _require_nonempty_str(self.llm_protocol_version,
                                  "llm_protocol_version (kind='llm')")
            if self.llm_gate_passed is not True:
                raise ValueError(
                    "kind='llm' rows must carry llm_gate_passed=True; a failed "
                    "gate must be persisted as summary_kind='skeleton' "
                    "(LLM_SUMMARY_PROTOCOL.on_gate_fail)")
            if self.model is not None:
                _require_nonempty_str(self.model, "model")
        if self.graph_snapshot_at is not None and not isinstance(
                self.graph_snapshot_at, datetime):
            raise TypeError(
                "graph_snapshot_at must be datetime or None, got "
                f"{type(self.graph_snapshot_at).__name__}")
        if not _FINGERPRINT_RE.fullmatch(self.skeleton.fingerprint):
            raise ValueError(
                "fingerprint must be a 64-char lowercase sha256 hexdigest, got "
                f"{self.skeleton.fingerprint!r}")


class SummaryStore:
    """Persistence for community summaries (kg_summary_t).

    ``session_factory`` is injectable for offline tests (mirrors
    DocAssetService); the default resolves the shared session context
    manager exactly like the rest of the knowevo layer. One session per
    call, one commit on exit, exceptions never swallowed.
    """

    def __init__(self, session_factory: Any = None):
        self._session_factory = session_factory or _get_db_session

    async def save_summaries(self, tenant_id: str, version_ref: str,
                             summaries: Sequence[SummaryRecord]) -> int:
        """Idempotent overwrite of the (tenant, version) summary set.

        The same (tenant_id, version_ref, community_id, level) key is ONE
        logical summary: rerunning the cluster + summarize job overwrites
        the existing row (all mutable columns, updated_at touched) and
        never appends a duplicate. Rows of OTHER versions are untouched.
        In-batch duplicates resolve last-write-wins in argument order.
        Returns the number of rows written (inserts + updates).
        """
        _require_nonempty_str(tenant_id, "tenant_id")
        _require_nonempty_str(version_ref, "version_ref")
        if len(version_ref) > VERSION_REF_MAX_LEN:
            raise ValueError(
                f"version_ref longer than {VERSION_REF_MAX_LEN} chars "
                f"(kg_summary_t.ontology_version width)")
        if isinstance(summaries, (str, bytes)) or not isinstance(summaries, Sequence):
            raise TypeError(
                f"summaries must be a Sequence of SummaryRecord, got "
                f"{type(summaries).__name__}")
        by_key: dict[tuple[str, int], SummaryRecord] = {}
        for rec in summaries:
            if not isinstance(rec, SummaryRecord):
                raise TypeError(
                    f"summaries items must be SummaryRecord, got "
                    f"{type(rec).__name__}")
            by_key[(rec.skeleton.community_id, rec.level)] = rec
        if not by_key:
            return 0

        with self._session_factory() as session:
            existing_rows = session.query(KgSummary).filter(
                KgSummary.tenant_id == tenant_id,
                KgSummary.ontology_version == version_ref,
            ).all()
            existing: dict[tuple[str, int], Any] = {
                (row.community_id, row.level): row for row in existing_rows
            }
            written = 0
            for (community_id, level), rec in by_key.items():
                values = _row_values(rec)
                row = existing.get((community_id, level))
                if row is None:
                    session.add(KgSummary(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        ontology_version=version_ref,
                        **values,
                    ))
                else:
                    for name, value in values.items():
                        setattr(row, name, value)
                    # DB-side now(): no trust in the caller's Python clock
                    # (same rule as valid_now's as_of default).
                    row.updated_at = func.now()
                written += 1
            session.flush()
        return written

    async def load_summaries(self, tenant_id: str,
                             version_ref: str) -> list[SummaryRecord]:
        """Load one (tenant, version) summary set, deterministic order.

        Rows come back ordered by (level, community_id) ascending - the
        kernel returns communities sorted by community_id, so level-0 sets
        round-trip in kernel order. Every row is rebuilt through
        SummaryRecord validation, so a corrupted row raises ValueError
        instead of being silently accepted.
        """
        _require_nonempty_str(tenant_id, "tenant_id")
        _require_nonempty_str(version_ref, "version_ref")
        if len(version_ref) > VERSION_REF_MAX_LEN:
            raise ValueError(
                f"version_ref longer than {VERSION_REF_MAX_LEN} chars "
                f"(kg_summary_t.ontology_version width)")
        with self._session_factory() as session:
            rows = session.query(KgSummary).filter(
                KgSummary.tenant_id == tenant_id,
                KgSummary.ontology_version == version_ref,
            ).order_by(
                KgSummary.level.asc(), KgSummary.community_id.asc()
            ).all()
            # Rebuild INSIDE the session (commit expires ORM rows; touching
            # attributes afterwards raises DetachedInstanceError).
            return [_record_from_row(row) for row in rows]


def _row_values(rec: SummaryRecord) -> dict[str, Any]:
    """SummaryRecord -> kg_summary_t column values (JSON-safe: the kernel's
    tuples become lists so a plain JSONB round-trip is lossless)."""
    skel = rec.skeleton
    return {
        "community_id": skel.community_id,
        "level": rec.level,
        "member_stable_ids": list(rec.member_ids),
        "summary_kind": rec.summary_kind,
        "summary_text": rec.summary_text,
        "model": rec.model,
        "skeleton_json": {
            "community_id": skel.community_id,
            "size": skel.size,
            "top_entities": [list(t) for t in skel.top_entities],
            "rel_type_counts": [list(r) for r in skel.rel_type_counts],
            "bridge_claims": list(skel.bridge_claims),
            "fingerprint": skel.fingerprint,
        },
        "top_entities": [list(t) for t in skel.top_entities],
        "rel_type_counts": [list(r) for r in skel.rel_type_counts],
        "bridge_claims": list(skel.bridge_claims),
        "fingerprint": skel.fingerprint,
        "llm_protocol_version": rec.llm_protocol_version,
        "llm_gate_passed": rec.llm_gate_passed,
        "graph_snapshot_at": rec.graph_snapshot_at,
    }


def _record_from_row(row: Any) -> SummaryRecord:
    """kg_summary_t row -> SummaryRecord (JSONB lists back to the kernel's
    tuple shapes). SummaryRecord validation re-runs here on purpose: a
    row that cannot rebuild a valid record is corrupt and must raise."""
    member_ids = tuple(str(m) for m in (row.member_stable_ids or []))
    skel_json = getattr(row, "skeleton_json", None) or {}
    stored_size = skel_json.get("size") if isinstance(skel_json, dict) else None
    if stored_size is not None and int(stored_size) != len(member_ids):
        raise ValueError(
            f"corrupt kg_summary_t row: skeleton_json.size={stored_size} "
            f"!= len(member_stable_ids)={len(member_ids)} "
            f"(community_id={row.community_id!r})")
    skel = SkeletonSummary(
        community_id=str(row.community_id),
        size=len(member_ids),
        top_entities=tuple(
            (str(eid), str(name), int(deg))
            for eid, name, deg in (row.top_entities or [])
        ),
        rel_type_counts=tuple(
            (str(rel), int(n)) for rel, n in (row.rel_type_counts or [])
        ),
        bridge_claims=tuple(str(c) for c in (row.bridge_claims or [])),
        fingerprint=str(row.fingerprint),
    )
    return SummaryRecord(
        skeleton=skel,
        member_ids=member_ids,
        level=int(row.level),
        summary_kind=str(row.summary_kind),
        summary_text=row.summary_text,
        model=row.model,
        llm_protocol_version=row.llm_protocol_version,
        llm_gate_passed=(None if row.llm_gate_passed is None
                         else bool(row.llm_gate_passed)),
        graph_snapshot_at=row.graph_snapshot_at,
    )
