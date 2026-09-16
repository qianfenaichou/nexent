"""
Unit and integration tests for services/knowevo/graph_store.py (T-07a).

Layer 1 (always runs): contract-shape tests that need no database - the
GraphStore ABC exposes the frozen method set, the dataclass vocabulary
(Subgraph/Path/HopPlan/EntityCard/EdgeCard) has the frozen fields, and the
SQLAlchemy current-view predicate (valid_now) compiles to the expected SQL
shape. These lock the seam contract itself.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): real-Postgres run of the adapter -
current vs historical view after supersede, idempotent upserts, tenant
isolation, multi-hop beam walk, reachable_decisions via evidence refs, and
stats. Same gate pattern as test_ontology_service.py (pitfalls #14).
"""
import os
import sys
import uuid as uuid_mod
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.graph_store import (
    EdgeCard,
    EntityCard,
    GraphStore,
    HopPlan,
    Path,
    PgJsonbGraphStore,
    Subgraph,
    _entity_to_card,
    _relation_to_card,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# Layer 1: contract shape (no database)
# ---------------------------------------------------------------------------

class TestContractShape:
    def test_abc_exposes_frozen_method_set(self):
        methods = {m for m in dir(GraphStore) if not m.startswith("_")}
        expected = {
            "upsert_entities", "upsert_relations", "neighbors", "multi_hop",
            "supersede", "reachable_decisions", "entity_lookup", "stats",
        }
        assert expected <= methods

    def test_pg_store_is_concrete(self):
        # PgJsonbGraphStore implements every abstract method (no missing
        # implementation left over from the ABC).
        store = PgJsonbGraphStore()
        for m in ("upsert_entities", "upsert_relations", "neighbors",
                  "multi_hop", "supersede", "reachable_decisions",
                  "entity_lookup", "stats"):
            assert callable(getattr(store, m, None))

    def test_result_shapes_frozen(self):
        card = EntityCard(stable_id="Drug:x", name="x", class_ref="Drug")
        assert card.props == {}
        edge = EdgeCard(id="e", src="a", dst="b", rel_type="r", claim="c")
        assert edge.contested is False
        sub = Subgraph(entities=[card], edges=[edge])
        assert sub.entities[0].name == "x"
        plan = HopPlan(rel_types=["indicated_for"], max_depth=2)
        assert plan.reverse is False
        p = Path(entities=["a"], edges=["e1"], claims=["c1"])
        assert p.claims == ["c1"]

    def test_card_mappers_from_rows(self):
        row_e = SimpleNamespace(
            stable_id="Drug:x", name="x", class_ref="Drug",
            props={"atc": "A10BA02"},
            aliases=[{"alias": "格华止", "type": "brand"}])
        card = _entity_to_card(row_e)
        assert card.stable_id == "Drug:x"
        assert card.aliases == ["格华止"]

        row_r = SimpleNamespace(
            id="e1", src="a", dst="b", rel_type="r", claim="c",
            props={"evidence_id": "ev1"}, contested=True)
        edge = _relation_to_card(row_r)
        assert edge.contested is True
        assert edge.props["evidence_id"] == "ev1"

    def test_valid_now_predicate_compiles(self):
        """The current-view predicate must compile to a valid SQLAlchemy
        expression without touching a database (shape lock)."""
        from database.knowevo_db import KgRelation, valid_now
        expr = valid_now(KgRelation)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "valid_at" in compiled
        assert "invalid_at" in compiled
        assert "IS NULL" in compiled

    def test_subgraph_neighbors_empty_seed(self):
        store = PgJsonbGraphStore()
        import asyncio
        sub = asyncio.run(store.neighbors(TENANT_A, []))
        assert sub.entities == [] and sub.edges == []


# ---------------------------------------------------------------------------
# Layer 2: real Postgres (RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

pg_gate = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests")


@pg_gate
@pytest.mark.asyncio
class TestPgGraphStore:
    async def test_upsert_idempotent_and_neighbors_current_view(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await store.upsert_entities(tenant, [
            {"stable_id": "Drug:a", "name": "二甲双胍", "class_ref": "Drug"},
            {"stable_id": "Disease:b", "name": "2型糖尿病",
             "class_ref": "Disease"},
        ])
        # idempotent rerun: same stable_id, props merged not duplicated
        await store.upsert_entities(tenant, [
            {"stable_id": "Drug:a", "name": "二甲双胍",
             "class_ref": "Drug", "props": {"atc": "A10BA02"}},
        ])
        await store.upsert_relations(tenant, [
            {"src": "Drug:a", "dst": "Disease:b",
             "rel_type": "indicated_for", "claim": "一线用药"},
        ])
        sub = await store.neighbors(tenant, ["Drug:a"], hop=1)
        assert {e.name for e in sub.entities} == {"二甲双胍", "2型糖尿病"}
        assert len(sub.edges) == 1
        stats = await store.stats(tenant, "graph")
        assert stats["entities"] == 2
        assert stats["edges_total"] == 1
        assert stats["edges_valid"] == 1

    async def test_supersede_moves_edge_to_history(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await store.upsert_entities(tenant, [
            {"stable_id": "Drug:a", "name": "a", "class_ref": "Drug"},
            {"stable_id": "Disease:b", "name": "b", "class_ref": "Disease"},
        ])
        await store.upsert_relations(tenant, [
            {"src": "Drug:a", "dst": "Disease:b",
             "rel_type": "indicated_for", "claim": "旧主张"},
        ])
        cur = await store.neighbors(tenant, ["Drug:a"], hop=1)
        edge_id = cur.edges[0].id
        await store.supersede(tenant, [edge_id], datetime.now(UTC),
                              "guideline v2024 supersedes")
        cur = await store.neighbors(tenant, ["Drug:a"], hop=1)
        assert cur.edges == []              # gone from current view
        hist = await store.neighbors(tenant, ["Drug:a"], hop=1,
                                     valid_view=False)
        assert len(hist.edges) == 1          # still in history
        assert hist.edges[0].props["supersede_reason"].startswith("guideline")

    async def test_tenant_isolation(self):
        store = PgJsonbGraphStore()
        for tenant in (TENANT_A, TENANT_B):
            await store.upsert_entities(tenant, [
                {"stable_id": "Drug:a", "name": "共享名",
                 "class_ref": "Drug"}])
        sub_a = await store.neighbors(TENANT_A, ["Drug:a"], hop=1)
        sub_b = await store.neighbors(TENANT_B, ["Drug:a"], hop=1)
        # each tenant sees exactly its own row
        assert len(sub_a.entities) == 1 and len(sub_b.entities) == 1
        stats_a = await store.stats(TENANT_A, "graph")
        stats_b = await store.stats(TENANT_B, "graph")
        assert stats_a["entities"] == 1 and stats_b["entities"] == 1

    async def test_multi_hop_beam_walk(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        ids = [f"e{i}" for i in range(4)]
        await store.upsert_entities(tenant, [
            {"stable_id": ids[0], "name": "s", "class_ref": "Drug"},
            {"stable_id": ids[1], "name": "n1", "class_ref": "Drug"},
            {"stable_id": ids[2], "name": "n2", "class_ref": "Disease"},
            {"stable_id": ids[3], "name": "n3", "class_ref": "Disease"},
        ])
        await store.upsert_relations(tenant, [
            {"src": ids[0], "dst": ids[1], "rel_type": "x", "claim": "c1"},
            {"src": ids[1], "dst": ids[2], "rel_type": "y", "claim": "c2"},
            {"src": ids[2], "dst": ids[3], "rel_type": "z", "claim": "c3"},
        ])
        paths = await store.multi_hop(
            tenant, [ids[0]], HopPlan(), beam=3, depth=3)
        assert paths, "expected at least one walk"
        longest = max(len(p.entities) for p in paths)
        assert longest >= 2
        # claims ride along for evidence-chain assembly
        assert any(len(p.claims) == len(p.entities) - 1 for p in paths)

    async def test_reachable_decisions_via_evidence_refs(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        from database.knowevo_db import KgEvidence, create_row
        ev = create_row(KgEvidence, tenant_id=tenant,
                        doc_id=uuid_mod.uuid4(), span_loc={},
                        span_text="x", entity_refs=["Drug:a"],
                        edge_ids=[], tag="EXTRACTED")
        hits = await store.reachable_decisions(tenant, ["Drug:a"])
        assert hits == [ev["id"]]
        misses = await store.reachable_decisions(tenant, ["Drug:zzz"])
        assert misses == []

    async def test_entity_lookup_alias_fallback(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await store.upsert_entities(tenant, [
            {"stable_id": "Drug:met", "name": "二甲双胍",
             "class_ref": "Drug",
             "aliases": [{"alias": "格华止", "type": "brand"}]},
        ])
        by_name = await store.entity_lookup(tenant, "二甲双胍")
        assert by_name[0].stable_id == "Drug:met"
        by_alias = await store.entity_lookup(tenant, "格华止")
        assert by_alias[0].stable_id == "Drug:met"
