"""L6-M2 wiring: EsRawListClient + env-gated factory (offline, fake core).

Layer 1 (always runs): the adapter must reshape raw ``exec_query`` hits
(top-level ``{score, document, index}``, id inside ``document``) into
single-id-space hits the RRF kernel can consume, force tenant isolation on
every route, and refuse to pretend a dense route exists while the entity
index carries no embedding field. The factory must return None when the
ES env is absent (= every caller keeps today's behaviour bit for bit) and
must construct the SDK core lazily so an offline test never imports a
network client at module import time.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.es_raw_list import (
    DENSE_ENTITY_DISABLED_REASON,
    ENTITY_INDEX_DEFAULT,
    EsRawListClient,
    build_es_raw_client,
)

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


class FakeEsClient:
    """Records raw ``search(index=, body=)`` calls (the transport the
    adapter uses after the 2026-09-30 field-schema correction); returns a
    canned raw ES response."""

    def __init__(self, hits):
        self.hits = list(hits)
        self.calls = []

    def search(self, index=None, body=None, **kwargs):
        self.calls.append({"index": list(index), "body": body})
        return {"hits": {"hits": list(self.hits)}}


class FakeCore:
    """Duck-typed ES core: only ``client.search`` is ever consumed."""

    def __init__(self, hits=None):
        self.client = FakeEsClient(hits or [])

    @property
    def calls(self):
        return self.client.calls

    def semantic_search(self, *a, **k):  # must never be called this round
        raise AssertionError("semantic_search must not be called (no dense route)")


def _es_hit(stable_id, name, score=2.0):
    return {
        "_score": score,
        "_source": {
            "stable_id": stable_id,
            "name": name,
            "class_ref": "Drug",
            "props": {"x": 1},
            "aliases": [{"alias": "格华止"}],
        },
        "_index": "knowevo_entities_m2",
    }


class TestEntityBm25Hits:
    def test_reshapes_to_single_id_space(self):
        core = FakeCore([_es_hit("Drug:a", "二甲双胍", score=3.0),
                         _es_hit("Drug:b", "格华止", score=1.0)])
        client = EsRawListClient(core)
        hits = client.entity_bm25_hits(TENANT, "二甲双胍", 5)
        assert [h["id"] for h in hits] == ["Drug:a", "Drug:b"]
        assert hits[0]["es_score"] == 3.0
        assert hits[0]["name"] == "二甲双胍"

    def test_hits_without_stable_id_are_dropped(self):
        core = FakeCore([
            {"_score": 2.0, "_source": {"name": "no id here"},
             "_index": "x"},
            _es_hit("Drug:a", "二甲双胍"),
            {"_score": 1.0, "_source": "not-a-mapping",
             "_index": "x"},
        ])
        client = EsRawListClient(core)
        hits = client.entity_bm25_hits(TENANT, "q", 5)
        assert [h["id"] for h in hits] == ["Drug:a"]

    def test_tenant_filter_is_forced_and_index_passed(self):
        core = FakeCore([_es_hit("Drug:a", "二甲双胍")])
        client = EsRawListClient(core, entity_index="knowevo_entities_m2")
        client.entity_bm25_hits(TENANT, "二甲双胍", 7)
        assert len(core.calls) == 1
        call = core.calls[0]
        assert call["index"] == ["knowevo_entities_m2"]
        body = call["body"]
        assert body["size"] == 7
        must = body["query"]["bool"]["must"]
        assert must == [{"multi_match": {
            "query": "二甲双胍",
            "fields": ["name", "aliases.alias"],
            "operator": "and",
        }}]
        assert body["query"]["bool"]["filter"] == [
            {"term": {"tenant_id": TENANT}}]

    def test_blank_query_hits_nothing(self):
        core = FakeCore([_es_hit("Drug:a", "二甲双胍")])
        client = EsRawListClient(core)
        assert client.entity_bm25_hits(TENANT, "   ", 5) == []
        assert core.calls == []


class TestEntitySearchSeam:
    def test_returns_graph_store_compatible_dicts(self):
        core = FakeCore([_es_hit("Drug:a", "二甲双胍")])
        client = EsRawListClient(core)
        cards = asyncio.run(client.entity_search(TENANT, "二甲双胍", 5))
        assert cards == [{
            "stable_id": "Drug:a", "name": "二甲双胍", "class_ref": "Drug",
            "props": {"x": 1}, "aliases": ["格华止"],
        }]

    def test_missing_name_or_stable_id_dropped(self):
        core = FakeCore([
            {"_score": 2.0, "_source": {"stable_id": "Drug:a"},
             "_index": "x"},
            {"_score": 1.0, "_source": {"name": "no stable id"},
             "_index": "x"},
            _es_hit("Drug:b", "格华止"),
        ])
        client = EsRawListClient(core)
        cards = asyncio.run(client.entity_search(TENANT, "q", 5))
        assert [c["stable_id"] for c in cards] == ["Drug:b"]

    def test_order_is_es_order(self):
        core = FakeCore([_es_hit("Drug:b", "格华止", score=5.0),
                         _es_hit("Drug:a", "二甲双胍", score=1.0)])
        client = EsRawListClient(core)
        cards = asyncio.run(client.entity_search(TENANT, "q", 5))
        assert [c["stable_id"] for c in cards] == ["Drug:b", "Drug:a"]


class TestDenseEntityHits:
    def test_honest_empty_without_embedding_model(self):
        client = EsRawListClient(FakeCore())
        assert client.dense_entity_hits(TENANT, "q", 5) == []

    def test_honest_empty_even_with_embedding_model(self):
        model = SimpleNamespace(model_type="embedding")
        client = EsRawListClient(FakeCore(), embedding_model=model)
        assert client.dense_entity_hits(TENANT, "q", 5) == []

    def test_multimodal_guard_never_touches_semantic_search(self):
        model = SimpleNamespace(model_type="multimodal")
        client = EsRawListClient(FakeCore(), embedding_model=model)
        assert client.dense_entity_hits(TENANT, "q", 5) == []

    def test_disabled_reason_constant_is_the_audit_string(self):
        assert DENSE_ENTITY_DISABLED_REASON == "entity_index_has_no_embedding_field"


class TestBuildEsRawClient:
    def test_env_missing_returns_none(self, monkeypatch):
        for name in ("ELASTICSEARCH_HOST", "ELASTICSEARCH_API_KEY",
                     "KW_ENTITY_ES_INDEX"):
            monkeypatch.delenv(name, raising=False)
        core = FakeCore()
        assert build_es_raw_client(core=core) is None
        assert core.calls == []  # no ES call was even possible

    def test_env_present_uses_injected_core_and_env_index(self, monkeypatch):
        monkeypatch.setenv("ELASTICSEARCH_HOST", "http://127.0.0.1:9210")
        monkeypatch.setenv("KW_ENTITY_ES_INDEX", "knowevo_entities_test")
        core = FakeCore()
        client = build_es_raw_client(core=core)
        assert isinstance(client, EsRawListClient)
        assert client.entity_index == "knowevo_entities_test"
        assert client.core is core

    def test_env_present_index_defaults_to_frozen_name(self, monkeypatch):
        monkeypatch.setenv("ELASTICSEARCH_HOST", "http://127.0.0.1:9210")
        monkeypatch.delenv("KW_ENTITY_ES_INDEX", raising=False)
        client = build_es_raw_client(core=FakeCore())
        assert client.entity_index == ENTITY_INDEX_DEFAULT
        assert ENTITY_INDEX_DEFAULT == "knowevo_entities_m2"

    def test_lazy_sdk_core_construction_with_host_and_key(self, monkeypatch):
        """No injected core => the SDK core is built lazily from the env."""
        monkeypatch.setenv("ELASTICSEARCH_HOST", "http://127.0.0.1:9210")
        monkeypatch.setenv("ELASTICSEARCH_API_KEY", "kw-test-key")
        monkeypatch.delenv("KW_ENTITY_ES_INDEX", raising=False)

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
        client = build_es_raw_client()
        assert built == {"host": "http://127.0.0.1:9210",
                         "api_key": "kw-test-key"}
        assert client.entity_index == ENTITY_INDEX_DEFAULT
