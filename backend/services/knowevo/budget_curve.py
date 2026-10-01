"""L8 human-review budget-quality curve kernel.

Given the L7 planner's ``per_item_voi`` (key order = greedy order, see
``update_planner.plan_minimal_update``), a per-item cost table, and a budget
sequence, produce quality-curve points: for each budget, the set of items an
expert can confirm and the ontology quality at that point.

The scientific claim this kernel supports is deliberately narrow (KnowEvo
提分总纲 §L8): "under a fixed expert-confirmation budget, the four ontology
quality metrics (Cov / Red / Dep / Align) as a function of how many items
were confirmed" - NOT "semi-automatic is X times faster than a human" (that
would need an expert baseline we do not have).

Review-order semantics (frozen): items are walked in the caller-supplied
review order (default = ``per_item_voi`` key order = the L7 greedy order);
an item is confirmed iff its cost fits in the remaining budget. Admission
is PREFIX: the first item that does not fit halts the walk and no later,
cheaper item is taken - the same "stop at first deficit, never resume"
rule as the L7 planner. The x-axis "confirmation count" is the special
case where every cost is 1.0 and budgets are 1..n.

Quality is an injectable callable ``quality_fn(confirmed_ids) -> mapping``
over the confirmed-id prefix in review order. The recommended default is
the production metric implementation
``ontology_service._k0_metrics_from_snapshot`` (Cov/Red/Dep/Align, 05-计划书
§3.4); :func:`k0_quality_fn` builds such a callable and falls back to that
implementation when no ``metrics_fn`` is injected.

Honest layering: this kernel is stdlib-only at import time, DB-free and
LLM-free. Estimating p_change / impact / confidence belongs to the caller
(see update_planner). Real human-minute costs live in
``evolution_round_t.cost.human_minutes`` and are mostly unset (settle writes
0.0 by default) - when they are missing, the caller must report
``insufficient_data`` rather than invent a cost table.

Evidence gate (2x2 with order, probe_p7): a gate drops items from the
review queue BEFORE ranking, so gated-off items never consume budget.
:func:`gate_filter` is the seam; the 2x2 arms are built by the caller.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

QualityFn = Callable[[tuple[str, ...]], Mapping[str, Any]]


@dataclass(frozen=True)
class CurvePoint:
    """One point on the budget-quality curve.

    ``confirmed_ids`` is the prefix of the review order that fits in
    ``budget``; ``quality`` is the injected quality callable's result for
    that prefix (copied, never aliased).
    """

    budget: float
    confirmed_ids: tuple[str, ...]
    n_confirmed: int
    cost_spent: float
    quality: dict[str, Any]


def _finite(name: str, value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number, got {value!r}") from exc
    if not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return v


def order_by_score(
    scores: Mapping[str, float],
    *,
    descending: bool = True,
) -> tuple[str, ...]:
    """Rank ids by score; ties broken by id lexicographic ascending.

    Same total-order discipline as ``update_planner`` (VOI-descending with
    an id tie-break), so a score-ordered arm is comparable to a VOI-ordered
    arm without a second ad-hoc sort rule.
    """
    sign = -1.0 if descending else 1.0
    return tuple(
        sorted(scores.keys(), key=lambda i: (sign * _finite(f"score[{i!r}]", scores[i]), i))
    )


def order_randomly(ids: Iterable[str], seed: int) -> tuple[str, ...]:
    """Deterministic seeded permutation of ``ids`` (the random-order arm).

    The same iterable contents and seed always yield the same tuple; this is
    the negative-control ordering where rank is independent of VOI and of
    quality contribution.
    """
    items = list(ids)
    if len(items) != len(set(items)):
        raise ValueError(f"ids must be unique, got {items!r}")
    rng = random.Random(seed)
    rng.shuffle(items)
    return tuple(items)


def gate_filter(
    ids: Iterable[str],
    keep: Callable[[str], bool],
) -> tuple[str, ...]:
    """Evidence-gate filter: ids failing ``keep`` never enter the review queue.

    The 2x2 gate x order design (probe_p7) toggles the gate arm by calling
    this with a constant-True keep (gate off) versus a real evidence test.
    """
    return tuple(i for i in ids if keep(i))


def k0_quality_fn(
    base_classes: Sequence[Mapping[str, Any]],
    item_classes: Mapping[str, Sequence[Mapping[str, Any]]],
    seed_terms: Sequence[str] | None = None,
    *,
    metrics_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> QualityFn:
    """Build a Quality callable backed by the production four metrics.

    Confirming item ``i`` appends ``item_classes[i]`` (may be missing/empty)
    to ``base_classes``; the snapshot ``{"classes": [...]}`` is then scored
    with ``metrics_fn(snapshot, seed_terms=seed_terms)``. When ``metrics_fn``
    is None the production ``ontology_service._k0_metrics_from_snapshot`` is
    imported lazily so this module stays stdlib-only at import time.

    The caller's ``base_classes`` / ``item_classes`` are never mutated.
    """
    if metrics_fn is None:
        from services.knowevo.ontology_service import _k0_metrics_from_snapshot

        metrics_fn = _k0_metrics_from_snapshot
    if not callable(metrics_fn):
        raise TypeError(f"metrics_fn must be callable, got {metrics_fn!r}")
    base_list = [dict(c) for c in base_classes]
    effects = {k: [dict(c) for c in v] for k, v in item_classes.items()}
    terms = list(seed_terms) if seed_terms is not None else None

    def quality(confirmed_ids: tuple[str, ...]) -> dict[str, Any]:
        classes = list(base_list)
        for i in confirmed_ids:
            classes.extend(effects.get(i, ()))
        result = metrics_fn({"classes": classes}, terms)
        return dict(result)

    return quality


def budget_curve(
    per_item_voi: Mapping[str, float],
    costs: Mapping[str, float],
    budgets: Sequence[float],
    quality_fn: QualityFn,
    *,
    order: Sequence[str] | None = None,
) -> list[CurvePoint]:
    """Evaluate the budget-quality curve at each budget checkpoint.

    Args:
        per_item_voi: id -> VOI for EVERY candidate, key order = greedy
            order (the L8 reuse point of
            ``update_planner.plan_minimal_update.per_item_voi``). Defines
            the candidate id set and, when ``order`` is None, the review
            order. For a confidence/random arm pass the real VOI map and
            override ``order``.
        costs: id -> confirmation cost (> 0, finite, same unit as budgets;
            use 1.0 for a pure "confirmation count" axis).
        budgets: budget checkpoints (finite, >= 0). Each checkpoint is an
            independent evaluation of the same review order; the sequence
            need not be sorted, duplicates allowed.
        quality_fn: called with the confirmed-id prefix (tuple, review
            order) at every checkpoint; must return a mapping (copied into
            the CurvePoint).
        order: optional review order; must be a permutation of the id set.
            Default is ``tuple(per_item_voi)``.

    Returns:
        One CurvePoint per entry of ``budgets``, in the same order.

    Raises:
        ValueError: cost not positive / not finite, budget negative / not
            finite, id-set mismatch between ``per_item_voi`` and ``costs``,
            or ``order`` not a permutation of the id set.
        TypeError: ``quality_fn`` is not callable.
    """
    if not callable(quality_fn):
        raise TypeError(f"quality_fn must be callable, got {quality_fn!r}")

    voi_ids = list(per_item_voi)
    if set(voi_ids) != set(costs):
        raise ValueError(
            "per_item_voi and costs must cover the same id set, got "
            f"voi={sorted(voi_ids)} costs={sorted(costs)}"
        )
    if len(voi_ids) != len(set(voi_ids)):
        raise ValueError("per_item_voi keys must be unique")

    clean_costs: dict[str, float] = {}
    for i in voi_ids:
        c = _finite(f"costs[{i!r}]", costs[i])
        if c <= 0.0:
            raise ValueError(f"costs[{i!r}] must be > 0, got {c!r}")
        clean_costs[i] = c

    for i in voi_ids:
        _finite(f"per_item_voi[{i!r}]", per_item_voi[i])

    if order is None:
        review_order: tuple[str, ...] = tuple(voi_ids)
    else:
        review_order = tuple(order)
        if len(review_order) != len(set(review_order)):
            raise ValueError(f"order must not repeat ids, got {review_order!r}")
        if set(review_order) != set(voi_ids):
            raise ValueError(
                "order must be a permutation of the id set, got "
                f"order={sorted(review_order)} ids={sorted(voi_ids)}"
            )

    clean_budgets = []
    for b in budgets:
        v = _finite("budget", b)
        if v < 0.0:
            raise ValueError(f"budget must be >= 0, got {v!r}")
        clean_budgets.append(v)

    curve: list[CurvePoint] = []
    for budget in clean_budgets:
        confirmed: list[str] = []
        spent = 0.0
        for item_id in review_order:
            c = clean_costs[item_id]
            if spent + c > budget:
                break
            confirmed.append(item_id)
            spent += c
        quality = dict(quality_fn(tuple(confirmed)))
        curve.append(
            CurvePoint(
                budget=budget,
                confirmed_ids=tuple(confirmed),
                n_confirmed=len(confirmed),
                cost_spent=spent,
                quality=quality,
            )
        )
    return curve
