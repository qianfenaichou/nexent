"""L6-M2 wiring: deterministic graph-route composition (offline fakes).

The graph route of the kg_search fusion is query -> entity_lookup seeds ->
per-hop BFS over the frozen ``neighbors`` seam -> deterministically ordered
EntityCard list. Ordering is the frozen key ``(hop_distance, -degree,
stable_id)`` - no PPR, no random walk, no dependence on PG's unordered
ilike rows (recon risk R5). ``ordering_key`` must be a total order: ids
absent from the BFS metadata sort after all known ones.

Fixture graph (query "降" matches exactly the two seed names):

    a(降糖灵) - b(糖平片) - c(肽键说)      d(降压灵, isolated seed)
    a - e(酶抑制)
"""
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from services.knowevo.graph_retrieve import (
    DEFAULT_SEED_TOP_K,
    graph_route_cards,
    ordering_key,
)
from services.knowevo.graph_store import EdgeCard, EntityCard

TENANT = "11111111-1111-1111-1111-111111111111"


class FakeGraphStore:
    """entity_lookup + per-hop neighbors over an in-memory graph."""

    def __init__(self, entities, edges, lookup_order=None):
        self.entities = {e.stable_id: e for e in entities}
        self.edges = list(edges)
        self.lookup_order = list(lookup_order or [e.stable_id for e in entities])
        self.lookup_calls = []
        self.neighbors_calls = []

    async def entity_lookup(self, tenant_id, query, top_k=5):
        self.lookup_calls.append((tenant_id, query, top_k))
        hits = [self.entities[sid] for sid in self.lookup_order
                if sid in self.entities and query in self.entities[sid].name]
        return hits[:top_k]

    async def neighbors(self, tenant_id, entity_ids, rel_types=None, hop=1,
                        valid_view=True, as_of=None):
        assert hop == 1, "the BFS must walk one layer per call"
        self.neighbors_calls.append(list(entity_ids))
        ids = set(entity_ids)
        edges = [e for e in self.edges if e.src in ids or e.dst in ids]
        seen = set(entity_ids)
        for e in edges:
            seen.update((e.src, e.dst))
        entities = [self.entities[sid] for sid in self.entities if sid in seen]
        return SimpleSubgraph(entities=entities, edges=edges)


class SimpleSubgraph:
    def __init__(self, entities, edges):
        self.entities = entities
        self.edges = edges


def _card(sid, name):
    return EntityCard(stable_id=sid, name=name, class_ref="Drug")


def _edge(eid, src, dst):
    return EdgeCard(id=eid, src=src, dst=dst, rel_type="r", claim="c")


SEED_A = _card("Drug:a", "降糖灵")
NODE_B = _card("Drug:b", "糖平片")
NODE_C = _card("Drug:c", "肽键说")
SEED_D = _card("Drug:d", "降压灵")
NODE_E = _card("Drug:e", "酶抑制")
GRAPH_EDGES = [
    _edge("e1", "Drug:a", "Drug:b"),
    _edge("e2", "Drug:b", "Drug:c"),
    _edge("e3", "Drug:a", "Drug:e"),
]


def _store():
    return FakeGraphStore([SEED_A, NODE_B, NODE_C, SEED_D, NODE_E],
                          GRAPH_EDGES)


class TestGraphRouteCards:
    def test_seeds_hop0_neighbors_hop1_two_hop_layering(self):
        store = _store()
        route = asyncio.run(graph_route_cards(
            store, TENANT, "降", seed_top_k=5, hop=2))
        # a and d are the lookup seeds (hop 0); b and e are 1 hop from a;
        # c is 2 hops from a (through b).
        assert route.hop_by_id == {
            "Drug:a": 0, "Drug:d": 0, "Drug:b": 1, "Drug:e": 1, "Drug:c": 2,
        }
        # Degree counted over every BFS edge: a:2, b:2, e:1, c:1, d:0.
        assert route.degree_by_id == {
            "Drug:a": 2, "Drug:b": 2, "Drug:c": 1, "Drug:e": 1, "Drug:d": 0,
        }

    def test_frozen_ordering_key_hops_then_degree_then_id(self):
        store = _store()
        route = asyncio.run(graph_route_cards(
            store, TENANT, "降", seed_top_k=5, hop=2))
        # hop 0 (degree desc: a(2), d(0)); hop 1 (b(2), e(1)); hop 2: c.
        assert [c.stable_id for c in route.cards] == [
            "Drug:a", "Drug:d", "Drug:b", "Drug:e", "Drug:c"]

    def test_seed_order_from_lookup_does_not_break_ties(self):
        """A degree-0 seed must never precede a same-hop seed with edges
        just because lookup returned it first (PG ilike rows are unordered;
        the route order is the key's order)."""
        store = _store()
        store.lookup_order = ["Drug:d", "Drug:a"]
        route = asyncio.run(graph_route_cards(store, TENANT, "降",
                                              seed_top_k=5, hop=1))
        assert [c.stable_id for c in route.cards][:2] == ["Drug:a", "Drug:d"]

    def test_empty_lookup_yields_empty_route_without_neighbors(self):
        store = FakeGraphStore([], [])
        route = asyncio.run(graph_route_cards(store, TENANT, "不存在",
                                              seed_top_k=5, hop=1))
        assert route.cards == ()
        assert route.hop_by_id == {} and route.degree_by_id == {}
        assert store.neighbors_calls == []

    def test_blank_query_yields_empty_route(self):
        store = _store()
        route = asyncio.run(graph_route_cards(store, TENANT, "  ",
                                              seed_top_k=5, hop=1))
        assert route.cards == ()
        assert store.lookup_calls == []

    def test_dedup_entity_reached_twice_counted_once(self):
        # b reachable from both seeds a and d -> one card, hop 1; its
        # degree spans edges seen across BOTH BFS layers (e1 + e2 + e4),
        # so this walks hop=2.
        edge_db = _edge("e4", "Drug:d", "Drug:b")
        store = FakeGraphStore([SEED_A, SEED_D, NODE_B, NODE_C],
                               GRAPH_EDGES + [edge_db])
        route = asyncio.run(graph_route_cards(store, TENANT, "降",
                                              seed_top_k=5, hop=2))
        assert sum(1 for c in route.cards if c.stable_id == "Drug:b") == 1
        assert route.hop_by_id["Drug:b"] == 1
        assert route.degree_by_id["Drug:b"] == 3

    def test_seed_top_k_and_hop_propagated_to_lookup(self):
        store = _store()
        asyncio.run(graph_route_cards(store, TENANT, "降", seed_top_k=2,
                                      hop=2))
        assert store.lookup_calls == [(TENANT, "降", 2)]
        # hop=2 => two BFS layers: [a, d] then the layer-1 frontier [b, e].
        assert store.neighbors_calls == [["Drug:a", "Drug:d"],
                                         ["Drug:b", "Drug:e"]]

    def test_validation_seed_top_k_and_hop(self):
        store = _store()
        with pytest.raises(ValueError):
            asyncio.run(graph_route_cards(store, TENANT, "降",
                                          seed_top_k=0, hop=1))
        with pytest.raises(ValueError):
            asyncio.run(graph_route_cards(store, TENANT, "降",
                                          seed_top_k=5, hop=0))
        with pytest.raises(ValueError):
            asyncio.run(graph_route_cards(store, TENANT, "降",
                                          seed_top_k=5, hop=4))
        with pytest.raises(TypeError):
            asyncio.run(graph_route_cards(store, TENANT, "降",
                                          seed_top_k="5", hop=1))
        with pytest.raises(TypeError):
            asyncio.run(graph_route_cards(store, TENANT, "降",
                                          seed_top_k=5, hop=True))

    def test_non_string_query_is_type_error(self):
        store = _store()
        with pytest.raises(TypeError):
            asyncio.run(graph_route_cards(store, TENANT, 42))


class TestOrderingKey:
    def test_total_order_for_ids_absent_from_metadata(self):
        known = ordering_key("Drug:a", {"Drug:a": 1}, {"Drug:a": 2})
        unknown = ordering_key("Drug:z", {"Drug:a": 1}, {"Drug:a": 2})
        assert known == (1, -2, "Drug:a")
        assert unknown[0] > known[0]  # absent ids sort after all known ones
        assert unknown == (unknown[0], 0, "Drug:z")

    def test_unknown_ids_among_themselves_break_by_id(self):
        a = ordering_key("Drug:a", {}, {})
        z = ordering_key("Drug:z", {}, {})
        assert a < z

    def test_degree_defaults_to_zero_for_unscored_known_hop(self):
        key = ordering_key("Drug:a", {"Drug:a": 1}, {})
        assert key == (1, 0, "Drug:a")

    def test_default_seed_top_k(self):
        assert DEFAULT_SEED_TOP_K == 5
