"""
Unit and integration tests for services/knowevo/version_pin.py (T-09, B2).

This is the single most important test file of the version-pinning claim:
the definition from 02-tech-plan 3.2 is transcribed in version_pin.py, and
these tests lock it down as a pure function (no database, no LLM) so a
refactor cannot quietly weaken it.

Layer 1 (always runs): resolve_version_clock resolution order, the
edge_in_version predicate at every boundary (before/inside/after window,
open window, closed window, exact-boundary inclusivity), path_version_valid
over mixed paths, filter_paths_by_version, naive/aware normalization.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): the same semantics through the real
Postgres adapter - an edge stamped invalid before t_v must be excluded from
a pinned neighborhood while remaining visible in the historical view, and a
pinned multi_hop walk must not traverse it. This is the test that catches
"the predicate compiles but the walk ignores it" (pitfall #25 pattern).
"""
import os
import sys
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.graph_store import EdgeCard, HopPlan, Path, PgJsonbGraphStore
from services.knowevo.version_pin import (
    VersionClock,
    edge_in_version,
    filter_paths_by_version,
    path_version_valid,
    pin_predicate,
    resolve_version_clock,
)

T_V = datetime(2025, 1, 1, tzinfo=UTC)
BEFORE = datetime(2024, 1, 1, tzinfo=UTC)
AFTER = datetime(2026, 1, 1, tzinfo=UTC)
CLOCK = VersionClock("v1.3.0", T_V, source="explicit")


def edge(valid_at=None, invalid_at=None, rel_type="indicated_for",
         contested=False):
    return EdgeCard(id="e1", src="a", dst="b", rel_type=rel_type,
                    claim="c", valid_at=valid_at, invalid_at=invalid_at,
                    contested=contested)


# ---------------------------------------------------------------------------
# Layer 1: predicate semantics (pure, no database)
# ---------------------------------------------------------------------------

class TestEdgeInVersion:
    def test_edge_that_starts_after_cutoff_is_out(self):
        # The whole point of B2: a fact that only became true after the
        # version's cutoff was not part of that version.
        assert edge_in_version(AFTER, None, CLOCK) is False

    def test_edge_that_started_before_cutoff_is_in(self):
        assert edge_in_version(BEFORE, None, CLOCK) is True

    def test_edge_invalidated_before_cutoff_is_out(self):
        assert edge_in_version(BEFORE, BEFORE + timedelta(days=30),
                               CLOCK) is False

    def test_edge_invalidated_after_cutoff_is_in(self):
        assert edge_in_version(BEFORE, AFTER, CLOCK) is True

    def test_open_ended_window_is_in(self):
        assert edge_in_version(BEFORE, None, CLOCK) is True

    def test_boundary_is_inclusive_at_valid_at(self):
        # valid_at <= t_v, so valid_at exactly at the cutoff counts as in.
        assert edge_in_version(T_V, None, CLOCK) is True

    def test_boundary_is_exclusive_at_invalid_at(self):
        # invalid_at > t_v, so an edge expiring exactly at the cutoff is
        # already out - otherwise a supersede stamped at the version
        # boundary would leak into the new version's subgraph.
        assert edge_in_version(BEFORE, T_V, CLOCK) is False

    def test_missing_valid_at_means_always_existed(self):
        # The column is NOT NULL server-side; None only appears in
        # hand-built fixtures, where "unknown start" must not mean "out".
        assert edge_in_version(None, None, CLOCK) is True

    def test_naive_datetimes_are_normalized(self):
        # Mixing naive and aware raises TypeError at comparison time; the
        # normalization in _ensure_aware is what keeps this total.
        # Naive on purpose: this is the case under test.
        naive_clock = datetime(2025, 1, 1)  # noqa: DTZ001
        assert edge_in_version(datetime(2024, 1, 1),  # noqa: DTZ001
                               None, naive_clock) is True
        assert edge_in_version(datetime(2026, 1, 1),  # noqa: DTZ001
                               None, naive_clock) is False

    def test_accepts_raw_datetime_instead_of_clock(self):
        assert edge_in_version(BEFORE, None, T_V) is True
        assert edge_in_version(AFTER, None, T_V) is False


class TestResolveVersionClock:
    def test_explicit_as_of_wins(self):
        clock = resolve_version_clock("v9", as_of=T_V)
        assert clock.as_of == T_V and clock.source == "explicit"

    def test_version_row_created_at_is_used(self):
        clock = resolve_version_clock(
            "v1.3.0", versions=[{"version": "v1.3.0", "created_at": T_V}])
        assert clock.as_of == T_V and clock.source == "version_created_at"

    def test_falls_back_to_now_and_admits_it(self):
        clock = resolve_version_clock("v1.3.0")
        assert clock.source == "now"
        assert clock.as_of.tzinfo is not None

    def test_unknown_version_label_falls_back(self):
        clock = resolve_version_clock(
            "v404", versions=[{"version": "v1.3.0", "created_at": T_V}])
        assert clock.source == "now"

    def test_row_without_created_at_falls_back(self):
        clock = resolve_version_clock(
            "v1.3.0", versions=[{"version": "v1.3.0"}])
        assert clock.source == "now"

    def test_none_version_without_as_of_is_now(self):
        clock = resolve_version_clock(None)
        assert clock.ontology_version is None and clock.source == "now"


class TestPathVersionValid:
    def test_all_edges_in_version_passes(self):
        assert path_version_valid([edge(BEFORE), edge(BEFORE)], CLOCK) is True

    def test_one_expired_edge_fails_the_whole_path(self):
        # A path is valid only when every hop is; a single violated edge is
        # enough to make the route unusable under that version.
        assert path_version_valid(
            [edge(BEFORE), edge(AFTER)], CLOCK) is False

    def test_empty_path_is_vacuously_valid(self):
        assert path_version_valid([], CLOCK) is True

    def test_accepts_plain_dicts(self):
        # The ablation harness replays paths out of the eval set as dicts.
        assert path_version_valid(
            [{"valid_at": BEFORE, "invalid_at": None}], CLOCK) is True
        assert path_version_valid(
            [{"valid_at": AFTER, "invalid_at": None}], CLOCK) is False

    def test_accepts_objects_with_time_attributes(self):
        class Row:
            valid_at = BEFORE
            invalid_at = None
        assert path_version_valid([Row()], CLOCK) is True


class TestFilterPathsByVersion:
    def test_keeps_only_pinned_paths(self):
        good = Path(entities=["a", "b"], claims=["c1"])
        bad = Path(entities=["a", "c"], claims=["c2"])
        edges = {
            id(good): [edge(BEFORE)],
            id(bad): [edge(AFTER)],
        }
        kept = filter_paths_by_version([good, bad], edges, CLOCK)
        assert kept == [good]

    def test_path_without_edge_mapping_is_dropped(self):
        # No edges means no evidence of validity - dropping is the safe side
        # (never claim pinned provenance for a path we cannot check).
        orphan = Path(entities=["a", "b"], claims=["c"])
        assert filter_paths_by_version([orphan], {}, CLOCK) == []


class TestPinPredicate:
    def test_compiles_to_valid_at_invalid_at_sql(self):
        from database.knowevo_db import KgRelation
        expr = pin_predicate(KgRelation, CLOCK)
        compiled = str(expr.compile(compile_kwargs={"literal_binds": True}))
        assert "valid_at" in compiled
        assert "invalid_at" in compiled
        assert "2025-01-01" in compiled

    def test_accepts_raw_datetime(self):
        from database.knowevo_db import KgRelation
        compiled = str(pin_predicate(KgRelation, T_V).compile(
            compile_kwargs={"literal_binds": True}))
        assert "2025-01-01" in compiled


# ---------------------------------------------------------------------------
# Layer 2: real Postgres (RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

pg_gate = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests")


@pg_gate
@pytest.mark.asyncio
class TestPinnedWalkInPostgres:
    """The ablation pair: the same graph, the same question, pinned and not.

    This is the reproducible evidence behind the B2 claim, run against the
    real adapter rather than a fake - a store that ignores ``as_of`` would
    pass every Layer 1 test and fail here (pitfall #25 pattern).
    """

    async def _seed_two_drugs_one_expired_edge(self, store, tenant):
        """Drug:a -(indicated_for, exact)-> Disease:b, superseded at T_V.

        The edge is written with an explicit window because the store's
        upsert deliberately never touches bi-temporal columns (that is the
        merge layer's job); version pinning is a *query* concern.
        """
        await store.upsert_entities(tenant, [
            {"stable_id": "Drug:a", "name": "旧药", "class_ref": "Drug"},
            {"stable_id": "Disease:b", "name": "2型糖尿病",
             "class_ref": "Disease"},
            {"stable_id": "Drug:c", "name": "新药", "class_ref": "Drug"},
        ])
        await store.upsert_relations(tenant, [
            {"src": "Drug:a", "dst": "Disease:b", "rel_type": "indicated_for",
             "claim": "旧版指南推荐：一线用药"},
            {"src": "Drug:c", "dst": "Disease:b", "rel_type": "indicated_for",
             "claim": "新版指南推荐：一线用药"},
        ])
        sub = await store.neighbors(tenant, ["Drug:a", "Drug:c"], hop=1)
        by_src = {e.src: e for e in sub.edges}
        old_edge = by_src["Drug:a"]
        new_edge = by_src["Drug:c"]
        # Old edge became valid before the cutoff and stopped being valid at
        # it; new edge only became valid after it.
        await self._set_window(tenant, old_edge.id,
                               BEFORE, T_V)
        await self._set_window(tenant, new_edge.id,
                               AFTER, None)
        return old_edge, new_edge

    @staticmethod
    async def _set_window(tenant, edge_id, valid_at, invalid_at):
        from database.knowevo_db import KgRelation, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgRelation).filter(
                KgRelation.tenant_id == tenant,
                KgRelation.id == edge_id).first()
            row.valid_at = valid_at
            row.invalid_at = invalid_at
            if invalid_at is not None:
                row.superseded_at = invalid_at
                props = dict(row.props or {})
                props["supersede_reason"] = "test fixture window"
                row.props = props
            session.flush()

    async def test_pinned_neighborhood_excludes_expired_and_future_edges(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await self._seed_two_drugs_one_expired_edge(store, tenant)

        unpinned = await store.neighbors(tenant, ["Drug:a", "Drug:c"], hop=1)
        assert len(unpinned.edges) == 1, (
            "current view should show only the edge valid now")

        pinned = await store.neighbors(tenant, ["Drug:a", "Drug:c"], hop=1,
                                       as_of=T_V)
        # At T_V the old edge is already invalid (invalid_at == t_v is out)
        # and the new edge is not yet valid, so the pinned subgraph is empty.
        assert pinned.edges == [], (
            "pinned view at t_v must contain neither the superseded edge "
            "nor the one that only became valid later")

    async def test_pinned_neighborhood_keeps_edge_valid_before_cutoff(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await self._seed_two_drugs_one_expired_edge(store, tenant)

        earlier = BEFORE + timedelta(days=1)
        pinned = await store.neighbors(tenant, ["Drug:a", "Drug:c"], hop=1,
                                      as_of=earlier)
        assert len(pinned.edges) == 1
        assert pinned.edges[0].src == "Drug:a"
        assert pinned.edges[0].claim.startswith("旧版指南")

    async def test_edge_cards_carry_the_time_window(self):
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await self._seed_two_drugs_one_expired_edge(store, tenant)
        sub = await store.neighbors(tenant, ["Drug:a"], hop=1,
                                   as_of=BEFORE + timedelta(days=1))
        card = sub.edges[0]
        assert card.valid_at is not None and card.invalid_at is not None, (
            "version pinning needs the window on the card, otherwise the "
            "pure predicate has nothing to test")

    async def test_pinned_multi_hop_does_not_traverse_expired_edge(self):
        """The store-level walk honours as_of end to end."""
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await self._seed_two_drugs_one_expired_edge(store, tenant)

        pinned = await store.multi_hop(tenant, ["Drug:a"], HopPlan(),
                                       beam=3, depth=2, as_of=T_V)
        # Nothing was valid at T_V from Drug:a, so no expansion happened.
        assert all(len(p.entities) == 1 for p in pinned)

        earlier = await store.multi_hop(tenant, ["Drug:a"], HopPlan(),
                                        beam=3, depth=2,
                                        as_of=BEFORE + timedelta(days=1))
        assert any(len(p.entities) > 1 for p in earlier)