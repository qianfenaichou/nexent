"""Tests for the L8 human-review budget-quality curve kernel (budget_curve).

Spec anchors: ``competition/docs/tech-optimization-2026-09-28/
l8-budget-quality-protocol-2026-09-29.md`` and
``competition/docs/tech-optimization-2026-09-28/KnowEvo提分总纲.md`` §L8.
Reuse seam: ``update_planner.plan_minimal_update`` exposes ``per_item_voi``
(key order = greedy order) which is the primary ordering input here.

Seams under test (public interface only):

1. ``budget_curve`` - per_item_voi + cost table + budget sequence -> curve
   points; quality is an injectable callable over the confirmed-id prefix.
2. ``order_by_score`` - deterministic ranking, ties broken by id ascending
   (same total order as update_planner).
3. ``order_randomly`` - seeded, deterministic permutation of the id set.
4. ``gate_filter`` - evidence-gate filter; dropped ids never enter review.
5. ``k0_quality_fn`` - Quality builder that defaults to the production
   ``ontology_service._k0_metrics_from_snapshot`` (lazy; injectable for
   offline tests).
6. ``CurvePoint`` field contract.

Expected literals are hand-computed from the worked examples below (unit
costs, integer budgets) or recorded from an actual run first and pinned
afterwards (never back-fill expected values from mental
simulation).
"""
import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.budget_curve import (
    CurvePoint,
    budget_curve,
    gate_filter,
    k0_quality_fn,
    order_by_score,
    order_randomly,
)


def q_len(ids):
    """Trivial quality: cov = share of confirmed over a fixed pool of 3."""
    return {"cov": len(ids) / 3.0, "n": len(ids)}


# ---------------------------------------------------------------------------
# 1. budget_curve: prefix admission under a budget (hand-worked example)
# ---------------------------------------------------------------------------


class TestBudgetCurvePrefixAdmission:
    def test_unit_costs_hand_example(self):
        # order a,b,c (per_item_voi key order); cost 2.0 each.
        # budget 0 -> () ; 2 -> (a) ; 3 -> (a) [b overflows, stop] ;
        # 4 -> (a,b) ; 6 -> (a,b,c)
        voi = {"a": 5.0, "b": 3.0, "c": 1.0}
        costs = {"a": 2.0, "b": 2.0, "c": 2.0}
        budgets = [0.0, 2.0, 3.0, 4.0, 6.0]
        curve = budget_curve(voi, costs, budgets, q_len)
        assert [p.confirmed_ids for p in curve] == [
            (),
            ("a",),
            ("a",),
            ("a", "b"),
            ("a", "b", "c"),
        ]
        assert [p.n_confirmed for p in curve] == [0, 1, 1, 2, 3]
        assert [p.cost_spent for p in curve] == [0.0, 2.0, 2.0, 4.0, 6.0]
        assert [p.budget for p in curve] == budgets
        assert [p.quality["cov"] for p in curve] == [
            0.0, 1 / 3.0, 1 / 3.0, 2 / 3.0, 1.0,
        ]
        assert [p.quality["n"] for p in curve] == [0, 1, 1, 2, 3]

    def test_unequal_costs_stop_at_first_overflow_no_resume(self):
        # costs a=3, b=3, c=1. budget 4: a fits (3<=4), b would make 6>4 ->
        # stop; the cheaper c is NOT taken (prefix semantics, matches the
        # L7 planner "stop at first deficit, never resume" rule).
        voi = {"a": 5.0, "b": 3.0, "c": 1.0}
        costs = {"a": 3.0, "b": 3.0, "c": 1.0}
        curve = budget_curve(voi, costs, [4.0], q_len)
        assert curve[0].confirmed_ids == ("a",)
        assert curve[0].cost_spent == 3.0

    def test_budget_covers_everything(self):
        voi = {"a": 1.0, "b": 1.0}
        costs = {"a": 1.0, "b": 1.0}
        curve = budget_curve(voi, costs, [10.0], q_len)
        assert curve[0].confirmed_ids == ("a", "b")
        assert curve[0].cost_spent == 2.0

    def test_empty_pool(self):
        curve = budget_curve({}, {}, [1.0, 2.0], lambda ids: {"cov": 0.0})
        assert len(curve) == 2
        assert all(p.confirmed_ids == () and p.n_confirmed == 0 for p in curve)
        assert all(p.cost_spent == 0.0 for p in curve)
        assert all(p.quality == {"cov": 0.0} for p in curve)

    def test_quality_fn_receives_prefix_in_review_order(self):
        seen = []

        def spy(ids):
            seen.append(tuple(ids))
            return {"cov": 0.0}

        voi = {"a": 3.0, "b": 2.0, "c": 1.0}
        costs = {"a": 1.0, "b": 1.0, "c": 1.0}
        budget_curve(voi, costs, [1.0, 3.0], spy)
        assert seen == [("a",), ("a", "b", "c")]

    def test_quality_dict_is_copied_not_aliased(self):
        shared = {"cov": 0.5}

        def q(_ids):
            return shared

        voi = {"a": 1.0}
        costs = {"a": 1.0}
        curve = budget_curve(voi, costs, [0.0, 1.0], q)
        assert curve[0].quality == {"cov": 0.5}
        assert curve[1].quality == {"cov": 0.5}
        assert curve[0].quality is not curve[1].quality
        assert curve[0].quality is not shared


# ---------------------------------------------------------------------------
# 2. Explicit review order (confidence / random arms)
# ---------------------------------------------------------------------------


class TestExplicitOrder:
    def test_order_overrides_voi_key_order(self):
        voi = {"a": 5.0, "b": 3.0, "c": 1.0}
        costs = {"a": 1.0, "b": 1.0, "c": 1.0}
        curve = budget_curve(voi, costs, [1.0, 2.0], q_len, order=("c", "b", "a"))
        assert curve[0].confirmed_ids == ("c",)
        assert curve[1].confirmed_ids == ("c", "b")

    def test_order_with_repeated_id_rejected(self):
        voi = {"a": 1.0, "b": 1.0}
        costs = {"a": 1.0, "b": 1.0}
        with pytest.raises(ValueError):
            budget_curve(voi, costs, [1.0], q_len, order=("a", "a", "b"))


# ---------------------------------------------------------------------------
# 3. order_by_score / order_randomly / gate_filter
# ---------------------------------------------------------------------------


class TestOrderings:
    def test_order_by_score_descending_tie_break_id_ascending(self):
        # b and c tie at 2.0 -> id ascending puts b before c
        scores = {"a": 1.0, "b": 2.0, "c": 2.0}
        assert order_by_score(scores) == ("b", "c", "a")
        assert order_by_score(scores, descending=False) == ("a", "b", "c")

    def test_order_by_score_empty(self):
        assert order_by_score({}) == ()

    def test_order_randomly_is_seeded_permutation(self):
        ids = ["a", "b", "c", "d", "e"]
        one = order_randomly(ids, seed=7)
        two = order_randomly(ids, seed=7)
        other = order_randomly(ids, seed=8)
        assert one == two
        assert sorted(one) == sorted(ids)
        assert set(one) == set(ids)
        # a different seed is allowed to coincide on n=5 but must not be
        # required to differ; the contract is determinism + permutation.
        assert sorted(other) == sorted(ids)

    def test_order_randomly_accepts_any_iterable(self):
        assert sorted(order_randomly((x for x in ["x", "y"]), seed=1)) == ["x", "y"]

    def test_gate_filter_preserves_order_and_drops_failing(self):
        ids = ["a", "b", "c", "d"]
        kept = gate_filter(ids, keep=lambda i: i in {"b", "d"})
        assert kept == ("b", "d")

    def test_gate_filter_empty_when_all_fail(self):
        assert gate_filter(["a", "b"], keep=lambda i: False) == ()


# ---------------------------------------------------------------------------
# 4. k0_quality_fn: injectable metrics, production default is documented
# ---------------------------------------------------------------------------


class TestK0QualityFn:
    def test_appends_confirmed_item_classes_to_base(self):
        base = [{"name": "Root"}]
        item_classes = {
            "a": [{"name": "Child", "parent": "Root"}],
            "b": [{"name": "Leaf", "parent": "Child", "anchor": "1.1"}],
        }
        calls = []

        def metrics_fn(snapshot, seed_terms=None):
            calls.append((sorted(c["name"] for c in snapshot["classes"]), seed_terms))
            return {"cov": len(snapshot["classes"]) / 3.0}

        q = k0_quality_fn(base, item_classes, seed_terms=["Root"], metrics_fn=metrics_fn)
        assert q(()) == {"cov": 1 / 3.0}
        assert q(("a",)) == {"cov": 2 / 3.0}
        assert q(("a", "b")) == {"cov": 1.0}
        assert calls == [
            (["Root"], ["Root"]),
            (["Child", "Root"], ["Root"]),
            (["Child", "Leaf", "Root"], ["Root"]),
        ]

    def test_item_without_class_entry_adds_nothing(self):
        base = [{"name": "Root"}]
        q = k0_quality_fn(
            base, {"a": [{"name": "Child"}]}, metrics_fn=lambda s, seed_terms=None: {
                "n": len(s["classes"])
            }
        )
        assert q(("a", "missing")) == {"n": 2}

    def test_default_metrics_fn_is_k0_production(self):
        # literals run-verified against ontology_service._k0_metrics_from_snapshot
        # (cov = share of CLASSES covered by seed terms, not the reverse)
        base = [{"name": "Root"}]
        item_classes = {"a": [{"name": "Child", "parent": "Root", "anchor": "1.1"}]}
        q = k0_quality_fn(base, item_classes, seed_terms=["Child"])
        m0 = q(())
        m1 = q(("a",))
        assert set(m0) == {"cov", "red", "dep", "align"}
        assert m0 == {"cov": 0.0, "red": 0.0, "dep": 1, "align": 0.0}
        assert m1 == {"cov": 0.5, "red": 0.0, "dep": 2, "align": 0.5}
        q2 = k0_quality_fn(base, item_classes, seed_terms=["Root", "Child"])
        assert q2(("a",)) == {"cov": 1.0, "red": 0.0, "dep": 2, "align": 0.5}

    def test_default_metrics_fn_sees_redundant_parent(self):
        # run-verified: also_parent adds a redundant edge (red = 1/3)
        base = [{"name": "Root"}]
        item_classes = {
            "a": [{"name": "Child", "parent": "Root", "anchor": "1.1"}],
            "b": [{"name": "Twin", "parent": "Root", "also_parent": "Child"}],
        }
        q = k0_quality_fn(base, item_classes, seed_terms=["Root", "Child", "Twin"])
        assert q(("a", "b")) == {"cov": 1.0, "red": 0.3333, "dep": 2, "align": 0.3333}

    def test_base_classes_not_mutated(self):
        base = [{"name": "Root"}]
        item_classes = {"a": [{"name": "Child"}]}
        q = k0_quality_fn(
            base, item_classes, metrics_fn=lambda s, seed_terms=None: {
                "n": len(s["classes"])
            }
        )
        q(("a",))
        assert base == [{"name": "Root"}]


# ---------------------------------------------------------------------------
# 5. Validation (fail-fast)
# ---------------------------------------------------------------------------


class TestValidation:
    def test_cost_not_positive_rejected(self):
        with pytest.raises(ValueError):
            budget_curve({"a": 1.0}, {"a": 0.0}, [1.0], q_len)
        with pytest.raises(ValueError):
            budget_curve({"a": 1.0}, {"a": -1.0}, [1.0], q_len)

    def test_cost_nan_rejected(self):
        with pytest.raises(ValueError):
            budget_curve({"a": 1.0}, {"a": float("nan")}, [1.0], q_len)

    def test_budget_negative_rejected(self):
        with pytest.raises(ValueError):
            budget_curve({"a": 1.0}, {"a": 1.0}, [-1.0], q_len)

    def test_budget_nan_rejected(self):
        with pytest.raises(ValueError):
            budget_curve({"a": 1.0}, {"a": 1.0}, [float("nan")], q_len)

    def test_id_set_mismatch_rejected(self):
        with pytest.raises(ValueError):
            budget_curve({"a": 1.0}, {"a": 1.0, "b": 1.0}, [1.0], q_len)
        with pytest.raises(ValueError):
            budget_curve({"a": 1.0, "b": 1.0}, {"a": 1.0}, [1.0], q_len)

    def test_order_not_permutation_rejected(self):
        voi = {"a": 1.0, "b": 1.0}
        costs = {"a": 1.0, "b": 1.0}
        with pytest.raises(ValueError):
            budget_curve(voi, costs, [1.0], q_len, order=("a",))
        with pytest.raises(ValueError):
            budget_curve(voi, costs, [1.0], q_len, order=("a", "b", "c"))

    def test_quality_fn_not_callable_rejected(self):
        with pytest.raises(TypeError):
            budget_curve({"a": 1.0}, {"a": 1.0}, [1.0], None)

    def test_curve_point_is_frozen_dataclass(self):
        voi = {"a": 1.0}
        costs = {"a": 1.0}
        p = budget_curve(voi, costs, [1.0], q_len)[0]
        assert isinstance(p, CurvePoint)
        with pytest.raises(Exception):
            p.budget = 99.0
