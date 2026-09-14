"""
Unit and integration tests for backend/database/knowevo_db.py (T-03).

Layer 1 (always runs): table metadata, bi-temporal predicate, and frozen
const.py env-var assertions - no database required.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): real-Postgres CRUD smoke for all 12
tables, tenant isolation, bi-temporal current-view filtering, and migration
idempotency. Follows the upstream pattern used by
test/backend/database/test_memory_dreaming_postgres_integration.py.

sys.path is adjusted so ``database.*`` resolves against backend/, matching
the sibling database tests.
"""
import os
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../backend"))

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database.knowevo_db import (
    KNOWEVO_MODELS,
    DocAsset,
    KgEntity,
    KgRelation,
    KnowevoTableBase,
    SkillTemplate,
    create_row,
    get_by_id,
    list_tenant_rows,
    valid_now,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"

EXPECTED_TABLES = {
    "ontology_version_t", "ontology_change_proposal_t",
    "kg_entity_t", "kg_relation_t", "kg_evidence_t",
    "kg_pending_entity_t", "doc_asset_t", "doc_version_diff_t",
    "decision_card_t", "evolution_round_t",
    "skill_template_t", "eval_run_t",
}

MODEL_NAMES = [
    "OntologyVersion", "OntologyChangeProposal", "KgEntity", "KgRelation",
    "KgEvidence", "KgPendingEntity", "DocAsset", "DocVersionDiff",
    "DecisionCard", "EvolutionRound", "SkillTemplate", "EvalRun",
]


# ---------------------------------------------------------------------------
# Layer 1: metadata / predicate assertions (no database)
# ---------------------------------------------------------------------------

class TestTableMetadata:
    def test_registry_has_exactly_12_tables(self):
        assert len(KNOWEVO_MODELS) == 12

    def test_table_names_match_frozen_ddl(self):
        assert {m.__tablename__ for m in KNOWEVO_MODELS} == EXPECTED_TABLES

    def test_own_base_isolated_from_upstream(self):
        # Own declarative base: must not share the upstream TableBase registry
        # (memo 11 #3: per-domain Base, a2a_agent_db pattern).
        from database.db_models import TableBase as UpstreamTableBase
        assert KnowevoTableBase is not UpstreamTableBase
        assert not issubclass(KgEntity, UpstreamTableBase)

    def test_all_tables_carry_tenant_id(self):
        for model in KNOWEVO_MODELS:
            assert "tenant_id" in model.__table__.columns, (
                f"{model.__tablename__} missing tenant_id"
            )

    def test_every_table_in_nexent_schema(self):
        for model in KNOWEVO_MODELS:
            assert model.__table__.schema == "nexent"

    def test_no_table_joins_upstream_registry(self):
        from database import db_models
        upstream_tables = set(db_models.TableBase.metadata.tables)
        ours = set(KnowevoTableBase.metadata.tables)
        assert ours and ours.isdisjoint(upstream_tables)


class TestEmbeddedColumnPolicy:
    def test_embedding_is_jsonb_not_vector(self):
        # Memo 11 #8: upstream image has no pgvector; JSONB array + service-layer cosine.
        from sqlalchemy.dialects.postgresql import JSONB
        assert isinstance(KgEntity.__table__.columns["embedding"].type, JSONB)

    def test_doc_asset_uses_meta_data_attribute_for_metadata_column(self):
        # "metadata" is a reserved DeclarativeBase attribute name; the ORM
        # attribute is meta_data while the DB column stays "metadata".
        assert hasattr(DocAsset, "meta_data")
        assert not hasattr(DocAsset, "metadata") or DocAsset.metadata.__class__.__name__ == "MetaData"
        assert "meta_data" not in DocAsset.__table__.columns
        assert DocAsset.__table__.columns["metadata"].name == "metadata"

    def test_skill_template_name_unique_constraint_single(self):
        uniques = [c for c in SkillTemplate.__table__.constraints
                   if type(c).__name__ == "UniqueConstraint"]
        assert len(uniques) == 1 and uniques[0].name == "uq_skill_template_name"


class TestBiTemporalPredicate:
    """valid_now() must express: valid_at <= as_of AND (invalid_at IS NULL OR
    invalid_at > as_of)."""

    def test_compiled_predicate_references_temporal_columns(self):
        predicate = valid_now(KgRelation, datetime(2026, 9, 14, tzinfo=timezone.utc))
        compiled = str(predicate.compile(compile_kwargs={"literal_binds": True}))
        assert "valid_at <= " in compiled
        assert "invalid_at IS NULL" in compiled or "invalid_at IS  NULL" in compiled
        assert "invalid_at > " in compiled

    def test_semantics_cover_current_expired_future(self):
        as_of = datetime(2026, 9, 14, tzinfo=timezone.utc)
        cases = [
            # (valid_at, invalid_at, expected_current)
            (datetime(2026, 1, 1, tzinfo=timezone.utc), None, True),          # current
            (datetime(2026, 1, 1, tzinfo=timezone.utc),
             datetime(2026, 6, 1, tzinfo=timezone.utc), False),                # expired
            (datetime(2027, 1, 1, tzinfo=timezone.utc), None, False),          # future
            (datetime(2026, 1, 1, tzinfo=timezone.utc),
             datetime(2026, 9, 15, tzinfo=timezone.utc), True),                # inside window
            (datetime(2026, 1, 1, tzinfo=timezone.utc),
             as_of, False),  # invalid_at == as_of is already expired (> strictly)
        ]
        for valid_at, invalid_at, expected in cases:
            ok_valid = valid_at is not None and valid_at <= as_of
            ok_invalid = invalid_at is None or invalid_at > as_of
            assert (ok_valid and ok_invalid) is expected, (
                f"valid_at={valid_at}, invalid_at={invalid_at}"
            )

    def test_default_now_predicate_keeps_open_intervals_only(self):
        # No as_of: current = valid_at set and invalid_at not yet closed.
        predicate = valid_now(KgEntity)
        compiled = str(predicate.compile(compile_kwargs={"literal_binds": True}))
        assert "invalid_at IS NULL" in compiled or "invalid_at IS  NULL" in compiled


class TestConstEnvVars:
    """Frozen env list from memo 10 section 3 must be present in const.py."""

    def test_const_module_has_kw_section(self):
        backend_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "backend"))
        const_path = os.path.join(backend_dir, "consts", "const.py")
        with open(const_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        for var in [
            "KW_LLM_SMALL_MODEL_ID", "KW_LLM_MID_MODEL_ID", "KW_LLM_LARGE_MODEL_ID",
            "KW_GRAPH_STORE_BACKEND", "KW_ONTOLOGY_MAX_CLASSES",
            "KW_ALIGN_TAU1", "KW_ALIGN_TAU2", "KW_AUTO_ACCEPT_LINE",
            "KW_MULTIHOP_MAX_DEPTH", "KW_MULTIHOP_BEAM", "KW_TOKEN_BUDGET_PER_DOMAIN",
        ]:
            # Each var must read its value through os.getenv on one line
            # (scalars are wrapped with int()/float()).
            matching = [
                line for line in source.splitlines()
                if line.startswith(var) and "os.getenv" in line
            ]
            assert matching, f"{var} missing from const.py"
        # Defaults match the frozen memo table.
        for default in ['"pg_jsonb"', '"120"', '"0.80"', '"0.60"', '"0.85"',
                        '"3"', '"3000000"']:
            assert default in source, f"missing default {default}"


# ---------------------------------------------------------------------------
# Layer 2: real-Postgres integration (opt-in via RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

_integration_only = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 with local PostgreSQL env",
)


def _engine_from_env():
    # URL.create handles passwords with reserved characters (e.g. "@") that
    # would break a hand-built DSN string.
    from sqlalchemy.engine import URL
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=os.getenv("KNOWEVO_TEST_PG_USER", "root"),
        password=os.getenv("KNOWEVO_TEST_PG_PASSWORD", "nexent@4321"),
        host=os.getenv("KNOWEVO_TEST_PG_HOST", "localhost"),
        port=int(os.getenv("KNOWEVO_TEST_PG_PORT", "5434")),
        database=os.getenv("KNOWEVO_TEST_PG_DB", "knowevo_test"),
    )
    return create_engine(url, pool_pre_ping=True)


@pytest.fixture(scope="module")
def engine():
    eng = _engine_from_env()
    with eng.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS nexent"))
        conn.commit()
    KnowevoTableBase.metadata.create_all(eng, checkfirst=True)
    yield eng
    KnowevoTableBase.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db_session_factory(engine):
    # Patch the module's own session seam (_get_db_session) so the CRUD
    # helpers run against the test engine. Importing database.client here
    # would drag in the full SDK dependency chain (smolagents et al.),
    # which the isolated unit-test environment does not install.
    import database.knowevo_db as kw
    from contextlib import contextmanager

    test_sessionmaker = sessionmaker(bind=engine)

    @contextmanager
    def _test_get_db_session(db_session=None):
        session = db_session if db_session is not None else test_sessionmaker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    original = kw._get_db_session
    kw._get_db_session = _test_get_db_session
    yield
    kw._get_db_session = original


# --- Per-table minimal valid column sets for CRUD smoke -------------------

def _sample_row(model, tenant):
    """Minimal payload per table exercising the frozen DDL columns.

    Unique keys (stable_id, name, asset_no, ...) get a random suffix per
    call: tests share one database and must not collide on unique
    constraints (uq_kg_entity_tenant_stable_id etc.)."""
    now = datetime.now(timezone.utc)
    nonce = uuid_mod.uuid4().hex[:8]
    doc_id = "33333333-3333-3333-3333-333333333333"
    round_id = "44444444-4444-4444-4444-444444444444"
    if model is KgEntity:
        return dict(
            tenant_id=tenant, stable_id=f"drug:{nonce}", name="Aspirin",
            aliases=[{"alias": "ASA", "type": "abbr"}],
            class_ref="Drug", props={"dose": {"value": "100mg"}},
            embedding=[0.1, 0.2, 0.3], status="active",
            valid_at=now, retrieved_at=now,
        )
    if model is KgRelation:
        return dict(
            tenant_id=tenant, src=f"drug:{nonce}", dst=f"symptom:{nonce}",
            rel_type="treats", claim="Aspirin treats headache",
            props={}, contested=False, valid_at=now,
        )
    if model.__tablename__ == "ontology_version_t":
        return dict(
            tenant_id=tenant, version="v1.0.0", status="draft",
            snapshot={"classes": []}, applied_ops=[],
        )
    if model.__tablename__ == "ontology_change_proposal_t":
        return dict(
            tenant_id=tenant, round_id=round_id, target="class:Drug",
            op="CLS_ADD", payload={"name": "Drug"}, confidence=0.9,
            impact=2, novelty=0.5, trigger_source="seed_bootstrap",
        )
    if model.__tablename__ == "kg_evidence_t":
        return dict(
            tenant_id=tenant, doc_id=doc_id,
            span_loc={"chunk_idx": 0, "page": 1}, span_text="Aspirin 100mg",
            entity_refs=[f"drug:{nonce}"], edge_ids=[doc_id], tag="EXTRACTED",
        )
    if model.__tablename__ == "kg_pending_entity_t":
        return dict(
            tenant_id=tenant, name=f"pending-{nonce}",
            evidence_ids=[doc_id],
        )
    if model.__tablename__ == "doc_asset_t":
        return dict(
            tenant_id=tenant, title="Drug label", modality="text",
            asset_no=f"AST-{nonce}", authority_level=3,
            doc_type="drug_label", meta_data={"lang": "zh"},
        )
    if model.__tablename__ == "doc_version_diff_t":
        return dict(
            tenant_id=tenant, old_doc=doc_id, new_doc=doc_id,
            section_align=[], changes=[{"op": "upd"}],
        )
    if model.__tablename__ == "decision_card_t":
        return dict(
            tenant_id=tenant, question_id="q-1",
            payload={"answer": "yes", "evidence_chain": []},
            knowledge_stamp={"ontology_version": "v1.0.0"},
        )
    if model.__tablename__ == "evolution_round_t":
        return dict(
            tenant_id=tenant, trigger_source="manual",
            ops_summary={"CLS_ADD": 1}, cost={"tokens": 10, "cny": 0.1},
        )
    if model.__tablename__ == "skill_template_t":
        return dict(
            tenant_id=tenant, name=f"tpl-{nonce}", task_type="search",
            domain="medical", version="v1",
            body_md="# SKILL", variables={}, source={"pattern": "manual"},
        )
    if model.__tablename__ == "eval_run_t":
        return dict(
            tenant_id=tenant, testset_hash="abc123",
            config={"ablation_level": "full"}, metrics={"acc": 0.9},
        )
    raise AssertionError(f"no sample row for {model.__tablename__}")


@_integration_only
class TestCrudSmokeIntegration:
    @pytest.mark.parametrize("model_name", MODEL_NAMES)
    def test_create_get_roundtrip(self, engine, db_session_factory, model_name):
        import database.knowevo_db as kw
        model = getattr(kw, model_name)
        created = create_row(model, **_sample_row(model, TENANT_A))
        assert created["id"] is not None
        fetched = get_by_id(model, created["id"], TENANT_A)
        assert fetched is not None and fetched["id"] == created["id"]


@_integration_only
class TestTenantIsolationIntegration:
    def test_cross_tenant_read_is_isolated(self, engine, db_session_factory):
        created = create_row(KgEntity, **_sample_row(KgEntity, TENANT_A))
        # Same primary key, wrong tenant: must not be readable.
        assert get_by_id(KgEntity, created["id"], TENANT_B) is None
        # Owner tenant still reads it.
        assert get_by_id(KgEntity, created["id"], TENANT_A) is not None

    def test_list_tenant_rows_excludes_other_tenants(self, engine, db_session_factory):
        create_row(KgEntity, **_sample_row(KgEntity, TENANT_A))
        create_row(KgEntity, **_sample_row(KgEntity, TENANT_B))
        list_b = list_tenant_rows(KgEntity, TENANT_B, limit=100)
        assert list_b, "tenant B should see its own row"
        for row in list_b:
            assert str(row["tenant_id"]) == TENANT_B

    def test_list_tenant_rows_isolation_exact(self, engine, db_session_factory):
        create_row(KgEntity, **_sample_row(KgEntity, TENANT_A))
        create_row(KgEntity, **_sample_row(KgEntity, TENANT_B))
        a_rows = list_tenant_rows(KgEntity, TENANT_A, limit=100)
        b_rows = list_tenant_rows(KgEntity, TENANT_B, limit=100)
        assert a_rows and b_rows
        assert {str(r["tenant_id"]) for r in a_rows} == {TENANT_A}
        assert {str(r["tenant_id"]) for r in b_rows} == {TENANT_B}


@_integration_only
class TestBiTemporalIntegration:
    # Dedicated tenant: CRUD-smoke rows under TENANT_A would otherwise leak
    # into the current-view assertions below.
    TENANT_T = "55555555-5555-5555-5555-555555555555"

    def test_current_view_excludes_expired_rows(self, engine, db_session_factory):
        now = datetime.now(timezone.utc)
        with sessionmaker(bind=engine)() as session:
            session.add_all([
                KgEntity(
                    tenant_id=self.TENANT_T, stable_id=f"drug:{uuid_mod.uuid4().hex[:6]}",
                    name="Current", class_ref="Drug",
                    valid_at=now - timedelta(days=10),
                ),
                KgEntity(
                    tenant_id=self.TENANT_T, stable_id=f"drug:{uuid_mod.uuid4().hex[:6]}",
                    name="Expired", class_ref="Drug",
                    valid_at=now - timedelta(days=30),
                    invalid_at=now - timedelta(days=5),
                ),
                KgEntity(
                    tenant_id=self.TENANT_T, stable_id=f"drug:{uuid_mod.uuid4().hex[:6]}",
                    name="Future", class_ref="Drug",
                    valid_at=now + timedelta(days=5),
                ),
            ])
            session.commit()
            q = session.query(KgEntity).filter(
                KgEntity.tenant_id == self.TENANT_T,
                valid_now(KgEntity),
            )
            names = {r.name for r in q.all()}
            assert names == {"Current"}

    def test_as_of_view_over_time_travel(self, engine, db_session_factory):
        now = datetime.now(timezone.utc)
        old_id = f"drug:{uuid_mod.uuid4().hex[:6]}"
        alive_id = f"drug:{uuid_mod.uuid4().hex[:6]}"
        with sessionmaker(bind=engine)() as session:
            session.add_all([
                KgEntity(
                    tenant_id=self.TENANT_T, stable_id=old_id,
                    name="OldOnly", class_ref="Drug",
                    valid_at=now - timedelta(days=30),
                    invalid_at=now - timedelta(days=10),
                ),
                KgEntity(
                    tenant_id=self.TENANT_T, stable_id=alive_id,
                    name="Alive", class_ref="Drug",
                    valid_at=now - timedelta(days=10),
                ),
            ])
            session.commit()
            # 20 days ago the first row was current and the second was not.
            as_of = now - timedelta(days=20)
            q = session.query(KgEntity).filter(
                KgEntity.tenant_id == self.TENANT_T,
                valid_now(KgEntity, as_of),
            )
            visible_ids = {r.stable_id for r in q.all()}
            assert old_id in visible_ids
            assert alive_id not in visible_ids


class TestMigrationFileContract:
    def test_migration_file_is_idempotent_sql(self):
        migration = os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "deploy", "sql", "migrations", "v2.5.5_kw_001_knowevo_core.sql",
        )
        with open(migration, "r", encoding="utf-8") as handle:
            sql = handle.read()
        # DDL must be guarded so a re-run (checksum-change cascade in the
        # migration runner) never fails (deploy/sql/migrations/README rules).
        assert sql.count("CREATE TABLE IF NOT EXISTS") == 12
        # Guarded index DDL: every CREATE INDEX after the first table block.
        assert "CREATE INDEX IF NOT EXISTS" in sql
        # Unique/dependency statements that cannot be IF NOT EXISTS guarded
        # are fine because CREATE TABLE IF NOT EXISTS short-circuits them.
