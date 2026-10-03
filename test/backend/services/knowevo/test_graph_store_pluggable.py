"""Contract tests for the pluggable GraphStore seam (second backend).

Run the same frozen surface against PgJsonbGraphStore and MemoryGraphStore
so ``KW_GRAPH_STORE_BACKEND`` is a live switch, not a dead declaration.
PG cases that need a real database stay in test_graph_store.py; this file
covers shape + factory + memory semantics that any adapter must honour.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from services.knowevo.graph_store import (
    EdgeCard,
    EntityCard,
    GraphStore,
    HopPlan,
    MemoryGraphStore,
    Path,
    PgJsonbGraphStore,
    Subgraph,
    make_graph_store,
)

FROZEN_METHODS = {
    "upsert_entities",
    "upsert_relations",
    "neighbors",
    "multi_hop",
    "supersede",
    "reachable_decisions",
    "entity_lookup",
    "stats",
}


class TestFactory:
    def test_default_backend_is_pg_jsonb(self):
        store = make_graph_store()
        assert isinstance(store, PgJsonbGraphStore)

    def test_memory_aliases(self):
        for name in ("memory", "mem", "in_memory", "inmemory", "Memory"):
            store = make_graph_store(name)
            assert isinstance(store, MemoryGraphStore)

    def test_pg_aliases(self):
        for name in ("pg_jsonb", "pg", "postgres", "postgresql"):
            store = make_graph_store(name)
            assert isinstance(store, PgJsonbGraphStore)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            make_graph_store("neo4j")

    def test_kw_graph_store_backend_selects_memory(self, monkeypatch):
        monkeypatch.setattr("consts.const.KW_GRAPH_STORE_BACKEND", "memory")
        store = make_graph_store()
        assert isinstance(store, MemoryGraphStore)
        monkeypatch.setattr("consts.const.KW_GRAPH_STORE_BACKEND", "pg_jsonb")
        store2 = make_graph_store()
        assert isinstance(store2, PgJsonbGraphStore)


class TestAdapterSurface:
    @pytest.mark.parametrize("factory", [
        lambda: PgJsonbGraphStore(),
        lambda: MemoryGraphStore(),
    ])
    def test_concrete_adapters_expose_frozen_set(self, factory):
        store = factory()
        assert isinstance(store, GraphStore)
        methods = {m for m in dir(store) if not m.startswith("_") and callable(getattr(store, m))}
        missing = FROZEN_METHODS - methods
        assert not missing

    @pytest.mark.parametrize("factory", [
        lambda: PgJsonbGraphStore(),
        lambda: MemoryGraphStore(),
    ])
    def test_constructor_no_side_effects(self, factory):
        store = factory()
        assert store is not None


class TestMemorySemantics:
    @pytest.fixture
    def store(self):
        return MemoryGraphStore()

    @pytest.fixture
    def t(self):
        return "tenant-a"

    async def test_upsert_entities_merge_props_append_aliases(self, store, t):
        await store.upsert_entities(t, [{
            "stable_id": "D:1", "name": "diabetes",
            "class_ref": "Disease", "props": {"a": 1},
            "aliases": ["dm"],
        }])
        await store.upsert_entities(t, [{
            "stable_id": "D:1", "name": "diabetes",
            "class_ref": "Disease", "props": {"a": 2, "b": 3},
            "aliases": ["dm", "t2dm"],
        }])
        cards = await store.entity_lookup(t, "diabetes", top_k=5)
        assert len(cards) == 1
        assert cards[0].props == {"a": 2, "b": 3}
        assert cards[0].aliases == ["dm", "t2dm"]

    async def test_upsert_relations_same_claim_idempotent(self, store, t):
        rel = {"src": "D:1", "dst": "E:1", "rel_type": "includes",
               "claim": "IFG included", "props": {"evidence_id": "ev1"}}
        await store.upsert_relations(t, [rel, rel])
        stats = await store.stats(t, "graph")
        assert stats["edges_total"] == 1

    async def test_upsert_relations_diff_claim_inserts_new(self, store, t):
        await store.upsert_relations(t, [
            {"src": "D:1", "dst": "E:1", "rel_type": "includes", "claim": "a"},
            {"src": "D:1", "dst": "E:1", "rel_type": "includes", "claim": "b"},
        ])
        stats = await store.stats(t, "graph")
        assert stats["edges_total"] == 2

    async def test_neighbors_current_view_and_rel_filter(self, store, t):
        await store.upsert_entities(t, [
            {"stable_id": "D:1", "name": "d", "class_ref": "Disease"},
            {"stable_id": "E:1", "name": "e", "class_ref": "Exam"},
            {"stable_id": "E:2", "name": "f", "class_ref": "Exam"},
        ])
        await store.upsert_relations(t, [
            {"src": "D:1", "dst": "E:1", "rel_type": "includes", "claim": "c1"},
            {"src": "D:1", "dst": "E:2", "rel_type": "treats", "claim": "c2"},
        ])
        sub = await store.neighbors(t, ["D:1"], rel_types=["includes"])
        assert {e.stable_id for e in sub.entities} == {"D:1", "E:1"}
        assert {r.rel_type for r in sub.edges} == {"includes"}

    async def test_neighbors_as_of_honours_window(self, store, t):
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=30)
        await store.upsert_entities(t, [
            {"stable_id": "D:1", "name": "d", "class_ref": "Disease"},
            {"stable_id": "E:1", "name": "e", "class_ref": "Exam"},
        ])
        await store.upsert_relations(t, [{
            "src": "D:1", "dst": "E:1", "rel_type": "includes",
            "claim": "old", "valid_at": past,
        }])
        # supersede the edge into history
        rels = store._relations[t]
        await store.supersede(t, [rels[0]["id"]], past + timedelta(days=1), "replaced")
        sub_now = await store.neighbors(t, ["D:1"], as_of=None)
        assert sub_now.edges == []
        sub_old = await store.neighbors(t, ["D:1"], as_of=past)
        assert len(sub_old.edges) == 1

    async def test_multi_hop_beam(self, store, t):
        await store.upsert_entities(t, [
            {"stable_id": s, "name": s, "class_ref": "X"}
            for s in ("A", "B", "C", "D")
        ])
        await store.upsert_relations(t, [
            {"src": "A", "dst": "B", "rel_type": "r", "claim": "ab"},
            {"src": "B", "dst": "C", "rel_type": "r", "claim": "bc"},
            {"src": "C", "dst": "D", "rel_type": "r", "claim": "cd"},
        ])
        paths = await store.multi_hop(t, ["A"], HopPlan(), beam=3, depth=3)
        assert paths
        assert paths[0].entities[0] == "A"
        assert max(len(p.entities) for p in paths) >= 3

    async def test_multi_hop_rank_injection(self, store, t):
        await store.upsert_entities(t, [
            {"stable_id": s, "name": s, "class_ref": "X"}
            for s in ("A", "B", "C")
        ])
        await store.upsert_relations(t, [
            {"src": "A", "dst": "B", "rel_type": "r", "claim": "keep"},
            {"src": "A", "dst": "C", "rel_type": "r", "claim": "drop"},
        ])

        def prefer_b(p: Path) -> float:
            return 1.0 if "B" in p.entities else 0.0

        paths = await store.multi_hop(
            t, ["A"], HopPlan(), beam=1, depth=1, rank=prefer_b)
        assert paths[0].entities[-1] == "B"

    async def test_reachable_decisions_via_register(self, store, t):
        d1, d2 = uuid.uuid4(), uuid.uuid4()
        store.register_evidence_refs(t, d1, ["D:1", "E:9"])
        store.register_evidence_refs(t, d2, ["Z:0"])
        got = await store.reachable_decisions(t, ["D:1"])
        assert got == [d1]
        assert await store.reachable_decisions(t, []) == []

    async def test_entity_lookup_name_then_alias(self, store, t):
        await store.upsert_entities(t, [
            {"stable_id": "1", "name": "metformin", "class_ref": "Drug"},
            {"stable_id": "2", "name": "other", "class_ref": "Drug",
             "aliases": ["glucophage"]},
        ])
        by_name = await store.entity_lookup(t, "met", top_k=5)
        assert [c.stable_id for c in by_name] == ["1"]
        by_alias = await store.entity_lookup(t, "gluco", top_k=5)
        assert [c.stable_id for c in by_alias] == ["2"]
        assert await store.entity_lookup(t, "", top_k=5) == []

    async def test_stats_shapes(self, store, t):
        await store.upsert_entities(t, [{"stable_id": "A", "name": "A"}])
        await store.upsert_relations(t, [
            {"src": "A", "dst": "A", "rel_type": "self", "claim": "loop"},
        ])
        g = await store.stats(t, "graph")
        assert g["entities"] == 1
        assert g["edges_total"] == 1
        assert g["edges_valid"] == 1
        f = await store.stats(t, "full")
        assert f["pending"] == 0

    async def test_tenant_isolation(self, store):
        await store.upsert_entities("t1", [{"stable_id": "A", "name": "A"}])
        await store.upsert_entities("t2", [{"stable_id": "B", "name": "B"}])
        s1 = await store.stats("t1", "graph")
        s2 = await store.stats("t2", "graph")
        assert s1["entities"] == 1
        assert s2["entities"] == 1
        assert await store.entity_lookup("t1", "B", top_k=5) == []


class TestResultShapesShared:
    async def test_memory_returns_frozen_card_types(self):
        store = MemoryGraphStore()
        t = "shape"
        await store.upsert_entities(t, [{
            "stable_id": "S1", "name": "n", "class_ref": "C",
            "props": {"k": 1}, "aliases": ["a"],
        }])
        await store.upsert_relations(t, [{
            "src": "S1", "dst": "S1", "rel_type": "r", "claim": "c",
            "props": {"evidence_id": "e"}, "contested": True,
        }])
        sub = await store.neighbors(t, ["S1"])
        assert isinstance(sub, Subgraph)
        assert all(isinstance(e, EntityCard) for e in sub.entities)
        assert all(isinstance(r, EdgeCard) for r in sub.edges)
        paths = await store.multi_hop(t, ["S1"], HopPlan(), beam=1, depth=1)
        assert all(isinstance(p, Path) for p in paths)
