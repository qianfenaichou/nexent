"""Evolution-loop kernel: minimal sufficient update set (VOI greedy planner).

Pure-function planner for the "minimal sufficient update set" algorithm:
given the affected surface of one change wave (ontology proposals P_aff and
decision cards D_aff), select the smallest human-confirmation / recompute
set U such that the expected quality loss of keeping the OLD conclusion for
everything outside U stays within epsilon.

Each item carries three caller-supplied signals:

- ``p_change``: P(the conclusion flips because of the change), estimated by
  the CALLER from change-overlap / alignment-conflict signals. This module
  never estimates probabilities.
- ``impact``: how many times the conclusion is referenced (citation count).
- ``cost``: confirmation / recompute cost, any unit, as long as it is the
  same unit as ``total_rebuild_cost`` when that is supplied.

Algorithm (frozen):

- VOI_i = p_change_i * impact_i; candidates are walked in VOI-descending
  order, ties broken by (kind, id) lexicographic ascending;
- a candidate is admitted into U iff ``voi >= cost`` (equality selects);
- stop rules, whichever fires first:
  1. epsilon target (spec goal clause): BEFORE examining the pending
     candidate, if the residual expected loss of leaving the pending
     candidate and everything after it unselected already satisfies
     ``residual <= epsilon``, stop ("epsilon_satisfied") - selecting more
     would violate minimality; every skipped item keeps its old conclusion
     within the bound;
  2. marginal benefit (spec method clause, literal): the first pending
     candidate with ``voi < cost`` halts the walk
     ("marginal_benefit_below_cost"). The greedy NEVER resumes after a
     deficit: the pending item and all later items are skipped even if a
     later item alone would satisfy ``voi >= cost``. Resuming would be a
     different, budget-maximizing algorithm and is deliberately not
     implemented here;
  3. if the walk consumes every candidate: "exhausted" (residual 0);
- at one boundary where both rules 1 and 2 would fire, the epsilon rule
  wins: the quality target is the primary objective, the cost rule is the
  efficiency heuristic.

``epsilon`` may be any float; a negative epsilon (or NaN) disables rule 1
because the residual is always >= 0, leaving the greedy to run until rule 2
or exhaustion.

Degenerate semantics: when the total VOI over all candidates is 0 (nothing
can change), ``quality_retention_rate`` is defined as 1.0 (nothing is at
risk). Otherwise it is ``1 - residual_expected_loss / total_voi``.

Determinism: the same candidate list (in any input order) with the same
epsilon and total_rebuild_cost yields an exactly equal plan - the sort key
is total, the float arithmetic runs in one fixed order, and ``per_item_voi``
/ ``skipped`` follow the deterministic greedy order.

Honest layering: this kernel is stdlib-only, DB-free and LLM-free. Turning
deltaS / E_aff / D_aff / P_aff into UpdateCandidate rows - including every
p_change estimate - belongs to the caller; wiring a production call site is
out of scope here (/ follow-up). ``per_item_voi`` exposes EVERY
candidate's VOI (selected and skipped alike) so the human-review budget
curve can reuse the same ranking without re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass

CANDIDATE_KINDS = ("proposal", "decision_card")

STOP_EPSILON_SATISFIED = "epsilon_satisfied"
STOP_MARGINAL_BENEFIT_BELOW_COST = "marginal_benefit_below_cost"
STOP_EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class UpdateCandidate:
    """One item on the affected surface that may need human confirmation.

    Validation is fail-fast at construction (spec: 校验先行): unknown kind,
    p_change outside [0, 1] (NaN included), negative impact and non-positive
    cost all raise ValueError.
    """

    id: str
    kind: str  # one of CANDIDATE_KINDS
    p_change: float  # P(conclusion flips), caller-estimated, within [0, 1]
    impact: int  # times the conclusion is referenced, >= 0
    cost: float  # confirmation/recompute cost, > 0

    def __post_init__(self) -> None:
        if self.kind not in CANDIDATE_KINDS:
            raise ValueError(
                f"kind must be one of {CANDIDATE_KINDS}, got {self.kind!r}"
            )
        if not (0.0 <= self.p_change <= 1.0):
            raise ValueError(
                f"p_change must be within [0, 1], got {self.p_change!r}"
            )
        if self.impact < 0:
            raise ValueError(f"impact must be >= 0, got {self.impact!r}")
        if self.cost <= 0:
            raise ValueError(f"cost must be > 0, got {self.cost!r}")


@dataclass(frozen=True)
class UpdatePlan:
    """Result of :func:`plan_minimal_update` (all fields documented there)."""

    selected: list[UpdateCandidate]
    skipped: list[UpdateCandidate]
    per_item_voi: dict[str, float]
    residual_expected_loss: float
    cost_selected: float
    cost_total: float
    cost_saving_rate: float | None
    quality_retention_rate: float
    stop_reason: str


def _voi(candidate: UpdateCandidate) -> float:
    """Value of information: P(change flips the conclusion) x impact."""
    return candidate.p_change * candidate.impact


def plan_minimal_update(
    candidates: list[UpdateCandidate],
    epsilon: float,
    *,
    total_rebuild_cost: float | None = None,
) -> UpdatePlan:
    """Plan the minimal sufficient update set U for one change wave.

    Args:
        candidates: affected-surface items (P_aff + D_aff). Ids must be
            unique - ``per_item_voi`` is keyed by id and duplicates would
            silently collapse it. Input order is irrelevant: the plan is
            computed over the deterministic VOI-descending, (kind, id)
            lexicographic order.
        epsilon: quality-loss bound for the target clause "items outside U
            keep their old conclusion with expected loss <= epsilon".
        total_rebuild_cost: optional cost of rebuilding the whole surface
            in the same unit as ``UpdateCandidate.cost``. When given, the
            plan reports ``cost_saving_rate = 1 - cost_selected /
            total_rebuild_cost`` (the frozen formalization of "incremental
            evolution vs full rebuild"); when None, ``cost_saving_rate`` is
            None. Must be > 0 when given.

    Returns:
        UpdatePlan with:
        - ``selected``: U, in greedy (VOI-descending) order;
        - ``skipped``: everything not in U, in the same deterministic order;
        - ``per_item_voi``: id -> VOI for EVERY candidate (selected and
          skipped), in greedy order - the reuse point for the budget curve;
        - ``residual_expected_loss``: running sum of VOI left unselected
          (mathematically the total VOI minus the selected VOI); after an
          epsilon stop it is guaranteed <= epsilon;
        - ``cost_selected`` / ``cost_total``: cost of U / cost of confirming
          everything (the non-minimal upper bound);
        - ``cost_saving_rate``: see ``total_rebuild_cost`` above;
        - ``quality_retention_rate``: 1 - residual/total_voi, defined as 1.0
          when total_voi == 0;
        - ``stop_reason``: "epsilon_satisfied" | "marginal_benefit_below_cost"
          | "exhausted" (semantics in the module docstring).

    Raises:
        ValueError: duplicate candidate ids, or non-positive
            ``total_rebuild_cost``. Candidate-field violations raise at
            ``UpdateCandidate`` construction instead (fail-fast boundary).
    """
    ids = [c.id for c in candidates]
    if len(ids) != len(set(ids)):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"candidate ids must be unique, duplicates: {duplicates}")
    if total_rebuild_cost is not None and total_rebuild_cost <= 0:
        raise ValueError(
            f"total_rebuild_cost must be > 0 when given, got {total_rebuild_cost!r}"
        )

    ordered = sorted(candidates, key=lambda c: (-_voi(c), c.kind, c.id))
    per_item_voi = {c.id: _voi(c) for c in ordered}
    total_voi = sum(per_item_voi.values())

    selected: list[UpdateCandidate] = []
    cost_selected = 0.0
    residual = total_voi
    stop_reason = STOP_EXHAUSTED
    for cand in ordered:
        # Rule 1 (epsilon target) is checked BEFORE rule 2 at every boundary:
        # if stopping now already satisfies the quality bound, selecting more
        # would violate minimality. Both-fire boundaries go to rule 1.
        if residual <= epsilon:
            stop_reason = STOP_EPSILON_SATISFIED
            break
        voi = per_item_voi[cand.id]
        # Rule 2 (marginal benefit, spec-literal): first deficit halts the
        # walk; the greedy never resumes behind a deficit.
        if voi < cand.cost:
            stop_reason = STOP_MARGINAL_BENEFIT_BELOW_COST
            break
        selected.append(cand)
        cost_selected += cand.cost
        residual -= voi

    selected_ids = {c.id for c in selected}
    skipped = [c for c in ordered if c.id not in selected_ids]
    cost_total = float(sum(c.cost for c in ordered))
    quality_retention_rate = (
        1.0 if total_voi == 0.0 else 1.0 - residual / total_voi
    )
    cost_saving_rate = (
        None
        if total_rebuild_cost is None
        else 1.0 - cost_selected / total_rebuild_cost
    )
    return UpdatePlan(
        selected=selected,
        skipped=skipped,
        per_item_voi=per_item_voi,
        residual_expected_loss=residual,
        cost_selected=cost_selected,
        cost_total=cost_total,
        cost_saving_rate=cost_saving_rate,
        quality_retention_rate=quality_retention_rate,
        stop_reason=stop_reason,
    )
