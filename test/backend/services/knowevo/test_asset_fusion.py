"""asset_search fusion factory + ignition gate (offline fakes).

``fused_asset_hits`` is the asset-context consumer of the frozen RRF
kernel. Id space is strictly ``AssetHit.id``. The ignition gate is the
anti-theatre rule from L6-M2: **at least two non-empty routes** before
fuse output is handed to the caller; a single-route list must not be
dressed up as fusion. Dense is honestly empty this round, so with only
BM25 the gate stays closed and the handler keeps today's path bit for bit.

Hand-computed RRF examples use k=60 (Cormack default) with independent
literals - never a re-implementation of the kernel formula.
"""
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from services.knowevo.asset_fusion import AssetFusionOutcome, fused_asset_hits
from services.knowevo.asset_raw_list import ASSET_INDEX_HAS_NO_EMBEDDING_FIELD

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeClient:
    """Records route calls; dense is honestly empty by default."""

    def __init__(self, bm25=None, dense=None, bm25_error=None):
        self.bm25 = list(bm25 or [])
        self.dense = list(dense or [])
        self.bm25_error = bm25_error
        self.bm25_calls = []
        self.dense_calls = []

    def asset_bm25_hits(self, tenant_id, query, top_k):
        self.bm25_calls.append((tenant_id, query, top_k))
        if self.bm25_error is not None:
            raise self.bm25_error
        return list(self.bm25)

    def asset_dense_hits(self, tenant_id, query, top_k):
        self.dense_calls.append((tenant_id, query, top_k))
        return list(self.dense)


def _hit(hid, **extra):
    base = {"id": hid, "title": f"t-{hid}", "asset_no": f"n-{hid}",
            "es_score": 1.0}
    base.update(extra)
    return base


class TestIgnitionGate:
    def test_single_route_bm25_does_not_fire(self):
        client = FakeClient(bm25=[_hit("a1"), _hit("a2"), _hit("a3")])
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert isinstance(outcome, AssetFusionOutcome)
        assert outcome.fired is False
        assert outcome.ids == ()
        assert outcome.hits == ()
        assert outcome.bm25_count == 3
        assert outcome.dense_count == 0
        assert outcome.dense_reason == ASSET_INDEX_HAS_NO_EMBEDDING_FIELD

    def test_single_route_dense_does_not_fire(self):
        client = FakeClient(dense=[_hit("d1"), _hit("d2")])
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert outcome.fired is False
        assert outcome.bm25_count == 0
        assert outcome.dense_count == 2

    def test_both_routes_empty_does_not_fire(self):
        client = FakeClient()
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert outcome.fired is False
        assert outcome.ids == ()

    def test_two_routes_fire_and_fuse(self):
        # Hand-computed RRF (k=60):
        # a1: bm25 rank1 + dense rank2 -> 1/61 + 1/62 = 0.032523
        # a2: bm25 rank2               -> 1/62 = 0.016129
        # d1:               dense rank1 -> 1/61 = 0.016393
        client = FakeClient(
            bm25=[_hit("a1"), _hit("a2")],
            dense=[_hit("d1"), _hit("a1")],
        )
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert outcome.fired is True
        assert outcome.ids == ("a1", "d1", "a2")
        first = outcome.hits[0]
        assert first.id == "a1"
        assert first.ranks == (1, 2)
        assert first.best_rank == 1
        assert outcome.bm25_count == 2
        assert outcome.dense_count == 2

    def test_one_hit_each_still_two_routes_fires(self):
        """Gate counts non-empty ROUTES, not total hits."""
        client = FakeClient(bm25=[_hit("a1")], dense=[_hit("d1")])
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert outcome.fired is True
        assert set(outcome.ids) == {"a1", "d1"}

    def test_gate_matrix_is_stable(self):
        cases = [
            ([], [], False),
            ([_hit("a1")], [], False),
            ([_hit("a1"), _hit("a2")], [], False),
            ([], [_hit("d1")], False),
            ([], [_hit("d1"), _hit("d2")], False),
            ([_hit("a1")], [_hit("d1")], True),
            ([_hit("a1"), _hit("a2")], [_hit("d1")], True),
        ]
        for bm25, dense, expected in cases:
            client = FakeClient(bm25=bm25, dense=dense)
            outcome = asyncio.run(
                fused_asset_hits(client, TENANT, "q", top_k=5))
            assert outcome.fired is expected, (bm25, dense)


class TestFusionSemantics:
    def test_cross_route_duplicate_sums(self):
        # a1 in both routes at rank 1 -> 1/61 + 1/61 = 2/61
        client = FakeClient(bm25=[_hit("a1"), _hit("a2")],
                            dense=[_hit("a1")])
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert outcome.ids[0] == "a1"
        assert outcome.hits[0].score == pytest.approx(2 / 61)

    def test_none_lists_treated_as_empty_and_gate_closed(self):
        client = FakeClient()
        client.asset_bm25_hits = lambda *a: None
        client.asset_dense_hits = lambda *a: None
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert outcome.fired is False
        assert outcome.bm25_count == 0
        assert outcome.dense_count == 0

    def test_route_failure_propagates(self):
        with pytest.raises(RuntimeError):
            asyncio.run(fused_asset_hits(
                FakeClient(bm25_error=RuntimeError("ES down")),
                TENANT, "q", top_k=5))

    def test_client_none_is_value_error(self):
        with pytest.raises(ValueError):
            asyncio.run(fused_asset_hits(None, TENANT, "q", top_k=5))

    def test_k_forwarded_to_kernel(self):
        with pytest.raises(ValueError):
            asyncio.run(fused_asset_hits(
                FakeClient(bm25=[_hit("a1")], dense=[_hit("d1")]),
                TENANT, "q", top_k=5, k=-1))

    def test_bm25_threaded_off_loop_args_passed(self):
        client = FakeClient()
        asyncio.run(fused_asset_hits(client, TENANT, "查询", top_k=3))
        assert client.bm25_calls == [(TENANT, "查询", 3)]
        assert client.dense_calls == [(TENANT, "查询", 3)]

    def test_deterministic_double_run(self):
        def run():
            client = FakeClient(bm25=[_hit("a2"), _hit("a1")],
                                dense=[_hit("a1"), _hit("d1")])
            return asyncio.run(
                fused_asset_hits(client, TENANT, "q", top_k=5))
        first, second = run(), run()
        assert first.ids == second.ids
        assert [h.ranks for h in first.hits] == [h.ranks for h in second.hits]

    def test_id_space_is_asset_hit_id(self):
        """Fused ids come from the adapter's AssetHit.id field only."""
        client = FakeClient(bm25=[_hit("uuid-1")], dense=[_hit("uuid-1")])
        outcome = asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
        assert outcome.ids == ("uuid-1",)
        assert all(isinstance(i, str) for i in outcome.ids)


class TestCrossSpaceGuard:
    def test_document_id_must_not_be_silently_accepted_as_mixed_space(self):
        """A hit tagged as document-space id is a programming error.

        The adapter is the only place that maps into AssetHit.id; the
        fusion factory refuses hits that claim a foreign id space marker
        so a future document-route cannot silently join this fuse.
        """
        bad = _hit("doc-9")
        bad["id_space"] = "document"
        client = FakeClient(bm25=[bad], dense=[_hit("a1")])
        with pytest.raises(ValueError):
            asyncio.run(fused_asset_hits(client, TENANT, "q", top_k=5))
