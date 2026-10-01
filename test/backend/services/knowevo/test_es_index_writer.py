"""Production ES write path (T-08 follow-up): EsIndexWriter + ingest hook.

Layer 1 (always runs, all-offline fake ES / fake PG): the writer must
create the production indices idempotently with an explicit strict
mapping (IK analyzers only when the ``_cat/plugins`` probe really finds
``analysis-ik``; probe errors degrade to standard, audited), bulk-upsert
entity cards idempotently by (tenant, stable_id) with the tenant forced
from the argument, and the ingest hook in ``pipeline/ingest_graph`` must
be failure-isolated: ES errors never change the run's exit code or
report, and the sync stays a no-op unless ``KW_ES_SYNC_ON_INGEST`` opts
in (the test harness sets ``ELASTICSEARCH_HOST`` globally, so host
presence alone must never arm a write path).
"""
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo import es_index_writer as xw
from services.knowevo import kg_service
from services.knowevo.es_index_writer import (
    ASSET_INDEX,
    DENSE_WRITEBACK_REASON,
    ENTITY_INDEX,
    EsIndexWriter,
    asset_mapping,
    build_es_index_writer,
    collect_active_entities,
    entity_mapping,
    sync_enabled,
    sync_tenant_entities,
)

TENANT = "33333333-3333-3333-3333-333333333333"
OTHER_TENANT = "44444444-4444-4444-4444-444444444444"


# ---------------------------------------------------------------------------
# Fake ES transport (duck-typed ElasticSearchCore)
# ---------------------------------------------------------------------------

class FakeIndices:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.create_calls = []
        self.exists_calls = []

    def exists(self, index=None, **kwargs):
        self.exists_calls.append(index)
        return index in self.existing

    def create(self, index=None, settings=None, mappings=None, **kwargs):
        self.create_calls.append({"index": index, "settings": settings,
                                  "mappings": mappings})
        self.existing.add(index)
        return {"acknowledged": True}


class FakeCat:
    def __init__(self, rows=None, error=None):
        self.rows = list(rows or [])
        self.error = error
        self.calls = 0

    def plugins(self, format=None, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.rows)


class FakeClient:
    def __init__(self, existing=(), plugin_rows=None, plugin_error=None,
                 bulk_response=None):
        self.indices = FakeIndices(existing)
        self.cat = FakeCat(plugin_rows, plugin_error)
        self.bulk_calls = []
        self._bulk_response = bulk_response

    def bulk(self, operations=None, refresh=None, **kwargs):
        self.bulk_calls.append({"operations": operations,
                                "refresh": refresh})
        if self._bulk_response is not None:
            return self._bulk_response
        items = [{"index": {"_id": op["index"].get("_id"), "status": 201}}
                 for op in (operations or []) if "index" in op]
        return {"took": 5, "errors": False, "items": items}


class FakeCore:
    def __init__(self, **kwargs):
        self.client = FakeClient(**kwargs)


def _card(sid="Drug:metformin", name="二甲双胍", **overrides):
    card = {
        "stable_id": sid,
        "name": name,
        "class_ref": "Drug",
        "status": "active",
        "aliases": [{"alias": "格华止", "type": "brand"}, "Metformin"],
        "props": {"x": 1},
    }
    card.update(overrides)
    return card


# ---------------------------------------------------------------------------
# Mapping shapes
# ---------------------------------------------------------------------------

class TestEntityMapping:
    def test_standard_shape_without_ik(self):
        m = entity_mapping(False)
        assert m["settings"] == {"number_of_shards": 1,
                                 "number_of_replicas": 0}
        props = m["mappings"]["properties"]
        assert m["mappings"]["dynamic"] == "strict"
        assert props["name"] == {"type": "text"}
        assert props["aliases"]["properties"]["alias"] == {"type": "text"}
        for field in ("tenant_id", "stable_id", "class_ref", "status"):
            assert props[field] == {"type": "keyword"}
        assert props["props"] == {"type": "object", "enabled": False}

    def test_no_embedding_field_is_declared(self):
        # Honest dense boundary: the mapping must not even offer a knn field.
        assert "embedding" not in entity_mapping(True)["mappings"]["properties"]
        assert "embedding" not in (
            entity_mapping(False)["mappings"]["properties"])

    def test_ik_analyzers_when_enabled(self):
        props = entity_mapping(True)["mappings"]["properties"]
        assert props["name"] == {"type": "text", "analyzer": "ik_max_word",
                                 "search_analyzer": "ik_smart"}
        assert props["aliases"]["properties"]["alias"] == {
            "type": "text", "analyzer": "ik_max_word",
            "search_analyzer": "ik_smart"}


class TestAssetMapping:
    def test_strict_shape(self):
        m = asset_mapping(False)
        assert m["mappings"]["dynamic"] == "strict"
        props = m["mappings"]["properties"]
        assert props["title"] == {"type": "text"}
        assert props["authority_level"] == {"type": "integer"}
        assert props["supersede_of"] == {"type": "keyword"}
        meta = props["metadata"]
        assert meta["dynamic"] is True
        assert meta["properties"]["split"] == {"type": "text"}
        assert meta["properties"]["row_number"] == {"type": "integer"}
        assert meta["properties"]["published_at"] == {"type": "keyword"}

    def test_ik_reaches_the_text_fields_only(self):
        props = asset_mapping(True)["mappings"]["properties"]
        assert props["title"]["analyzer"] == "ik_max_word"
        assert props["metadata"]["properties"]["split"]["analyzer"] == (
            "ik_max_word")
        assert "analyzer" not in props["authority_level"]


# ---------------------------------------------------------------------------
# IK probe: the three audited states + injected short-circuit
# ---------------------------------------------------------------------------

class TestProbeIk:
    def test_present_uses_ik(self):
        core = FakeCore(plugin_rows=[{"component": "analysis-ik",
                                      "version": "8.x"}])
        w = EsIndexWriter(core)
        assert w.probe_ik() is True
        assert w.audit["ik_probe"] == "present"
        assert w.audit["ik_enabled"] is True

    def test_absent_falls_back_to_standard(self):
        core = FakeCore(plugin_rows=[])  # 2026-09-30 container reality
        w = EsIndexWriter(core)
        assert w.probe_ik() is False
        assert w.audit["ik_probe"] == "absent"
        assert w.audit["ik_enabled"] is False

    def test_probe_error_degrades_to_standard_and_audits(self):
        core = FakeCore(plugin_error=ConnectionError("es down"))
        w = EsIndexWriter(core)
        assert w.probe_ik() is False
        assert w.audit["ik_probe"] == "error"
        assert w.audit["ik_enabled"] is False
        # The writer stays usable after a failed probe.
        assert w.ensure_indices() == [ENTITY_INDEX, ASSET_INDEX]

    def test_injected_answer_never_touches_the_probe(self):
        core = FakeCore(plugin_rows=[{"component": "analysis-ik"}])
        w = EsIndexWriter(core, ik_enabled=False)
        assert w.probe_ik() is False
        assert core.client.cat.calls == 0
        assert w.audit["ik_probe"] == "injected"

    def test_probe_result_is_cached(self):
        core = FakeCore(plugin_rows=[])
        w = EsIndexWriter(core)
        w.probe_ik()
        w.ensure_indices()
        assert core.client.cat.calls == 1


# ---------------------------------------------------------------------------
# ensure_indices: idempotent, never touches existing indices
# ---------------------------------------------------------------------------

class TestEnsureIndices:
    def test_creates_both_production_indices_when_absent(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        created = w.ensure_indices()
        assert created == [ENTITY_INDEX, ASSET_INDEX]
        assert ENTITY_INDEX == "knowevo_entities"
        assert ASSET_INDEX == "knowevo_assets"
        assert [c["index"] for c in core.client.indices.create_calls] == [
            ENTITY_INDEX, ASSET_INDEX]

    def test_created_with_explicit_strict_mapping(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        w.ensure_indices()
        call = core.client.indices.create_calls[0]
        assert call["settings"] == {"number_of_shards": 1,
                                    "number_of_replicas": 0}
        assert call["mappings"]["dynamic"] == "strict"
        assert "name" in call["mappings"]["properties"]

    def test_second_call_creates_nothing(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        assert w.ensure_indices() == [ENTITY_INDEX, ASSET_INDEX]
        assert w.ensure_indices() == []
        assert len(core.client.indices.create_calls) == 2

    def test_existing_indices_are_never_touched(self):
        core = FakeCore(existing=[ENTITY_INDEX, ASSET_INDEX])
        w = EsIndexWriter(core)
        assert w.ensure_indices() == []
        assert core.client.indices.create_calls == []
        assert w.audit["indices_created"] == []


# ---------------------------------------------------------------------------
# upsert_entities: bulk, idempotent by (tenant, stable_id)
# ---------------------------------------------------------------------------

class TestUpsertEntities:
    def test_bulk_ops_carry_tenant_scoped_ids_and_forced_tenant(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        result = w.upsert_entities(TENANT, [_card()])
        assert result["sent"] == 1
        ops = core.client.bulk_calls[0]["operations"]
        assert ops[0]["index"] == {
            "_index": ENTITY_INDEX, "_id": f"{TENANT}::Drug:metformin"}
        doc = ops[1]
        assert doc["tenant_id"] == TENANT
        assert doc["stable_id"] == "Drug:metformin"
        assert doc["name"] == "二甲双胍"
        assert doc["class_ref"] == "Drug"
        assert doc["status"] == "active"

    def test_aliases_normalised_from_both_shapes(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        w.upsert_entities(TENANT, [_card()])
        doc = core.client.bulk_calls[0]["operations"][1]
        assert doc["aliases"] == [{"alias": "格华止", "type": "brand"},
                                  {"alias": "Metformin"}]

    def test_props_passthrough_and_refresh_forwarded(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        w.upsert_entities(TENANT, [_card(props={"evidence_ids": ["ev-1"]})])
        assert core.client.bulk_calls[0]["operations"][1]["props"] == {
            "evidence_ids": ["ev-1"]}
        assert core.client.bulk_calls[0]["refresh"] == "wait_for"

    def test_cards_without_stable_id_or_name_are_dropped(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        result = w.upsert_entities(TENANT, [
            _card(sid="Drug:ok", name="二甲双胍"),
            {"name": "no stable id"},
            {"stable_id": "Drug:no-name"},
            "not-a-mapping",
        ])
        assert result["sent"] == 1
        ops = core.client.bulk_calls[0]["operations"]
        assert [o.get("index", {}).get("_id") for o in ops[::2]] == [
            f"{TENANT}::Drug:ok"]

    def test_tenant_is_forced_from_argument_not_card(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        w.upsert_entities(TENANT, [_card(), _card(sid="Drug:other")])
        for doc in core.client.bulk_calls[0]["operations"][1::2]:
            assert doc["tenant_id"] == TENANT

    def test_rerun_reissues_identical_payload(self):
        # Idempotency by construction: same cards -> same doc ids + docs,
        # so a re-run overwrites in place instead of duplicating.
        core = FakeCore()
        w = EsIndexWriter(core)
        cards = [_card(), _card(sid="Symptom:polyuria", name="多尿",
                                class_ref="Symptom")]
        w.upsert_entities(TENANT, cards)
        w.upsert_entities(TENANT, cards)
        first, second = core.client.bulk_calls
        assert first["operations"] == second["operations"]

    def test_same_stable_id_across_tenants_never_collides(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        w.upsert_entities(TENANT, [_card()])
        w.upsert_entities(OTHER_TENANT, [_card()])
        id1 = core.client.bulk_calls[0]["operations"][0]["index"]["_id"]
        id2 = core.client.bulk_calls[1]["operations"][0]["index"]["_id"]
        assert id1 == f"{TENANT}::Drug:metformin"
        assert id2 == f"{OTHER_TENANT}::Drug:metformin"
        assert id1 != id2

    def test_empty_cards_issue_no_bulk_call(self):
        core = FakeCore()
        w = EsIndexWriter(core)
        result = w.upsert_entities(TENANT, [])
        assert result == {"sent": 0, "bulk_errors": 0, "took_ms": None}
        assert core.client.bulk_calls == []
        assert w.audit["last_bulk"] == result

    def test_bulk_errors_counted_from_the_es_response(self):
        response = {"took": 9, "errors": True, "items": [
            {"index": {"_id": "a", "status": 201}},
            {"index": {"_id": "b", "status": 400,
                       "error": {"type": "mapper_parsing_exception"}}},
            {"index": {"_id": "c", "status": 429,
                       "error": {"type": "es_rejected_execution"}}},
        ]}
        core = FakeCore(bulk_response=response)
        w = EsIndexWriter(core)
        result = w.upsert_entities(TENANT, [_card(), _card(sid="Drug:b"),
                                            _card(sid="Drug:c")])
        assert result == {"sent": 3, "bulk_errors": 2, "took_ms": 9}
        assert w.audit["last_bulk"] == result


# ---------------------------------------------------------------------------
# collect_active_entities: PG side of the reconcile (fake session factory)
# ---------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    def filter(self, *args, **kwargs):
        self._captured["filters"].extend(args)
        return self

    def order_by(self, *args, **kwargs):
        self._captured["order_by"].extend(args)
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    def query(self, model):
        self._captured["models"].append(model)
        return _FakeQuery(self._rows, self._captured)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _pg_row(sid, name="二甲双胍", cls="Drug"):
    return SimpleNamespace(
        stable_id=sid, name=name, class_ref=cls, status="active",
        aliases=[{"alias": "格华止", "type": "brand"}],
        props={"evidence_ids": ["ev-1"]},
        embedding=[0.1, 0.2],   # must NOT be exported (no dense write-back)
        id="row-pk-1",          # must NOT be exported (doc id is derived)
    )


class TestCollectActiveEntities:
    def test_exports_exactly_the_mapping_fields(self):
        captured = {"filters": [], "order_by": [], "models": []}
        factory = lambda: _FakeSession([_pg_row("Drug:metformin")], captured)
        docs = collect_active_entities(TENANT, factory)
        assert len(docs) == 1
        assert docs[0] == {
            "stable_id": "Drug:metformin", "name": "二甲双胍",
            "class_ref": "Drug", "status": "active",
            "aliases": [{"alias": "格华止", "type": "brand"}],
            "props": {"evidence_ids": ["ev-1"]},
        }
        assert "embedding" not in docs[0]
        assert "id" not in docs[0]

    def test_queries_the_active_entities_of_one_tenant(self):
        from database.knowevo_db import KgEntity

        captured = {"filters": [], "order_by": [], "models": []}
        factory = lambda: _FakeSession([], captured)
        collect_active_entities(TENANT, factory)
        assert captured["models"] == [KgEntity]
        assert captured["filters"][0].right.value == TENANT
        assert captured["filters"][1].right.value == "active"


# ---------------------------------------------------------------------------
# sync_tenant_entities / build_es_index_writer: env gating
# ---------------------------------------------------------------------------

class TestSyncTenantEntities:
    def test_disabled_by_default_returns_none_without_side_effects(self,
                                                                   monkeypatch):
        monkeypatch.delenv(xw.ENV_SYNC_ON_INGEST, raising=False)
        # Host IS set (the harness sets it globally) - the flag alone must
        # decide; nothing else may be touched when the sync is disarmed.
        def forbidden_factory():
            raise AssertionError(
                "PG must not be touched when the sync is disarmed")

        assert sync_tenant_entities(TENANT,
                                    session_factory=forbidden_factory) is None

    def test_enabled_reconciles_and_returns_the_audit(self, monkeypatch):
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, "1")
        core = FakeCore()
        writer = EsIndexWriter(core)
        captured = {"filters": [], "order_by": [], "models": []}
        factory = lambda: _FakeSession([_pg_row("Drug:metformin"),
                                        _pg_row("Symptom:polyuria", "多尿",
                                                "Symptom")], captured)
        result = sync_tenant_entities(TENANT, writer=writer,
                                      session_factory=factory)
        assert result["sent"] == 2
        assert result["bulk_errors"] == 0
        assert result["ik_probe"] == "absent"
        assert result["indices_created"] == [ENTITY_INDEX, ASSET_INDEX]
        # Both indices were ensured before the bulk went out.
        assert [c["index"] for c in core.client.indices.create_calls] == [
            ENTITY_INDEX, ASSET_INDEX]
        assert len(core.client.bulk_calls) == 1


class TestBuildEsIndexWriter:
    def test_flag_off_returns_none_even_with_host_set(self, monkeypatch):
        monkeypatch.delenv(xw.ENV_SYNC_ON_INGEST, raising=False)
        monkeypatch.setenv(xw.ENV_ES_HOST, "http://127.0.0.1:9210")
        assert build_es_index_writer(core=FakeCore()) is None

    def test_flag_on_with_injected_core_builds_the_writer(self, monkeypatch):
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, "true")
        monkeypatch.setenv(xw.ENV_ES_HOST, "http://127.0.0.1:9210")
        core = FakeCore()
        writer = build_es_index_writer(core=core)
        assert isinstance(writer, EsIndexWriter)
        assert writer.core is core
        assert writer.entity_index == ENTITY_INDEX

    def test_flag_on_without_host_returns_none(self, monkeypatch):
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, "1")
        monkeypatch.delenv(xw.ENV_ES_HOST, raising=False)
        assert build_es_index_writer() is None

    def test_lazy_sdk_core_construction(self, monkeypatch):
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, "1")
        monkeypatch.setenv(xw.ENV_ES_HOST, "http://127.0.0.1:9210")
        monkeypatch.setenv(xw.ENV_ES_API_KEY, "kw-test-key")

        built = {}

        class FakeSdkCore:
            def __init__(self, host=None, api_key=None, **kwargs):
                built["host"] = host
                built["api_key"] = api_key

        fake_mod = type(sys)("fake_es_core_mod")
        fake_mod.ElasticSearchCore = FakeSdkCore
        monkeypatch.setitem(sys.modules,
                            "nexent.vector_database.elasticsearch_core",
                            fake_mod)
        writer = build_es_index_writer()
        assert built == {"host": "http://127.0.0.1:9210",
                         "api_key": "kw-test-key"}
        assert isinstance(writer, EsIndexWriter)


def test_sync_enabled_parses_truthy_values(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, value)
        assert sync_enabled() is True
    for value in ("0", "false", "", "off"):
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, value)
        assert sync_enabled() is False


def test_dense_writeback_reason_is_the_audit_string():
    assert DENSE_WRITEBACK_REASON == (
        "embedding_model_not_resolvable_at_ingestion_time")
    w = EsIndexWriter(FakeCore())
    assert w.audit["dense_writeback"] == DENSE_WRITEBACK_REASON


# ---------------------------------------------------------------------------
# The ingest hook in pipeline/ingest_graph: failure isolation
# ---------------------------------------------------------------------------

from services.knowevo.pipeline import ingest_graph as ig
from services.knowevo.schemas import (
    Entity,
    ExtractionResult,
)


class _HookStore:
    """Minimal PgStore surface used by _run (snapshot + run ledger)."""

    def __init__(self):
        self.recorded = 0

    async def load_ontology_snapshot(self, tenant_id):
        return {"classes": {}}

    async def is_extract_done(self, tenant_id, span_hash):
        return False

    async def record_extract_run(self, tenant_id, run_id, span_hash, channel,
                                 tokens_spent, **diagnostics):
        self.recorded += 1
        return True


class _HookSvc:
    """Canned single-entity extraction; merge_delta returns a summary."""

    def __init__(self, **kwargs):
        pass

    async def extract(self, span, ontology_summary=None):
        return ExtractionResult(
            channel="llm",
            entities=[Entity(name="二甲双胍", class_ref="Drug")])

    async def merge_delta(self, results):
        return {"added": 1, "merged": 0, "superseded": 0,
                "contended": 0, "pending": 0}


HOOK_TENANT = "tenant-es-hook"


def _write_batch(tmp_path):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({
        "tenant_id": HOOK_TENANT,
        "docs": [{"doc_id": "doc-1", "chunks": [
            {"chunk_idx": 0, "modality": "text", "text": "hello world"}]}],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _wire(monkeypatch, tmp_path):
    monkeypatch.setattr(kg_service, "PgStore", lambda: _HookStore())
    monkeypatch.setattr(kg_service, "KGService", _HookSvc)
    monkeypatch.setattr(ig, "_EchoLLM", lambda: object())
    monkeypatch.setattr(ig, "COST_LEDGER_PATH", tmp_path / "ledger.md")


class TestIngestHook:
    def test_hook_reconciles_the_run_tenant_after_completion(self,
                                                             monkeypatch,
                                                             tmp_path,
                                                             capsys):
        _wire(monkeypatch, tmp_path)
        calls = []

        def fake_sync(tenant_id, **kwargs):
            calls.append(tenant_id)
            return {"sent": 1}

        monkeypatch.setattr(xw, "sync_tenant_entities", fake_sync)
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, "1")

        rc = ig.main(["--batch", str(_write_batch(tmp_path))])
        report = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert calls == [HOOK_TENANT]  # exactly once, with the run's tenant
        assert report["extracted"] == 1
        assert report["errors"] == []

    def test_es_failure_leaves_the_ingest_result_untouched(self, monkeypatch,
                                                           tmp_path, capsys,
                                                           caplog):
        caplog.set_level(logging.DEBUG,
                         logger="services.knowevo.pipeline.ingest_graph")
        _wire(monkeypatch, tmp_path)

        def broken_sync(tenant_id, **kwargs):
            raise RuntimeError("es cluster down")

        monkeypatch.setattr(xw, "sync_tenant_entities", broken_sync)
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, "1")

        rc = ig.main(["--batch", str(_write_batch(tmp_path))])
        report = json.loads(capsys.readouterr().out)

        assert rc == 0, "ES failure must not change the run's exit code"
        assert report["extracted"] == 1, "ingest result unchanged"
        assert report["errors"] == []
        # _run aggregates via getattr on the merge_delta return; the fake's
        # dict return therefore aggregates to zeros (existing behaviour).
        assert report["merged"] == {"added": 0, "merged": 0, "superseded": 0,
                                    "contended": 0, "pending": 0}
        assert any("es entity reconcile skipped" in r.getMessage()
                   and r.levelno == logging.DEBUG
                   for r in caplog.records), (
            "the failure may only surface as a debug log")

    def test_dry_run_never_triggers_the_sync(self, monkeypatch, tmp_path):
        _wire(monkeypatch, tmp_path)

        def forbidden(tenant_id, **kwargs):
            raise AssertionError("dry-run must not sync")

        monkeypatch.setattr(xw, "sync_tenant_entities", forbidden)
        monkeypatch.setenv(xw.ENV_SYNC_ON_INGEST, "1")

        rc = ig.main(["--batch", str(_write_batch(tmp_path)), "--dry-run"])
        assert rc == 0
