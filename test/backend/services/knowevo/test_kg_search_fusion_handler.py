"""Wiring: kg_search_handler fusion + bit-for-bit fallback (offline).

The handler tries the three-way RRF fusion only when the ES raw-list
adapter is configured (env-gated factory, monkeypatched here); every
other outcome - missing env, fusion exception, empty fusion - must
reproduce the pre-L6 lookup path bit for bit, including the structured
error semantics. The fused path may change the entity-card ORDER only:
its members are exactly the ``store.neighbors`` result of the fused
seeds (nothing invented), which is also the observable credential that
the BM25 route really ran (the probe entity f below only enters through
it).

Fixture graph (query "降" matches seeds a, d only):

    a(降糖灵) - b(糖平片) - f(肽键说)      d(降压灵, isolated seed)
    a - e(酶抑制)
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
import services.knowevo.es_raw_list as es_raw_list_mod
from services.knowevo.graph_store import EdgeCard, EntityCard, PgJsonbGraphStore

from mcp_servers.knowevo_mcp.schemas import KGSearchInput, KGSearchOutput
from mcp_servers.knowevo_mcp.server import _store, kg_search_handler

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeStore:
    """In-memory GraphStore; records every seam call."""

    def __init__(self):
        self.entities = {
            "Drug:a": EntityCard(stable_id="Drug:a", name="降糖灵",
                                 class_ref="Drug"),
            "Drug:d": EntityCard(stable_id="Drug:d", name="降压灵",
                                 class_ref="Drug"),
            "Drug:b": EntityCard(stable_id="Drug:b", name="糖平片",
                                 class_ref="Drug"),
            "Drug:e": EntityCard(stable_id="Drug:e", name="酶抑制",
                                 class_ref="Drug"),
            "Drug:f": EntityCard(stable_id="Drug:f", name="肽键说",
                                 class_ref="Drug"),
        }
        self.edges = [
            EdgeCard(id="e1", src="Drug:a", dst="Drug:b", rel_type="r",
                     claim="c1"),
            EdgeCard(id="e2", src="Drug:a", dst="Drug:e", rel_type="r",
                     claim="c2"),
            EdgeCard(id="e3", src="Drug:b", dst="Drug:f", rel_type="r",
                     claim="c3"),
        ]
        self.lookup_calls = []
        self.neighbors_calls = []

    async def entity_lookup(self, tenant_id, query, top_k=5):
        self.lookup_calls.append((tenant_id, query, top_k))
        hits = [c for c in self.entities.values() if query in c.name]
        return hits[:top_k]

    async def neighbors(self, tenant_id, entity_ids, rel_types=None, hop=1,
                        valid_view=True, as_of=None):
        self.neighbors_calls.append(list(entity_ids))
        ids = set(entity_ids)
        edges = [e for e in self.edges if e.src in ids or e.dst in ids]
        seen = set(entity_ids)
        for e in edges:
            seen.update((e.src, e.dst))
        entities = [self.entities[sid] for sid in self.entities
                    if sid in seen]
        return SimpleNamespace(entities=entities, edges=edges)


class BoomLookupStore(FakeStore):
    async def entity_lookup(self, *a, **k):
        raise RuntimeError("db down")

    async def neighbors(self, *a, **k):
        raise RuntimeError("db down")


class FakeClient:
    """The ES raw-list adapter surface the fused path needs."""

    def __init__(self, bm25=None, bm25_error=None):
        self.bm25 = list(bm25 or [])
        self.bm25_error = bm25_error
        self.bm25_calls = []

    def entity_bm25_hits(self, tenant_id, query, top_k):
        self.bm25_calls.append((tenant_id, query, top_k))
        if self.bm25_error is not None:
            raise self.bm25_error
        return list(self.bm25)

    def dense_entity_hits(self, tenant_id, query, top_k):
        return []


def _install_client(monkeypatch, client):
    monkeypatch.setattr(es_raw_list_mod, "build_es_raw_client",
                        lambda: client)


@pytest.fixture(autouse=True)
def _no_es_env(monkeypatch):
    """Default state: no ES env => every test starts on the legacy path."""
    for name in ("ELASTICSEARCH_HOST", "ELASTICSEARCH_API_KEY",
                 "KW_ENTITY_ES_INDEX"):
        monkeypatch.delenv(name, raising=False)


LEGACY_KEYS = {"entities", "edges", "valid_view", "used_tokens", "elapsed_ms"}


class TestLegacyFallback:
    async def _legacy_expectation(self, store, top_k):
        hits = await store.entity_lookup(TENANT, "降", top_k)
        sub = await store.neighbors(TENANT, [h.stable_id for h in hits],
                                    hop=1)
        return sub

    def test_env_missing_reproduces_pre_l6_path_bit_for_bit(self):
        store = FakeStore()
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=5), store=store,
            tenant_id=TENANT))
        sub = asyncio.run(self._legacy_expectation(FakeStore(), 5))
        assert [e.stable_id for e in out.entities] == [
            e.stable_id for e in sub.entities]
        assert [e.id for e in out.edges] == [e.id for e in sub.edges]
        # the lookup path is the only store traffic (no graph-route BFS)
        assert len(store.lookup_calls) == 1
        assert len(store.neighbors_calls) == 1
        assert isinstance(out, KGSearchOutput)

    def test_store_failure_still_answers_kg_search_failed(self):
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="降"), store=BoomLookupStore(),
            tenant_id=TENANT))
        assert out == {"error_code": "kg_search_failed",
                       "hint": "graph query failed: RuntimeError"}

    def test_empty_lookup_keeps_empty_shape(self):
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="不存在"), store=FakeStore(),
            tenant_id=TENANT))
        assert isinstance(out, KGSearchOutput)
        assert out.entities == [] and out.edges == []


class TestFusedPath:
    def test_fusion_reranks_seeds_and_keeps_neighbors_membership(self, monkeypatch):
        # bm25 = [f, b]: f (ES rank1) is only reachable through the fusion
        # route - its presence proves the ES route really ran.
        client = FakeClient(bm25=[{"id": "Drug:f", "es_score": 9.0},
                                  {"id": "Drug:b", "es_score": 4.0}])
        _install_client(monkeypatch, client)
        store = FakeStore()
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=2), store=store,
            tenant_id=TENANT))
        assert isinstance(out, KGSearchOutput)
        # Hand-computed RRF (k=60): b = 1/62 + 1/63 = 0.032002 (bm25 r2 +
        # graph r3) > a = f = 1/61 (tie broken by best_rank 1 then id) >
        # d = 1/62 > e = 1/64. Fused order [b, a, f, d, e]; top_k=2 seeds
        # = [b, a]; their neighborhood = {a, b, e, f}.
        assert [e.stable_id for e in out.entities] == [
            "Drug:b", "Drug:a", "Drug:e", "Drug:f"]
        # seeds lead in fused-rank order, tail in (hop, -degree, sid)
        # order: e (hop 1, degree 1) then f (absent from BFS metadata).
        # Membership is exactly the neighbors() result - nothing invented.
        assert {e.stable_id for e in out.entities} == {
            "Drug:a", "Drug:b", "Drug:e", "Drug:f"}
        assert [e.id for e in out.edges] == ["e1", "e2", "e3"]
        # the fused seeds (not the lookup order) fed the neighborhood
        assert store.neighbors_calls[-1] == ["Drug:b", "Drug:a"]

    def test_fused_output_shape_field_set_is_unchanged(self, monkeypatch):
        client = FakeClient(bm25=[{"id": "Drug:f"}])
        original_factory = es_raw_list_mod.build_es_raw_client
        _install_client(monkeypatch, client)
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=2), store=FakeStore(),
            tenant_id=TENANT))
        # restore the real env-gated factory so the second call is the
        # TRUE legacy path (no ES env => fusion skipped), not fusion again
        monkeypatch.setattr(es_raw_list_mod, "build_es_raw_client",
                            original_factory)
        legacy = asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=2), store=FakeStore(),
            tenant_id=TENANT))
        assert set(out.model_dump(mode="json")) == LEGACY_KEYS
        assert set(legacy.model_dump(mode="json")) == LEGACY_KEYS
        assert out.used_tokens == 0 and isinstance(out.elapsed_ms, int)

    def test_bm25_route_receives_tenant_query_and_topk(self, monkeypatch):
        client = FakeClient(bm25=[{"id": "Drug:f"}])
        _install_client(monkeypatch, client)
        asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=3), store=FakeStore(),
            tenant_id=TENANT))
        assert client.bm25_calls == [(TENANT, "降", 3)]


class TestFusedFallback:
    def test_fusion_exception_falls_back_bit_for_bit(self, monkeypatch):
        client = FakeClient(bm25_error=RuntimeError("ES exploded"))
        _install_client(monkeypatch, client)
        store = FakeStore()
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=5), store=store,
            tenant_id=TENANT))
        fallback = asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=5), store=FakeStore(),
            tenant_id=TENANT))
        assert isinstance(out, KGSearchOutput)
        assert [e.stable_id for e in out.entities] == [
            e.stable_id for e in fallback.entities]
        assert [e.id for e in out.edges] == [e.id for e in fallback.edges]
        # legacy lookup path ran after the fusion failure
        assert store.lookup_calls == [(TENANT, "降", 5)]

    def test_all_routes_empty_falls_back_to_lookup(self, monkeypatch):
        # bm25 route empty AND graph route empty (no seed matches) =>
        # fusion has nothing to say; the lookup path answers instead.
        client = FakeClient(bm25=[])
        _install_client(monkeypatch, client)
        store = FakeStore()
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="不存在", top_k=5), store=store,
            tenant_id=TENANT))
        assert isinstance(out, KGSearchOutput)
        assert out.entities == [] and out.edges == []
        # graph-route BFS + legacy lookup both asked the store
        assert store.lookup_calls == [(TENANT, "不存在", 5),
                                      (TENANT, "不存在", 5)]

    def test_bm25_empty_degrades_to_graph_route_only(self, monkeypatch):
        # One honest degradation of the frozen RRF semantics: with the
        # bm25 list empty, fusion keeps the single non-empty route's own
        # order (graph route: hop/degree/id).
        client = FakeClient(bm25=[])
        _install_client(monkeypatch, client)
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="降", top_k=5), store=FakeStore(),
            tenant_id=TENANT))
        assert isinstance(out, KGSearchOutput)
        # Fusion keeps the single non-empty route's own order. Note the
        # membership consequence of different seeds: b is a fused seed
        # (hop 1 from a), so its edge e3 is explored and f joins the
        # neighborhood as a tail card - unlike the lookup path, whose
        # seeds [a, d] never see e3 at hop=1.
        assert [e.stable_id for e in out.entities] == [
            "Drug:a", "Drug:d", "Drug:b", "Drug:e", "Drug:f"]

    def test_fusion_failure_keeps_error_code_semantics(self, monkeypatch):
        # fusion blows up on the store seam, then the lookup path fails
        # too: the structured error must be exactly the pre-L6 one.
        _install_client(monkeypatch, FakeClient(bm25=[{"id": "Drug:f"}]))
        out = asyncio.run(kg_search_handler(
            KGSearchInput(query="降"), store=BoomLookupStore(),
            tenant_id=TENANT))
        assert out == {"error_code": "kg_search_failed",
                       "hint": "graph query failed: RuntimeError"}


class TestStoreInjection:
    def test_env_missing_store_is_unchanged(self):
        store = _store()
        assert isinstance(store, PgJsonbGraphStore)
        assert store.es_client is None

    def test_env_present_injects_the_raw_list_adapter(self, monkeypatch):
        monkeypatch.setenv("ELASTICSEARCH_HOST", "http://127.0.0.1:9210")
        monkeypatch.delenv("KW_ENTITY_ES_INDEX", raising=False)
        store = _store()
        assert isinstance(store, PgJsonbGraphStore)
        from services.knowevo.es_raw_list import ENTITY_INDEX_DEFAULT, EsRawListClient
        assert isinstance(store.es_client, EsRawListClient)
        assert store.es_client.entity_index == ENTITY_INDEX_DEFAULT
        # the injected adapter must satisfy the es_client seam the store
        # calls (entity_search), sync or async - this one is sync.
        assert callable(store.es_client.entity_search)
