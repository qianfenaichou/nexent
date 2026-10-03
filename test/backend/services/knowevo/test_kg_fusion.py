"""Wiring: the kg_search fusion factory (offline fakes).

``fused_entity_cards`` is the first production consumer of the frozen
``rrf_fusion.fuse`` kernel: it pulls the BM25 raw list (sync SDK call, so
it must be threaded off the event loop), keeps the dense slot honestly
empty with its audit reason, takes the deterministic graph route, fuses
the three lists in the shared stable_id space, and returns the fused
order plus the FusedHit audit (ranks / first_seen). Failures propagate
to the caller - the handler owns the silent fallback.
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
from services.knowevo.es_raw_list import DENSE_ENTITY_DISABLED_REASON
from services.knowevo.graph_store import EdgeCard, EntityCard
from services.knowevo.kg_fusion import FusionOutcome, fused_entity_cards

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeClient:
    """Records the BM25 route call; dense slot honestly empty."""

    def __init__(self, bm25=None, bm25_error=None):
        self.bm25 = list(bm25 or [])
        self.bm25_error = bm25_error
        self.bm25_calls = []
        self.dense_calls = []

    def entity_bm25_hits(self, tenant_id, query, top_k):
        self.bm25_calls.append((tenant_id, query, top_k))
        if self.bm25_error is not None:
            raise self.bm25_error
        return list(self.bm25)

    def dense_entity_hits(self, tenant_id, query, top_k):
        self.dense_calls.append((tenant_id, query, top_k))
        return []


class FakeStore:
    """Graph route: seeds matching "降" (a, d), a-b + a-e edges, hop=1."""

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
        }
        self.edges = [EdgeCard(id="e1", src="Drug:a", dst="Drug:b",
                               rel_type="r", claim="c"),
                      EdgeCard(id="e2", src="Drug:a", dst="Drug:e",
                               rel_type="r", claim="c")]

    async def entity_lookup(self, tenant_id, query, top_k=5):
        hits = [c for c in self.entities.values() if query in c.name]
        return hits[:top_k]

    async def neighbors(self, tenant_id, entity_ids, rel_types=None, hop=1,
                        valid_view=True, as_of=None):
        ids = set(entity_ids)
        edges = [e for e in self.edges if e.src in ids or e.dst in ids]
        seen = set(entity_ids)
        for e in edges:
            seen.update((e.src, e.dst))
        entities = [c for c in self.entities.values() if c.stable_id in seen]
        return SimpleNamespace(entities=entities, edges=edges)


class BoomStore:
    async def entity_lookup(self, *a, **k):
        raise RuntimeError("lookup down")

    async def neighbors(self, *a, **k):
        raise AssertionError("never reached")


def _bm25_hits(*sids):
    return [{"id": s, "es_score": 1.0} for s in sids]


class TestFusedEntityCards:
    def test_fused_order_and_audit_over_three_routes(self):
        client = FakeClient(bm25=_bm25_hits("Drug:b", "Drug:a"))
        outcome = asyncio.run(fused_entity_cards(
            client, FakeStore(), TENANT, "降", seed_top_k=5, hop=1))
        assert isinstance(outcome, FusionOutcome)
        # Hand-computed RRF (k=60). Graph route (seeds a,d; b,e neighbors)
        # = [a, d, b, e]. a: 1/(60+2) [bm25 rank2] + 1/(60+1) [graph r1]
        # = 0.03252; b: 1/61 [bm25 r1] + 1/63 [graph r3] = 0.03221;
        # d: 1/62 = 0.01613; e: 1/64 = 0.01563.
        assert outcome.ids == ("Drug:a", "Drug:b", "Drug:d", "Drug:e")
        first = outcome.hits[0]
        assert first.ranks == (2, None, 1)
        assert first.best_rank == 1
        assert first.first_seen == {"id": "Drug:a", "es_score": 1.0}
        assert outcome.bm25_count == 2 and outcome.dense_count == 0
        assert outcome.graph_count == 4  # a, d seeds + b, e neighbors

    def test_dense_slot_empty_with_frozen_audit_reason(self):
        client = FakeClient()
        outcome = asyncio.run(fused_entity_cards(
            client, FakeStore(), TENANT, "降", seed_top_k=5, hop=1))
        assert client.dense_calls == [(TENANT, "降", 5)]
        assert outcome.dense_count == 0
        assert outcome.dense_reason == DENSE_ENTITY_DISABLED_REASON
        assert DENSE_ENTITY_DISABLED_REASON == "entity_index_has_no_embedding_field"

    def test_graph_route_metadata_rides_on_outcome(self):
        client = FakeClient(bm25=_bm25_hits("Drug:a"))
        outcome = asyncio.run(fused_entity_cards(
            client, FakeStore(), TENANT, "降", seed_top_k=5, hop=1))
        assert outcome.graph_route is not None
        assert outcome.graph_route.hop_by_id["Drug:a"] == 0
        assert outcome.graph_route.hop_by_id["Drug:b"] == 1
        assert outcome.graph_route.degree_by_id["Drug:a"] == 2

    def test_bm25_route_call_args_and_threading(self):
        client = FakeClient()
        asyncio.run(fused_entity_cards(
            client, FakeStore(), TENANT, "降", seed_top_k=3, hop=2))
        # The sync SDK call must still be made (off-loop) with the same args.
        assert client.bm25_calls == [(TENANT, "降", 3)]

    def test_empty_routes_yield_empty_fusion(self):
        store = FakeStore()
        client = FakeClient()
        outcome = asyncio.run(fused_entity_cards(
            client, store, TENANT, "不存在", seed_top_k=5, hop=1))
        assert outcome.ids == () and outcome.hits == ()
        assert outcome.graph_route.cards == ()

    def test_client_none_is_a_value_error_not_a_silent_empty(self):
        with pytest.raises(ValueError):
            asyncio.run(fused_entity_cards(
                None, FakeStore(), TENANT, "降", seed_top_k=5, hop=1))

    def test_route_failures_propagate_to_caller(self):
        with pytest.raises(RuntimeError):
            asyncio.run(fused_entity_cards(
                FakeClient(bm25_error=RuntimeError("ES down")),
                FakeStore(), TENANT, "降", seed_top_k=5, hop=1))
        with pytest.raises(RuntimeError):
            asyncio.run(fused_entity_cards(
                FakeClient(), BoomStore(), TENANT, "降", seed_top_k=5,
                hop=1))

    def test_none_bm25_list_treated_as_empty(self):
        client = FakeClient()
        client.entity_bm25_hits = lambda *a: None
        outcome = asyncio.run(fused_entity_cards(
            client, FakeStore(), TENANT, "降", seed_top_k=5, hop=1))
        # Graph route only: single non-empty list keeps its own order.
        assert outcome.ids == ("Drug:a", "Drug:d", "Drug:b", "Drug:e")
        assert outcome.bm25_count == 0

    def test_deterministic_double_run(self):
        def run():
            client = FakeClient(bm25=_bm25_hits("Drug:b", "Drug:a"))
            return asyncio.run(fused_entity_cards(
                client, FakeStore(), TENANT, "降", seed_top_k=5, hop=1))
        first, second = run(), run()
        assert first.ids == second.ids
        assert [h.ranks for h in first.hits] == [h.ranks for h in second.hits]

    def test_k_forwarded_to_kernel(self):
        with pytest.raises(ValueError):
            asyncio.run(fused_entity_cards(
                FakeClient(bm25=_bm25_hits("Drug:a")), FakeStore(), TENANT,
                "降", seed_top_k=5, hop=1, k=-1))
