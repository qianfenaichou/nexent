"""Tests for the L7 minimal-sufficient-update planner (update_planner).

Spec anchors: workspace archive doc
``archive/旧计划书/05-项目综合评估与优化路线-独立评审.md`` §3.3 and
``competition/docs/tech-optimization-2026-09-28/KnowEvo提分总纲.md`` §L7.

Semantics pinned here (every asserted float literal was verified by actually
running the implementation first - : never back-fill expected
values from mental simulation):

- VOI_i = p_change_i * impact_i; greedy walk in VOI-descending order, ties
  broken by (kind, id) lexicographic ascending;
- selection condition ``voi >= cost`` (equality selects); the walk STOPS at
  the first marginal deficit and never resumes (spec-literal "stop
  immediately"): the pending item and all later items are skipped;
- epsilon rule is evaluated BEFORE examining the pending candidate: if the
  residual expected loss of leaving the pending candidate and everything
  after it unselected already satisfies ``residual <= epsilon``, stop with
  "epsilon_satisfied"; at one boundary where both rules would fire, the
  epsilon rule wins;
- determinism: same input -> equal plan; ``per_item_voi`` key order follows
  the greedy order; ``skipped`` follows the same deterministic order;
- degenerate semantics: total VOI == 0 -> quality_retention_rate = 1.0;
  empty input -> stop_reason "exhausted", residual 0.0;
- p_change outside [0, 1], negative impact, non-positive cost, unknown kind,
  duplicate ids and non-positive total_rebuild_cost all raise ValueError.
"""
import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.update_planner import UpdateCandidate, plan_minimal_update


def uc(cid, kind="proposal", p=0.5, impact=10, cost=1.0):
    """Shorthand builder keeping test tables readable."""
    return UpdateCandidate(id=cid, kind=kind, p_change=p, impact=impact, cost=cost)


# ---------------------------------------------------------------------------
# 1. Greedy order and the voi >= cost selection condition (hand-computed)
# ---------------------------------------------------------------------------


class TestGreedyOrderAndSelectionCondition:
    def test_three_item_hand_example(self):
        # voi: a=0.5*10=5.0, b=0.25*12=3.0, c=0.5*2=1.0; costs 3.0/2.0/10.0
        a = uc("a", "proposal", p=0.5, impact=10, cost=3.0)
        b = uc("b", "decision_card", p=0.25, impact=12, cost=2.0)
        c = uc("c", "proposal", p=0.5, impact=2, cost=10.0)
        plan = plan_minimal_update([b, c, a], epsilon=0.0)
        # epsilon=0.0 isolates the marginal-benefit rule (residual > 0 > epsilon
        # until the list is consumed)
        assert [x.id for x in plan.selected] == ["a", "b"]  # 5.0 then 3.0
        assert [x.id for x in plan.skipped] == ["c"]  # 1.0 < 10.0 -> stop
        assert plan.stop_reason == "marginal_benefit_below_cost"
        assert plan.per_item_voi == {"a": 5.0, "b": 3.0, "c": 1.0}
        assert list(plan.per_item_voi) == ["a", "b", "c"]  # greedy order
        assert plan.residual_expected_loss == 1.0
        assert plan.cost_selected == 5.0
        assert plan.cost_total == 15.0
        # 1 - 1/9, literal run-verified 
        assert plan.quality_retention_rate == 0.8888888888888888
        assert plan.cost_saving_rate is None

    def test_voi_equal_to_cost_is_selected(self):
        # voi 0.25*4 = 1.0 == cost 1.0: boundary equality selects
        d = uc("d", p=0.25, impact=4, cost=1.0)
        plan = plan_minimal_update([d], epsilon=0.0)
        assert [x.id for x in plan.selected] == ["d"]
        assert plan.stop_reason == "exhausted"
        assert plan.residual_expected_loss == 0.0
        assert plan.quality_retention_rate == 1.0

    def test_selection_order_follows_voi_not_kind_grouping(self):
        lo_card = uc("lo", "decision_card", p=0.5, impact=2, cost=1.0)  # voi 1.0
        hi_prop = uc("hi", "proposal", p=0.5, impact=10, cost=1.0)  # voi 5.0
        plan = plan_minimal_update([lo_card, hi_prop], epsilon=0.0)
        assert [x.id for x in plan.selected] == ["hi", "lo"]


# ---------------------------------------------------------------------------
# 2. Epsilon early stop (target clause)
# ---------------------------------------------------------------------------


class TestEpsilonStop:
    def test_epsilon_stops_before_pending_candidate(self):
        a = uc("a", "proposal", p=0.5, impact=10, cost=3.0)  # voi 5.0
        b = uc("b", "decision_card", p=0.25, impact=12, cost=2.0)  # voi 3.0
        c = uc("c", "proposal", p=0.5, impact=2, cost=10.0)  # voi 1.0
        # after selecting a and b the residual is 1.0 <= epsilon -> c is never
        # examined; keeping c on its old conclusion is already within epsilon
        plan = plan_minimal_update([a, b, c], epsilon=1.0)
        assert [x.id for x in plan.selected] == ["a", "b"]
        assert [x.id for x in plan.skipped] == ["c"]
        assert plan.stop_reason == "epsilon_satisfied"
        assert plan.residual_expected_loss == 1.0
        assert plan.quality_retention_rate == 0.8888888888888888

    def test_epsilon_ge_total_voi_yields_empty_plan(self):
        a = uc("a", "proposal", p=0.5, impact=10, cost=3.0)  # voi 5.0
        b = uc("b", "decision_card", p=0.25, impact=12, cost=2.0)  # voi 3.0
        c = uc("c", "proposal", p=0.5, impact=2, cost=10.0)  # voi 1.0
        # total voi 9.0 <= epsilon at the very first boundary: minimal U = {}
        plan = plan_minimal_update([a, b, c], epsilon=9.0)
        assert plan.selected == []
        assert plan.stop_reason == "epsilon_satisfied"
        assert plan.residual_expected_loss == 9.0
        assert plan.quality_retention_rate == 0.0  # 1 - 9/9: nothing averted
        assert plan.cost_selected == 0.0

    def test_epsilon_rule_wins_at_same_boundary(self):
        # pending item has voi < cost AND residual <= epsilon: epsilon wins
        item = uc("p", p=0.5, impact=2, cost=100.0)  # voi 1.0 < 100.0
        plan = plan_minimal_update([item], epsilon=5.0)  # residual 1.0 <= 5.0
        assert plan.stop_reason == "epsilon_satisfied"


# ---------------------------------------------------------------------------
# 3. Marginal-benefit stop; stop reasons are distinct; never resumes
# ---------------------------------------------------------------------------


class TestMarginalBenefitStop:
    def test_deficit_stops_and_never_resumes(self):
        x = uc("x", p=0.25, impact=4, cost=10.0)  # voi 1.0 < 10.0 -> deficit
        y = uc("y", p=0.5, impact=1, cost=0.1)  # voi 0.5 >= 0.1, but later
        plan = plan_minimal_update([y, x], epsilon=0.0)
        # spec-literal "stop immediately": the walk halts at the first deficit
        # and the affordable y behind it is NOT selected
        assert plan.selected == []
        assert [i.id for i in plan.skipped] == ["x", "y"]
        assert plan.stop_reason == "marginal_benefit_below_cost"
        assert plan.residual_expected_loss == 1.5
        assert list(plan.per_item_voi) == ["x", "y"]

    def test_single_unaffordable_item_yields_empty_plan(self):
        s = uc("s", p=0.5, impact=2, cost=5.0)  # voi 1.0 < 5.0
        plan = plan_minimal_update([s], epsilon=0.0)
        assert plan.selected == []
        assert plan.stop_reason == "marginal_benefit_below_cost"
        assert plan.residual_expected_loss == 1.0
        assert plan.quality_retention_rate == 0.0  # 1 - 1/1

    def test_stop_reasons_are_distinct_between_rules(self):
        a = uc("a", p=0.5, impact=10, cost=3.0)  # voi 5.0, affordable
        c = uc("c", p=0.5, impact=2, cost=10.0)  # voi 1.0, deficit
        by_cost = plan_minimal_update([a, c], epsilon=0.0)
        by_eps = plan_minimal_update([a, c], epsilon=1.0)
        assert by_cost.stop_reason == "marginal_benefit_below_cost"
        assert by_eps.stop_reason == "epsilon_satisfied"
        assert by_cost.stop_reason != by_eps.stop_reason


# ---------------------------------------------------------------------------
# 4. Determinism and (kind, id) lexicographic tie-break
# ---------------------------------------------------------------------------


class TestDeterminismAndTieBreak:
    def test_equal_voi_ties_break_by_kind(self):
        t_prop = uc("t1", "proposal", p=0.5, impact=8, cost=100.0)  # voi 4.0
        t_card = uc("t2", "decision_card", p=0.25, impact=16, cost=1.0)  # voi 4.0
        plan = plan_minimal_update([t_prop, t_card], epsilon=0.0)
        # "decision_card" < "proposal" -> t2 examined first (and selected);
        # then t1: voi 4.0 < cost 100.0 -> stop
        assert [x.id for x in plan.selected] == ["t2"]
        assert plan.stop_reason == "marginal_benefit_below_cost"
        assert list(plan.per_item_voi) == ["t2", "t1"]

    def test_equal_voi_same_kind_ties_break_by_id(self):
        u_late = uc("u2", "proposal", p=0.25, impact=16, cost=100.0)  # voi 4.0
        u_early = uc("u1", "proposal", p=0.5, impact=8, cost=1.0)  # voi 4.0
        plan = plan_minimal_update([u_late, u_early], epsilon=0.0)
        # same kind -> id ascending: "u1" examined first and selected
        assert [x.id for x in plan.selected] == ["u1"]
        assert list(plan.per_item_voi) == ["u1", "u2"]

    def test_same_input_same_output_regardless_of_input_order(self):
        a = uc("a", "proposal", p=0.5, impact=10, cost=3.0)
        b = uc("b", "decision_card", p=0.25, impact=12, cost=2.0)
        c = uc("c", "proposal", p=0.5, impact=2, cost=10.0)
        p1 = plan_minimal_update([a, b, c], epsilon=1.0, total_rebuild_cost=20.0)
        p2 = plan_minimal_update([c, a, b], epsilon=1.0, total_rebuild_cost=20.0)
        p3 = plan_minimal_update([b, c, a], epsilon=1.0, total_rebuild_cost=20.0)
        assert p1 == p2 == p3
        assert list(p1.per_item_voi) == list(p2.per_item_voi) == list(p3.per_item_voi)
        # re-running on the identical input is stable too
        assert plan_minimal_update([a, b, c], epsilon=1.0, total_rebuild_cost=20.0) == p1


# ---------------------------------------------------------------------------
# 5. Empty input / degenerate semantics
# ---------------------------------------------------------------------------


class TestDegenerateSemantics:
    def test_empty_input(self):
        plan = plan_minimal_update([], epsilon=0.5)
        assert plan.selected == []
        assert plan.skipped == []
        assert plan.per_item_voi == {}
        assert plan.residual_expected_loss == 0.0
        assert plan.cost_selected == 0.0
        assert plan.cost_total == 0.0
        assert plan.stop_reason == "exhausted"
        assert plan.quality_retention_rate == 1.0  # total VOI == 0 -> 1.0
        assert plan.cost_saving_rate is None

    def test_empty_input_with_rebuild_cost(self):
        plan = plan_minimal_update([], epsilon=0.5, total_rebuild_cost=10.0)
        assert plan.cost_saving_rate == 1.0  # 1 - 0/10

    def test_all_unaffordable_items_yield_empty_plan(self):
        hi = uc("hi", p=0.5, impact=2, cost=5.0)  # voi 1.0 < 5.0
        lo = uc("lo", p=0.5, impact=1, cost=4.0)  # voi 0.5 < 4.0
        plan = plan_minimal_update([hi, lo], epsilon=0.0)
        assert plan.selected == []
        assert plan.stop_reason == "marginal_benefit_below_cost"
        assert plan.residual_expected_loss == 1.5
        assert plan.quality_retention_rate == 0.0  # 1 - 1.5/1.5

    def test_zero_total_voi_is_epsilon_satisfied_with_full_retention(self):
        z = uc("z", p=0.0, impact=7, cost=1.0)  # voi 0.0 via p_change
        plan = plan_minimal_update([z], epsilon=0.0)
        # residual 0.0 <= epsilon 0.0 fires the epsilon rule BEFORE the
        # benefit rule at the same boundary
        assert plan.selected == []
        assert plan.stop_reason == "epsilon_satisfied"
        assert plan.residual_expected_loss == 0.0
        assert plan.quality_retention_rate == 1.0  # total VOI == 0 -> 1.0

        z2 = uc("z2", p=0.9, impact=0, cost=2.0)  # voi 0.0 via impact
        plan2 = plan_minimal_update([z2], epsilon=0.0)
        assert plan2.quality_retention_rate == 1.0

    def test_negative_epsilon_disables_the_epsilon_rule(self):
        # residual is always >= 0, so residual <= negative epsilon never fires
        a = uc("a", p=0.5, impact=10, cost=3.0)  # voi 5.0, affordable
        c = uc("c", p=0.5, impact=2, cost=10.0)  # voi 1.0 < 10.0
        plan = plan_minimal_update([a, c], epsilon=-1.0)
        assert [x.id for x in plan.selected] == ["a"]
        assert plan.stop_reason == "marginal_benefit_below_cost"


# ---------------------------------------------------------------------------
# 6. Validation (ValueError, fail-fast at construction / call)
# ---------------------------------------------------------------------------


class TestValidation:
    def test_p_change_outside_unit_interval_raises(self):
        with pytest.raises(ValueError):
            UpdateCandidate(id="x", kind="proposal", p_change=1.5, impact=3, cost=1.0)
        with pytest.raises(ValueError):
            UpdateCandidate(id="x", kind="proposal", p_change=-0.1, impact=3, cost=1.0)
        # NaN must not slip through the range check
        with pytest.raises(ValueError):
            UpdateCandidate(
                id="x", kind="proposal", p_change=math.nan, impact=3, cost=1.0
            )

    def test_negative_impact_raises(self):
        with pytest.raises(ValueError):
            UpdateCandidate(id="x", kind="proposal", p_change=0.5, impact=-1, cost=1.0)

    def test_non_positive_cost_raises(self):
        with pytest.raises(ValueError):
            UpdateCandidate(id="x", kind="proposal", p_change=0.5, impact=3, cost=0.0)
        with pytest.raises(ValueError):
            UpdateCandidate(id="x", kind="proposal", p_change=0.5, impact=3, cost=-2.0)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            UpdateCandidate(id="x", kind="entity", p_change=0.5, impact=3, cost=1.0)

    def test_duplicate_ids_raise(self):
        with pytest.raises(ValueError):
            plan_minimal_update([uc("dup"), uc("dup")], epsilon=0.0)

    def test_non_positive_rebuild_cost_raises(self):
        good = uc("g", p=0.5, impact=2, cost=1.0)
        with pytest.raises(ValueError):
            plan_minimal_update([good], epsilon=0.0, total_rebuild_cost=0.0)
        with pytest.raises(ValueError):
            plan_minimal_update([good], epsilon=0.0, total_rebuild_cost=-5.0)


# ---------------------------------------------------------------------------
# 7. cost_saving_rate math
# ---------------------------------------------------------------------------


class TestCostSavingRate:
    def test_saving_rate_with_rebuild_cost(self):
        a = uc("a", "proposal", p=0.5, impact=10, cost=3.0)  # voi 5.0
        b = uc("b", "decision_card", p=0.25, impact=12, cost=2.0)  # voi 3.0
        plan = plan_minimal_update([a, b], epsilon=0.0, total_rebuild_cost=20.0)
        assert [x.id for x in plan.selected] == ["a", "b"]  # both affordable
        assert plan.cost_selected == 5.0
        assert plan.cost_saving_rate == 0.75  # 1 - 5/20, exact

    def test_saving_rate_is_none_without_rebuild_cost(self):
        a = uc("a", p=0.5, impact=10, cost=3.0)
        plan = plan_minimal_update([a], epsilon=0.0)
        assert plan.cost_saving_rate is None

    def test_saving_rate_never_negative_when_rebuild_covers_selection(self):
        # partial selection: only the affordable head is taken
        a = uc("a", p=0.5, impact=10, cost=3.0)  # voi 5.0, selected
        c = uc("c", p=0.5, impact=2, cost=10.0)  # voi 1.0, skipped
        plan = plan_minimal_update([a, c], epsilon=0.0, total_rebuild_cost=20.0)
        assert plan.cost_selected == 3.0
        assert plan.cost_saving_rate == 0.85  # 1 - 3/20, exact


# ---------------------------------------------------------------------------
# 8. per_item_voi fully exposed (L8 reuse point)
# ---------------------------------------------------------------------------


class TestPerItemVoiExposure:
    def test_every_candidate_exposed_including_skipped(self):
        items = [
            uc("p1", "proposal", p=0.5, impact=40, cost=6.0),  # voi 20.0
            uc("d1", "decision_card", p=0.5, impact=30, cost=4.0),  # voi 15.0
            uc("d4", "decision_card", p=0.125, impact=20, cost=3.0),  # voi 2.5
        ]
        plan = plan_minimal_update(items, epsilon=0.0)
        assert set(plan.per_item_voi) == {"p1", "d1", "d4"}
        assert plan.per_item_voi["d4"] == 2.5  # skipped items still exposed
        assert plan.per_item_voi["p1"] == 20.0
        assert list(plan.per_item_voi) == ["p1", "d1", "d4"]  # greedy order
        assert [x.id for x in plan.selected] == ["p1", "d1"]
        assert [x.id for x in plan.skipped] == ["d4"]
        # L8 reuse: the ranking can be rebuilt from the plan alone
        ranked = sorted(plan.per_item_voi.items(), key=lambda kv: -kv[1])
        assert [k for k, _ in ranked] == ["p1", "d1", "d4"]

    def test_voi_values_are_p_change_times_impact(self):
        items = [
            uc("i1", "proposal", p=0.75, impact=8, cost=1.0),  # voi 6.0
            uc("i2", "decision_card", p=0.125, impact=16, cost=9.0),  # voi 2.0
        ]
        plan = plan_minimal_update(items, epsilon=0.0)
        assert plan.per_item_voi["i1"] == pytest.approx(0.75 * 8)
        assert plan.per_item_voi["i2"] == pytest.approx(0.125 * 16)
        assert plan.residual_expected_loss == pytest.approx(2.0)
