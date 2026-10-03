"""Tests for services/knowevo/summary_store.py (persistence, kw_012).

Layer 0 (always runs): static assertions on migration
``deploy/sql/migrations/v2.5.5_kw_012_kg_summary.sql`` - the file exists
under its frozen name, carries every required column / the version-scoped
UNIQUE / the two CHECKs / the fingerprint index, is idempotent, and stays
self-contained (no ALTER/DROP of upstream tables). The expected strings
come from the design doc section 6 (l5-community-summary-design) and the
kw_012 migration contract, not from reading the implementation.

Layer 1 (always runs): offline behavior against a fake session - kernel
records round-trip through save/load, the same (tenant, version,
community) key overwrites instead of appending, versions and tenants are
isolated, load order is deterministic, and session failures propagate
(never swallowed). Skeletons are built by the REAL kernel
(cluster_greedy_modularity + skeleton_summary) so the expected values are
kernel outputs, not hand-derived constants.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): real-Postgres run - JSONB round
trip through the actual column types, overwrite idempotency on real rows,
version/tenant coexistence, and the ck_kgs_llm_gate CHECK constraint.
Same gate pattern as test_graph_store.py; throwaway
tenants are cleaned up in finally blocks.
"""
import os
import sys
import uuid as uuid_mod
from dataclasses import replace
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from services.knowevo.community_summary import (
    LLM_SUMMARY_PROTOCOL,
    Community,
    cluster_greedy_modularity,
    entities_from_hits,
    score_communities,
    skeleton_summary,
)
from services.knowevo.summary_store import (
    COMMUNITY_ID_MAX_LEN,
    SUMMARY_KIND_LLM,
    SUMMARY_KIND_SKELETON,
    SUMMARY_KINDS,
    VERSION_REF_MAX_LEN,
    KgSummary,
    SummaryRecord,
    SummaryStore,
)
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
VERSION_1 = "v1.0.0"
VERSION_2 = "v2.0.0"

MIGRATION_PATH = (_REPO_ROOT / "deploy" / "sql" / "migrations"
                  / "v2.5.5_kw_012_kg_summary.sql")

# Two disjoint triangles: greedy modularity must keep them separate (no
# bridge edge), and each triangle carries its own topic tokens so the
# global-route seam test has exactly one community matching its query.
NODES = ["ent:a", "ent:b", "ent:c", "ent:d", "ent:e", "ent:f"]
NAMES = {n: n.split(":")[1].upper() for n in NODES}
EDGES = [
    {"src": "ent:a", "dst": "ent:b", "rel_type": "cultivates",
     "claim": "rice paddy irrigation"},
    {"src": "ent:b", "dst": "ent:c", "rel_type": "cultivates",
     "claim": "rice seedling nursery"},
    {"src": "ent:a", "dst": "ent:c", "rel_type": "supplies",
     "claim": "paddy water source"},
    {"src": "ent:d", "dst": "ent:e", "rel_type": "processes",
     "claim": "wheat threshing grain"},
    {"src": "ent:e", "dst": "ent:f", "rel_type": "processes",
     "claim": "wheat milling flour"},
    {"src": "ent:d", "dst": "ent:f", "rel_type": "stores",
     "claim": "grain silo storage"},
]
RICE_COMMUNITY_ID = "ent:a"  # min member id of the rice/paddy triangle


def _kernel_records():
    """Records built from real kernel outputs (independent expected values)."""
    comms = cluster_greedy_modularity(NODES, EDGES)
    assert [c.community_id for c in comms] == sorted(c.community_id for c in comms)
    return [
        SummaryRecord(
            skeleton=skeleton_summary(c, names=NAMES, edges=EDGES),
            member_ids=c.member_ids,
        )
        for c in comms
    ]


def _llm_variant(record: SummaryRecord) -> SummaryRecord:
    """A gate-passed LLM row over the same skeleton (protocol version from
    the kernel's frozen LLM_SUMMARY_PROTOCOL, not a copy-pasted literal)."""
    return replace(
        record,
        summary_kind=SUMMARY_KIND_LLM,
        summary_text="community theme: rice paddy irrigation aggregates the members",
        model="test-model",
        llm_protocol_version=LLM_SUMMARY_PROTOCOL["version"],
        llm_gate_passed=True,
    )


# ---------------------------------------------------------------------------
# Fake session: understands exactly what SummaryStore issues (chained
# .filter() on column == literal, .order_by(col.asc(), ...), add/flush).
# ---------------------------------------------------------------------------

def _matches(crit, row) -> bool:
    column = getattr(crit.left, "key", None)
    expected = getattr(crit.right, "value", None)
    return getattr(row, column) == expected


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self._criteria = []
        self._order_keys = []

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def order_by(self, *criteria):
        for crit in criteria:
            self._order_keys.append(crit.element.key)
        return self

    def all(self):
        rows = [r for r in self._rows
                if all(_matches(c, r) for c in self._criteria)]
        # successive stable sorts in reverse criterion order == tuple sort
        for key in reversed(self._order_keys):
            rows = sorted(rows, key=lambda r: getattr(r, key))
        return rows


class _FakeSession:
    def __init__(self, table):
        self._table = table
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.committed = True
        return False

    def query(self, model):
        return _FakeQuery(self._table)

    def add(self, row):
        self._table.append(row)

    def flush(self):
        pass


class _FakeSessionFactory:
    def __init__(self):
        self.table = []
        self.sessions = []

    def __call__(self):
        session = _FakeSession(self.table)
        self.sessions.append(session)
        return session


def _fake_store() -> tuple[SummaryStore, _FakeSessionFactory]:
    factory = _FakeSessionFactory()
    return SummaryStore(session_factory=factory), factory


# ---------------------------------------------------------------------------
# Layer 0: migration file static assertions (kw_012 contract, section 6).
# ---------------------------------------------------------------------------

class TestKw012MigrationFile:
    def test_exists_under_frozen_name(self):
        assert MIGRATION_PATH.name == "v2.5.5_kw_012_kg_summary.sql"
        assert MIGRATION_PATH.exists()

    def test_required_columns_present(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        for fragment in (
            "id UUID PRIMARY KEY",
            "tenant_id UUID NOT NULL",
            "ontology_version VARCHAR(20) NOT NULL",
            "community_id VARCHAR(80) NOT NULL",
            "level INT NOT NULL DEFAULT 0",
            "member_stable_ids JSONB NOT NULL",
            "summary_kind VARCHAR(16) NOT NULL DEFAULT 'skeleton'",
            "summary_text TEXT",
            "model VARCHAR(128)",
            "skeleton_json JSONB NOT NULL",
            "top_entities JSONB NOT NULL",
            "rel_type_counts JSONB NOT NULL",
            "bridge_claims JSONB NOT NULL",
            "fingerprint CHAR(64) NOT NULL",
            "llm_protocol_version VARCHAR(32)",
            "llm_gate_passed BOOLEAN",
            "graph_snapshot_at TIMESTAMPTZ",
            "created_at TIMESTAMPTZ DEFAULT now()",
            "updated_at TIMESTAMPTZ DEFAULT now()",
        ):
            assert fragment in sql, f"missing column fragment: {fragment}"

    def test_version_scoped_unique_and_gate_checks(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "UNIQUE (tenant_id, ontology_version, community_id, level)" in sql
        assert "CHECK (summary_kind IN ('skeleton', 'llm'))" in sql
        assert "ck_kgs_llm_gate" in sql
        assert "llm_gate_passed IS TRUE" in sql

    def test_fingerprint_index_present(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "CREATE INDEX IF NOT EXISTS ix_kgs_fp" in sql
        assert "ON nexent.kg_summary_t(tenant_id, fingerprint)" in sql

    def test_idempotent_and_transactional(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "CREATE SCHEMA IF NOT EXISTS nexent" in sql
        assert "CREATE TABLE IF NOT EXISTS nexent.kg_summary_t" in sql
        assert "BEGIN;" in sql
        assert "COMMIT;" in sql
        # WHY header comes first (kw_001/kw_011 convention)
        assert sql.lstrip().startswith("--")
        # family lesson must be documented in the header
        assert "#129" in sql

    def test_self_contained_no_upstream_modification(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        for forbidden in ("ALTER TABLE", "DROP TABLE", "DROP COLUMN",
                          "UPDATE ", "DELETE FROM"):
            assert forbidden not in sql


class TestOrmModelShape:
    def test_table_and_schema(self):
        assert KgSummary.__tablename__ == "kg_summary_t"
        assert KgSummary.__table__.schema == "nexent"

    def test_unique_constraint_is_version_scoped(self):
        uniques = [c for c in KgSummary.__table__.constraints
                   if isinstance(c, UniqueConstraint)]
        assert len(uniques) == 1
        assert [col.name for col in uniques[0].columns] == [
            "tenant_id", "ontology_version", "community_id", "level",
        ]

    def test_fingerprint_index_declared(self):
        assert [idx.name for idx in KgSummary.__table__.indexes] == ["ix_kgs_fp"]


# ---------------------------------------------------------------------------
# Layer 1a: SummaryRecord validation (spec-derived invariants).
# ---------------------------------------------------------------------------

class TestSummaryRecordValidation:
    def test_rejects_non_skeleton(self):
        with pytest.raises(TypeError, match="SkeletonSummary"):
            SummaryRecord(skeleton={"community_id": "ent:a"},
                          member_ids=("ent:a",))

    def test_rejects_empty_member_ids(self):
        records = _kernel_records()
        with pytest.raises(ValueError, match="non-empty"):
            SummaryRecord(skeleton=records[0].skeleton, member_ids=())

    def test_rejects_unsorted_member_ids(self):
        records = _kernel_records()
        skel = records[0].skeleton
        other = next(m for m in NODES if m != skel.community_id)
        with pytest.raises(ValueError, match="sorted"):
            SummaryRecord(skeleton=skel, member_ids=(other, skel.community_id))

    def test_rejects_community_id_not_min(self):
        records = _kernel_records()
        skel = records[0].skeleton
        outsiders = sorted(m for m in NODES if m != skel.community_id)[:2]
        with pytest.raises(ValueError, match="min"):
            SummaryRecord(skeleton=skel, member_ids=tuple(outsiders))

    def test_rejects_negative_level(self):
        records = _kernel_records()
        with pytest.raises(ValueError, match="level"):
            SummaryRecord(skeleton=records[0].skeleton,
                          member_ids=records[0].member_ids, level=-1)

    def test_rejects_unknown_summary_kind(self):
        records = _kernel_records()
        with pytest.raises(ValueError, match="summary_kind"):
            SummaryRecord(skeleton=records[0].skeleton,
                          member_ids=records[0].member_ids, summary_kind="hybrid")

    def test_skeleton_row_must_not_carry_llm_fields(self):
        records = _kernel_records()
        base = {"skeleton": records[0].skeleton,
                "member_ids": records[0].member_ids}
        with pytest.raises(ValueError, match="summary_text"):
            SummaryRecord(summary_text="text", **base)
        with pytest.raises(ValueError, match="model"):
            SummaryRecord(model="m", **base)
        with pytest.raises(ValueError, match="llm_protocol_version"):
            SummaryRecord(llm_protocol_version="p", **base)
        with pytest.raises(ValueError, match="llm_gate_passed"):
            SummaryRecord(llm_gate_passed=True, **base)

    def test_llm_row_requires_text_protocol_and_passed_gate(self):
        records = _kernel_records()
        base = {"skeleton": records[0].skeleton,
                "member_ids": records[0].member_ids,
                "summary_kind": SUMMARY_KIND_LLM}
        # a MISSING field surfaces as TypeError ("must be str, got NoneType")
        with pytest.raises((TypeError, ValueError), match="summary_text"):
            SummaryRecord(llm_protocol_version="p", llm_gate_passed=True, **base)
        with pytest.raises((TypeError, ValueError), match="llm_protocol_version"):
            SummaryRecord(summary_text="t", llm_gate_passed=True, **base)
        with pytest.raises(ValueError, match="llm_gate_passed"):
            SummaryRecord(summary_text="t", llm_protocol_version="p",
                          llm_gate_passed=False, **base)
        with pytest.raises(ValueError, match="llm_gate_passed"):
            SummaryRecord(summary_text="t", llm_protocol_version="p", **base)

    def test_rejects_bad_fingerprint_shape(self):
        records = _kernel_records()
        skel = replace(records[0].skeleton, fingerprint="not-a-sha256")
        with pytest.raises(ValueError, match="hexdigest"):
            SummaryRecord(skeleton=skel, member_ids=records[0].member_ids)

    def test_rejects_non_datetime_snapshot(self):
        records = _kernel_records()
        with pytest.raises(TypeError, match="graph_snapshot_at"):
            SummaryRecord(skeleton=records[0].skeleton,
                          member_ids=records[0].member_ids,
                          graph_snapshot_at="2026-09-30")

    def test_valid_llm_record_passes(self):
        records = _kernel_records()
        rec = _llm_variant(records[0])
        assert rec.summary_kind == SUMMARY_KIND_LLM
        assert rec.llm_gate_passed is True

    def test_vocabulary_constants_frozen(self):
        assert SUMMARY_KINDS == ("skeleton", "llm")
        assert SUMMARY_KIND_SKELETON == "skeleton"
        assert SUMMARY_KIND_LLM == "llm"


# ---------------------------------------------------------------------------
# Layer 1b: store behavior on the fake session.
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    async def test_round_trip_preserves_kernel_skeletons(self):
        store, factory = _fake_store()
        records = _kernel_records()
        written = await store.save_summaries(TENANT_A, VERSION_1, records)
        assert written == len(records)
        assert factory.sessions[-1].committed is True

        loaded = await store.load_summaries(TENANT_A, VERSION_1)
        by_id = {r.skeleton.community_id: r for r in records}
        assert {r.skeleton.community_id for r in loaded} == set(by_id)
        for rec in loaded:
            src = by_id[rec.skeleton.community_id]
            assert rec.skeleton.top_entities == src.skeleton.top_entities
            assert rec.skeleton.rel_type_counts == src.skeleton.rel_type_counts
            assert rec.skeleton.bridge_claims == src.skeleton.bridge_claims
            assert rec.skeleton.fingerprint == src.skeleton.fingerprint
            assert rec.skeleton.size == src.skeleton.size
            assert rec.member_ids == src.member_ids
            assert rec.level == 0
            assert rec.summary_kind == SUMMARY_KIND_SKELETON
            assert rec.summary_text is None
            assert rec.model is None
            assert rec.llm_protocol_version is None
            assert rec.llm_gate_passed is None

    async def test_llm_fields_round_trip(self):
        store, _ = _fake_store()
        records = [_llm_variant(r) for r in _kernel_records()]
        await store.save_summaries(TENANT_A, VERSION_1, records)
        loaded = await store.load_summaries(TENANT_A, VERSION_1)
        for rec in loaded:
            assert rec.summary_kind == SUMMARY_KIND_LLM
            assert rec.summary_text.startswith("community theme:")
            assert rec.model == "test-model"
            assert rec.llm_protocol_version == LLM_SUMMARY_PROTOCOL["version"]
            assert rec.llm_gate_passed is True

    async def test_loaded_records_feed_global_route_seam(self):
        """The consumption shape: rebuild kernel Communities from the
        loaded records and feed score_communities / entities_from_hits."""
        store, _ = _fake_store()
        await store.save_summaries(TENANT_A, VERSION_1, _kernel_records())
        loaded = await store.load_summaries(TENANT_A, VERSION_1)
        comms = [Community(community_id=r.skeleton.community_id,
                           member_ids=r.member_ids) for r in loaded]
        hits = score_communities("rice irrigation",
                                 [r.skeleton for r in loaded], top_n=1)
        assert hits[0].community_id == RICE_COMMUNITY_ID
        entity_ids = entities_from_hits(hits, comms, edges=EDGES)
        assert entity_ids[:3] == ("ent:a", "ent:b", "ent:c")

    async def test_empty_version_loads_empty(self):
        store, _ = _fake_store()
        assert await store.load_summaries(TENANT_A, "v9.9.9") == []

    async def test_load_order_is_level_then_community_id(self):
        store, _ = _fake_store()
        records = _kernel_records()
        await store.save_summaries(TENANT_A, VERSION_1, list(reversed(records)))
        loaded = await store.load_summaries(TENANT_A, VERSION_1)
        ids = [r.skeleton.community_id for r in loaded]
        assert ids == sorted(ids)


class TestIdempotentOverwrite:
    async def test_same_key_overwrites_not_appends(self):
        store, factory = _fake_store()
        records = _kernel_records()
        await store.save_summaries(TENANT_A, VERSION_1, records)
        updated = [replace(records[0]), *(_llm_variant(r) for r in records[1:])]
        written = await store.save_summaries(TENANT_A, VERSION_1, updated)
        assert written == len(updated)
        assert len(factory.table) == len(records)  # row count unchanged
        loaded = await store.load_summaries(TENANT_A, VERSION_1)
        kinds = {r.skeleton.community_id: r.summary_kind for r in loaded}
        assert kinds[records[0].skeleton.community_id] == SUMMARY_KIND_SKELETON
        for rec in records[1:]:
            assert kinds[rec.skeleton.community_id] == SUMMARY_KIND_LLM

    async def test_in_batch_duplicate_last_write_wins(self):
        store, factory = _fake_store()
        records = _kernel_records()
        rec = records[0]
        written = await store.save_summaries(
            TENANT_A, VERSION_1, [rec, rec, _llm_variant(rec)])
        assert written == 1
        assert len(factory.table) == 1
        loaded = await store.load_summaries(TENANT_A, VERSION_1)
        assert loaded[0].summary_kind == SUMMARY_KIND_LLM

    async def test_other_version_rows_untouched(self):
        store, factory = _fake_store()
        records = _kernel_records()
        await store.save_summaries(TENANT_A, VERSION_1, records)
        await store.save_summaries(TENANT_A, VERSION_2,
                                   [_llm_variant(r) for r in records])
        assert len(factory.table) == 2 * len(records)
        v1 = await store.load_summaries(TENANT_A, VERSION_1)
        v2 = await store.load_summaries(TENANT_A, VERSION_2)
        assert all(r.summary_kind == SUMMARY_KIND_SKELETON for r in v1)
        assert all(r.summary_kind == SUMMARY_KIND_LLM for r in v2)

    async def test_tenant_isolation(self):
        store, _ = _fake_store()
        records = _kernel_records()
        await store.save_summaries(TENANT_A, VERSION_1, records)
        assert await store.load_summaries(TENANT_B, VERSION_1) == []
        await store.save_summaries(TENANT_B, VERSION_1, records[:1])
        assert len(await store.load_summaries(TENANT_A, VERSION_1)) == len(records)
        assert len(await store.load_summaries(TENANT_B, VERSION_1)) == 1

    async def test_empty_summaries_is_noop(self):
        def factory():
            raise AssertionError("session must not be opened for an empty batch")

        store = SummaryStore(session_factory=factory)
        assert await store.save_summaries(TENANT_A, VERSION_1, []) == 0


class TestStoreValidationAndErrors:
    async def test_rejects_non_sequence_summaries(self):
        store, _ = _fake_store()
        records = _kernel_records()
        with pytest.raises(TypeError, match="Sequence"):
            await store.save_summaries(TENANT_A, VERSION_1, "records")
        with pytest.raises(TypeError, match="Sequence"):
            await store.save_summaries(TENANT_A, VERSION_1, 42)
        with pytest.raises(TypeError, match="SummaryRecord"):
            await store.save_summaries(TENANT_A, VERSION_1,
                                       [r.skeleton for r in records])

    async def test_rejects_overlong_version_ref(self):
        store, _ = _fake_store()
        with pytest.raises(ValueError, match=str(VERSION_REF_MAX_LEN)):
            await store.save_summaries(TENANT_A, "v" * (VERSION_REF_MAX_LEN + 1),
                                       _kernel_records())
        # load side shares the same fail-fast width check
        with pytest.raises(ValueError, match=str(VERSION_REF_MAX_LEN)):
            await store.load_summaries(TENANT_A, "v" * (VERSION_REF_MAX_LEN + 1))

    async def test_load_rejects_size_member_mismatch(self):
        """skeleton_json.size vs len(member_stable_ids) is a corrupt row."""
        store, factory = _fake_store()
        records = _kernel_records()
        await store.save_summaries(TENANT_A, VERSION_1, records)
        row = factory.table[0]
        blob = dict(row.skeleton_json or {})
        blob["size"] = int(blob.get("size", 0)) + 1
        row.skeleton_json = blob
        with pytest.raises(ValueError, match="skeleton_json.size"):
            await store.load_summaries(TENANT_A, VERSION_1)

    async def test_rejects_empty_tenant_or_version(self):
        store, _ = _fake_store()
        records = _kernel_records()
        with pytest.raises(ValueError, match="tenant_id"):
            await store.save_summaries("", VERSION_1, records)
        with pytest.raises(ValueError, match="version_ref"):
            await store.save_summaries(TENANT_A, "", records)
        with pytest.raises(ValueError, match="tenant_id"):
            await store.load_summaries("", VERSION_1)
        with pytest.raises(ValueError, match="version_ref"):
            await store.load_summaries(TENANT_A, "")

    def test_community_id_width_mirrors_column(self):
        assert COMMUNITY_ID_MAX_LEN == 80

    async def test_session_open_failure_propagates(self):
        def factory():
            raise RuntimeError("db down")

        store = SummaryStore(session_factory=factory)
        records = _kernel_records()
        with pytest.raises(RuntimeError, match="db down"):
            await store.save_summaries(TENANT_A, VERSION_1, records)
        with pytest.raises(RuntimeError, match="db down"):
            await store.load_summaries(TENANT_A, VERSION_1)

    async def test_query_failure_propagates(self):
        class _BrokenSession(_FakeSession):
            def query(self, model):
                raise RuntimeError("query exploded")

        store = SummaryStore(session_factory=lambda: _BrokenSession([]))
        with pytest.raises(RuntimeError, match="query exploded"):
            await store.save_summaries(TENANT_A, VERSION_1, _kernel_records())
        with pytest.raises(RuntimeError, match="query exploded"):
            await store.load_summaries(TENANT_A, VERSION_1)


# ---------------------------------------------------------------------------
# Layer 2: real Postgres (RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

pg_gate = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests")


def _cleanup_tenant(tenant: str) -> None:
    from database.knowevo_db import _get_db_session

    with _get_db_session() as session:
        session.query(KgSummary).filter(
            KgSummary.tenant_id == tenant).delete()
        session.flush()


@pg_gate
class TestPgSummaryStore:
    async def test_save_load_overwrite_on_real_db(self):
        from database.knowevo_db import _get_db_session

        store = SummaryStore()
        tenant = str(uuid_mod.uuid4())
        records = _kernel_records()
        try:
            written = await store.save_summaries(tenant, VERSION_1, records)
            assert written == len(records)
            loaded = await store.load_summaries(tenant, VERSION_1)
            assert {r.skeleton.fingerprint for r in loaded} == {
                r.skeleton.fingerprint for r in records}
            # JSONB round trip through the real column types
            by_id = {r.skeleton.community_id: r for r in records}
            for rec in loaded:
                src = by_id[rec.skeleton.community_id]
                assert rec.skeleton.top_entities == src.skeleton.top_entities
                assert rec.member_ids == src.member_ids
            # overwrite: row count must not grow
            await store.save_summaries(
                tenant, VERSION_1, [_llm_variant(r) for r in records])
            with _get_db_session() as session:
                n_rows = session.query(KgSummary).filter(
                    KgSummary.tenant_id == tenant).count()
            assert n_rows == len(records)
            kinds = {r.skeleton.community_id: r.summary_kind
                     for r in await store.load_summaries(tenant, VERSION_1)}
            assert set(kinds.values()) == {SUMMARY_KIND_LLM}
        finally:
            _cleanup_tenant(tenant)

    async def test_version_coexistence_and_tenant_isolation_on_real_db(self):
        store = SummaryStore()
        tenant_a = str(uuid_mod.uuid4())
        tenant_b = str(uuid_mod.uuid4())
        records = _kernel_records()
        try:
            await store.save_summaries(tenant_a, VERSION_1, records)
            await store.save_summaries(tenant_a, VERSION_2,
                                       [_llm_variant(r) for r in records])
            await store.save_summaries(tenant_b, VERSION_1, records[:1])
            assert len(await store.load_summaries(tenant_a, VERSION_1)) == len(records)
            assert len(await store.load_summaries(tenant_a, VERSION_2)) == len(records)
            assert len(await store.load_summaries(tenant_b, VERSION_1)) == 1
            assert await store.load_summaries(tenant_b, VERSION_2) == []
        finally:
            _cleanup_tenant(tenant_a)
            _cleanup_tenant(tenant_b)

    async def test_llm_gate_check_enforced_by_db(self):
        """ck_kgs_llm_gate: a kind='llm' row without text / passed gate is
        unrepresentable at the schema level (on_gate_fail discipline)."""
        from database.knowevo_db import _get_db_session

        store = SummaryStore()
        tenant = str(uuid_mod.uuid4())
        try:
            await store.save_summaries(tenant, VERSION_1, _kernel_records())
            raised = False
            with _get_db_session() as session:
                session.add(KgSummary(
                    id=uuid_mod.uuid4(), tenant_id=tenant,
                    ontology_version=VERSION_1, community_id="ent:a",
                    level=0, member_stable_ids=["ent:a"],
                    summary_kind=SUMMARY_KIND_LLM, summary_text=None,
                    skeleton_json={}, top_entities=[], rel_type_counts=[],
                    bridge_claims=[], fingerprint="0" * 64))
                try:
                    session.flush()
                except IntegrityError:
                    raised = True
                    session.rollback()
            assert raised, "ck_kgs_llm_gate must reject a gate-failed llm row"
        finally:
            _cleanup_tenant(tenant)
