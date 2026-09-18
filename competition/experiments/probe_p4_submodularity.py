"""Empirical submodularity probe for the greedy VOI-selection claim (plan section 3.1).

Background
----------
The project plan (05-计划书, section 3.1) claims: when the impact function is
coverage-type it is submodular, so greedy selection carries the classic
(1 - 1/e) ~= 0.63 approximation guarantee; the production impact function
contains non-coverage terms, so no strict guarantee is claimed. This probe
supplies data for that claim. Python 3 standard library only.

Part A - synthetic coverage setting, exhaustively enumerable
    n proposals (default 14, hard-capped at 20), each covering an i.i.d.
    Bernoulli subset (p = COVER_PROB_PART_A) of a ground set of
    GROUND_SIZE_PART_A elements. f(S) = |union_{p in S} cover(p)| is a coverage
    function: monotone and submodular in theory. Per random instance:
      * TRUE optimum under budget k: brute-force enumeration of EVERY subset of
        size <= k. No approximate solver is used anywhere; monotonicity would
        justify restricting to size exactly k, but we enumerate all sizes <= k
        so the optimality claim leans on nothing unverified.
      * deterministic greedy: argmax marginal coverage each step, ties broken
        by lowest proposal index (stable ordering, exact integer arithmetic).
      * ratio = f(greedy) / f(optimum), aggregated over >= 300 instances as a
        distribution (min / p10 / median / mean / max), plus the fraction of
        instances with ratio >= (1 - 1/e) and the fraction where greedy is
        exactly optimal (integer-valued comparison, hence exact).

Part B - real-shaped dual-channel impact, diminishing-returns curve
    Each proposal covers an entity subset and an evidence subset (correlated
    via latent topics to mimic realistic overlap of cited entities and evidence
    segments). f(S) = w1 * |union of entities| + w2 * |union of evidence|, with
    w1 / w2 recorded in the JSON config. Dynamic greedy adds ALL proposals one
    by one, each step taking the maximum marginal VOI (ties -> lowest index),
    and records the marginal gain per selection position. For a submodular
    impact this greedy marginal-gain sequence is provably non-increasing, so
    any increase is direct evidence against (empirical) submodularity. The
    curve is aggregated over instances; violations of the non-increasing
    property are reported both on the mean curve and per instance.

Honesty notes
    * The coverage assumption is an idealization; for the real impact function
      only empirical monotonicity is verified here, never a strict
      submodularity guarantee.
    * The simulated dual-channel impact of Part B is itself a nonnegative
      weighted sum of two coverage functions, hence submodular in theory as
      well; real non-coverage terms are NOT simulated. Part B therefore
      validates the measurement pipeline and the empirical diminishing-returns
      shape, and must not be read as a proof for the production function.
    * w1 = 1.0 and w2 = 0.5 are exactly representable in binary floating
      point, so Part B per-instance gain comparisons carry no rounding noise;
      a 1e-9 tolerance is additionally reported for the mean curve, whose
      entries are averages of many exact values.
    * Fraction metrics are always reported together with their denominators;
      degenerate instances with f(optimum) = 0 are counted separately instead
      of being silently dropped, and no exception is ever swallowed.

Usage
    python3 probe_p4_submodularity.py [--seed S] [--instances N] [--n N] [--output PATH]

适用范围：合成条件下的机制验证，非真实数据结论。
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import platform
import random
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Pre-registered experiment constants (recorded verbatim in the output JSON).
# --------------------------------------------------------------------------
ONE_MINUS_ONE_OVER_E = 1.0 - math.exp(-1.0)

GROUND_SIZE_PART_A = 30      # ground-set size of the synthetic coverage setting
COVER_PROB_PART_A = 0.25     # per-element inclusion probability of a proposal cover

ENTITY_GROUND_SIZE = 40      # ground-set size of the "cited entities" channel
EVIDENCE_GROUND_SIZE = 60    # ground-set size of the "evidence segments" channel
W1_ENTITY = 1.0              # entity-channel weight (exactly representable in binary)
W2_EVIDENCE = 0.5            # evidence-channel weight (exactly representable in binary)
PART_B_TOPICS = 4            # latent topics inducing correlated overlap
PART_B_ENTITY_CORE = 8       # topic-core size on the entity ground set
PART_B_EVIDENCE_CORE = 15    # topic-core size on the evidence ground set
PART_B_MAX_ENT = 10          # max entity elements per proposal
PART_B_MAX_EV = 20           # max evidence segments per proposal

MAX_N_EXHAUSTIVE = 20        # hard cap: keeps exhaustive enumeration feasible
SEED_OFFSET_PART_B = 1_000_000_007
MEAN_CURVE_TOL = 1e-9

DEFAULT_OUTPUT = (
    "/home/qianqian/Work/All/Nexent/nexent/competition/deliverables/"
    "algorithm-probes/probe_p4_submodularity.json"
)


if hasattr(int, "bit_count"):  # Python >= 3.10

    def _popcount(value: int) -> int:
        return value.bit_count()

else:

    def _popcount(value: int) -> int:
        return bin(value).count("1")


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile of an ascending-sorted list, q in [0, 100]."""
    if not sorted_values:
        raise ValueError("percentile of an empty list is undefined")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * (q / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[int(lo)])
    frac = pos - lo
    return float(sorted_values[int(lo)]) * (1.0 - frac) + float(sorted_values[int(hi)]) * frac


def count_increases(values: list[float], tol: float = 0.0) -> list[int]:
    """Indices i where values[i + 1] > values[i] + tol (violations of non-increase)."""
    return [i for i in range(len(values) - 1) if values[i + 1] > values[i] + tol]


# --------------------------------------------------------------------------
# Part A: synthetic coverage setting with exhaustive optimum.
# --------------------------------------------------------------------------
def make_part_a_instance(rng: random.Random, n: int) -> list[int]:
    """One synthetic coverage instance: n bitmask covers over GROUND_SIZE_PART_A elements."""
    covers = []
    for _ in range(n):
        mask = 0
        for element in range(GROUND_SIZE_PART_A):
            if rng.random() < COVER_PROB_PART_A:
                mask |= 1 << element
        covers.append(mask)
    return covers


def exhaustive_optimum_cover(covers: list[int], k: int) -> tuple[int, int]:
    """TRUE optimum of f(S)=|union covers| over ALL subsets of size <= k (brute force).

    Returns (optimum_value, number_of_subsets_evaluated). Monotonicity would let
    us restrict to size exactly k; every size <= k is enumerated anyway so the
    optimality claim does not lean on any unverified assumption.
    """
    best = 0
    combos = 0
    for size in range(k + 1):
        for combo in itertools.combinations(range(len(covers)), size):
            combos += 1
            union = 0
            for idx in combo:
                union |= covers[idx]
            value = _popcount(union)
            if value > best:
                best = value
    return best, combos


def greedy_cover(covers: list[int], k: int) -> tuple[list[int], int]:
    """Deterministic greedy: argmax marginal coverage each step, ties -> lowest index.

    Exact integer arithmetic; the strict '>' comparison makes the first (lowest
    index) maximizer win, so the selection is fully deterministic.
    """
    chosen: list[int] = []
    chosen_union = 0
    remaining = list(range(len(covers)))
    for _ in range(min(k, len(covers))):
        base = _popcount(chosen_union)
        best_idx = -1
        best_gain = -1
        best_union = chosen_union
        for idx in remaining:
            union = chosen_union | covers[idx]
            gain = _popcount(union) - base
            if gain > best_gain:
                best_gain, best_idx, best_union = gain, idx, union
        if best_idx < 0:
            raise RuntimeError("greedy found no candidate although the pool is non-empty")
        chosen.append(best_idx)
        chosen_union = best_union
        remaining.remove(best_idx)
    return chosen, _popcount(chosen_union)


def run_part_a(rng: random.Random, n: int, k: int, instances: int) -> dict:
    ratios: list[float] = []
    combos_total = 0
    greedy_optimal_count = 0
    ge_bound_count = 0
    zero_optimum_count = 0
    f_opt_sum = 0
    f_greedy_sum = 0
    for _ in range(instances):
        covers = make_part_a_instance(rng, n)
        optimum, combos = exhaustive_optimum_cover(covers, k)
        combos_total += combos
        _, greedy_value = greedy_cover(covers, k)
        f_opt_sum += optimum
        f_greedy_sum += greedy_value
        if optimum == 0:
            # Degenerate instance (every cover empty): greedy is trivially optimal,
            # 0/0 is defined as 1.0 and the instance is counted separately.
            zero_optimum_count += 1
            ratio = 1.0
        else:
            ratio = greedy_value / optimum
        if greedy_value == optimum:
            greedy_optimal_count += 1
        if ratio >= ONE_MINUS_ONE_OVER_E:
            ge_bound_count += 1
        ratios.append(ratio)
    ratios.sort()
    return {
        "n_proposals": n,
        "budget_k": k,
        "instances": instances,
        "exhaustive": True,
        "optimum_method": "brute-force enumeration of every subset of size <= k (no approximate solver)",
        "subsets_enumerated_total": combos_total,
        "ratio_stats": {
            "n_instances": instances,
            "min": ratios[0],
            "p10": _percentile(ratios, 10.0),
            "median": statistics.median(ratios),
            "mean": statistics.fmean(ratios),
            "max": ratios[-1],
        },
        "greedy_optimal_ratio_eq_1": {
            "count": greedy_optimal_count,
            "total": instances,
            "fraction": greedy_optimal_count / instances,
        },
        "ratio_ge_one_minus_one_over_e": {
            "threshold": ONE_MINUS_ONE_OVER_E,
            "count": ge_bound_count,
            "total": instances,
            "fraction": ge_bound_count / instances,
        },
        "degenerate_zero_optimum_instances": zero_optimum_count,
        "mean_f_optimal": f_opt_sum / instances,
        "mean_f_greedy": f_greedy_sum / instances,
        "theory_note": (
            "For a monotone submodular (coverage) objective, deterministic greedy guarantees "
            "f(greedy) >= (1 - 1/e) * OPT on every instance; any ratio below the bound would "
            "indicate an implementation bug, not a theory violation."
        ),
    }


# --------------------------------------------------------------------------
# Part B: dual-channel (entities + evidence segments) impact, greedy curve.
# --------------------------------------------------------------------------
def _draw_subset(rng: random.Random, core: set[int], ground_size: int, max_size: int) -> set[int]:
    """Random subset: roughly half drawn from the topic core, rest uniformly at random."""
    size = rng.randint(1, max_size)
    chosen = set(rng.sample(sorted(core), min(len(core), size // 2)))
    while len(chosen) < size:
        chosen.add(rng.randrange(ground_size))
    return chosen


def _set_to_mask(elements: set[int]) -> int:
    mask = 0
    for element in elements:
        mask |= 1 << element
    return mask


def make_part_b_instance(rng: random.Random, n: int) -> tuple[list[int], list[int]]:
    """Dual-channel instance: correlated entity/evidence covers via latent topics."""
    ent_cores = [set(rng.sample(range(ENTITY_GROUND_SIZE), PART_B_ENTITY_CORE)) for _ in range(PART_B_TOPICS)]
    ev_cores = [set(rng.sample(range(EVIDENCE_GROUND_SIZE), PART_B_EVIDENCE_CORE)) for _ in range(PART_B_TOPICS)]
    ent_masks: list[int] = []
    ev_masks: list[int] = []
    for i in range(n):
        ent = _draw_subset(rng, ent_cores[i % PART_B_TOPICS], ENTITY_GROUND_SIZE, PART_B_MAX_ENT)
        ev = _draw_subset(rng, ev_cores[i % PART_B_TOPICS], EVIDENCE_GROUND_SIZE, PART_B_MAX_EV)
        ent_masks.append(_set_to_mask(ent))
        ev_masks.append(_set_to_mask(ev))
    return ent_masks, ev_masks


def dual_channel_value(ent_union: int, ev_union: int) -> float:
    return W1_ENTITY * _popcount(ent_union) + W2_EVIDENCE * _popcount(ev_union)


def greedy_marginal_curve(ent_masks: list[int], ev_masks: list[int]) -> list[float]:
    """Dynamic greedy over ALL n proposals; returns the marginal VOI per selection position.

    Each step adds the maximum marginal-VOI proposal (ties -> lowest index), so the
    selected sequence is in descending marginal-VOI order by construction. For a
    submodular impact this curve is provably non-increasing.
    """
    n = len(ent_masks)
    ent_union = 0
    ev_union = 0
    remaining = list(range(n))
    curve: list[float] = []
    for _ in range(n):
        base = dual_channel_value(ent_union, ev_union)
        best_idx = -1
        best_gain = -1.0
        best_ent = ent_union
        best_ev = ev_union
        for idx in remaining:
            u_ent = ent_union | ent_masks[idx]
            u_ev = ev_union | ev_masks[idx]
            gain = dual_channel_value(u_ent, u_ev) - base
            if gain > best_gain:
                best_gain, best_idx, best_ent, best_ev = gain, idx, u_ent, u_ev
        if best_idx < 0:
            raise RuntimeError("greedy found no candidate although the pool is non-empty")
        curve.append(best_gain)
        ent_union, ev_union = best_ent, best_ev
        remaining.remove(best_idx)
    return curve


def run_part_b(rng: random.Random, n: int, instances: int) -> dict:
    curves: list[list[float]] = []
    for _ in range(instances):
        ent_masks, ev_masks = make_part_b_instance(rng, n)
        curves.append(greedy_marginal_curve(ent_masks, ev_masks))

    mean_curve = [statistics.fmean(curve[i] for curve in curves) for i in range(n)]
    median_curve = [statistics.median(curve[i] for curve in curves) for i in range(n)]

    strict_positions = count_increases(mean_curve)
    tol_positions = count_increases(mean_curve, tol=MEAN_CURVE_TOL)

    per_instance_counts = [len(count_increases(curve)) for curve in curves]
    per_position_counts = [0] * (n - 1)
    for curve in curves:
        for i in count_increases(curve):
            per_position_counts[i] += 1
    histogram = dict(sorted(Counter(per_instance_counts).items()))
    with_any = sum(1 for c in per_instance_counts if c > 0)

    return {
        "instances": instances,
        "proposals_per_instance": n,
        "positions": n,
        "greedy_procedure": (
            "dynamic greedy over all n proposals: each step adds the maximum marginal-VOI proposal "
            "(ties -> lowest index), so the selection is in descending marginal-VOI order"
        ),
        "mean_marginal_gain_curve": mean_curve,
        "median_marginal_gain_curve": median_curve,
        "mean_curve_nonincreasing_violations": {
            "strict_increase_count": len(strict_positions),
            "strict_increase_positions": strict_positions,
            "increase_count_with_tolerance": len(tol_positions),
            "tolerance": MEAN_CURVE_TOL,
        },
        "per_instance_violations": {
            "counts_min": min(per_instance_counts),
            "counts_median": statistics.median(per_instance_counts),
            "counts_mean": statistics.fmean(per_instance_counts),
            "counts_max": max(per_instance_counts),
            "instances_with_any_violation": {
                "count": with_any,
                "total": instances,
                "fraction": with_any / instances,
            },
            "histogram_violation_count_to_instances": {str(key): value for key, value in histogram.items()},
            "per_position_increase_counts": per_position_counts,
        },
        "interpretation": (
            "For a submodular impact the greedy marginal-gain sequence is provably non-increasing, so any "
            "increase is direct evidence against (empirical) submodularity. The simulated dual-channel "
            "function is coverage-type (nonnegative weighted sum of two coverage functions), hence also "
            "submodular in theory: zero violations validate the measurement pipeline and the empirical "
            "diminishing-returns shape, but do NOT certify the production impact function, whose "
            "non-coverage terms are not simulated here."
        ),
    }


# --------------------------------------------------------------------------
# CLI, assembly, output.
# --------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="probe_p4_submodularity.py",
        description=(
            "Empirical submodularity probe: (A) greedy vs exhaustive optimum on synthetic coverage "
            "instances; (B) diminishing-returns curve of a dual-channel impact function."
        ),
    )
    parser.add_argument("--seed", type=int, default=20260919,
                    help="Master RNG seed (fixed default for reproducibility).")
    parser.add_argument(
        "--instances",
        type=int,
        default=400,
        help="Random instances per part (pre-registered minimum: 300 for part A, 50 for part B).",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=14,
        help="Proposals per instance, 2..20 (<= 20 keeps exhaustive enumeration feasible).",
    )
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT, help="Path of the output JSON file.")
    args = parser.parse_args(argv)
    if not 2 <= args.n <= MAX_N_EXHAUSTIVE:
        parser.error(f"--n must be in [2, {MAX_N_EXHAUSTIVE}] so exhaustive enumeration stays feasible (got {args.n})")
    if args.instances < 1:
        parser.error("--instances must be >= 1")
    if args.instances < 300:
        print(
            f"WARNING: --instances={args.instances} is below the pre-registered minimum of 300 for part A.",
            file=sys.stderr,
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    n = args.n
    k = max(1, n // 4)
    rng_a = random.Random(args.seed)
    rng_b = random.Random(args.seed + SEED_OFFSET_PART_B)

    part_a = run_part_a(rng_a, n, k, args.instances)
    part_b = run_part_b(rng_b, n, args.instances)

    mean_ratio = part_a["ratio_stats"]["mean"]
    conclusion = (
        f"合成覆盖型设定下贪心达到最优的 {mean_ratio * 100:.1f}%，支持工程使用；"
        "真实函数的子模性声明保持'近似/经验成立'的诚实措辞。"
    )

    result = {
        "probe": "p4_submodularity",
        "purpose": (
            "Empirical evidence for the plan section 3.1 claim: coverage-type impact is submodular so "
            "greedy carries the (1 - 1/e) approximation guarantee; the real impact function contains "
            "non-coverage terms, so only an approximate/empirical statement is made for it."
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "config": {
            "seed": args.seed,
            "part_a_seed": args.seed,
            "part_b_seed": args.seed + SEED_OFFSET_PART_B,
            "n_proposals": n,
            "budget_k": k,
            "instances": args.instances,
            "ground_set_size_part_a": GROUND_SIZE_PART_A,
            "cover_prob_part_a": COVER_PROB_PART_A,
            "w1_entity_weight": W1_ENTITY,
            "w2_evidence_weight": W2_EVIDENCE,
            "entity_ground_set_size": ENTITY_GROUND_SIZE,
            "evidence_ground_set_size": EVIDENCE_GROUND_SIZE,
            "part_b_topics": PART_B_TOPICS,
            "max_n_exhaustive": MAX_N_EXHAUSTIVE,
            "greedy_tie_break": "deterministic stable ordering: lowest proposal index wins ties (exact arithmetic)",
            "optimum_method_part_a": "exhaustive brute force over ALL subsets of size <= k",
            "python_version": platform.python_version(),
        },
        "part_a": part_a,
        "part_b": part_b,
        "honesty_notes": [
            "覆盖型假设是理想化假设，真实函数只验证经验单调性，不声称严格子模性保证。",
            "Part A 的最优解是对全部 size<=k 子集的真实穷举（ brute force ），未用任何近似算法冒充最优。",
            "Part B 模拟的双通道 impact 本身仍是覆盖型函数（两通道非负加权并集），真实 impact 中的非覆盖项未被"
            "模拟；Part B 验证的是测量流程与经验边际递减形状，不能外推为真实函数的子模性证明。",
            "比例类指标均同时报告 count 与分母（实例数）；f(opt)=0 的退化实例单独计数，不静默丢弃；异常不吞。",
        ],
        "conclusion": conclusion,
        "scope_statement": "适用范围：合成条件下的机制验证，非真实数据结论。",
    }

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    stats = part_a["ratio_stats"]
    opt = part_a["greedy_optimal_ratio_eq_1"]
    ge = part_a["ratio_ge_one_minus_one_over_e"]
    bviol = part_b["per_instance_violations"]
    bmean = part_b["mean_curve_nonincreasing_violations"]
    print("=" * 78)
    print("Probe P4: empirical submodularity check (plan section 3.1)")
    print(f"  config: n={n}, k={k}, instances={args.instances}, seed={args.seed}")
    print("-" * 78)
    print("Part A - synthetic coverage, greedy vs EXHAUSTIVE optimum:")
    print(
        f"  ratio f(greedy)/f(opt): min={stats['min']:.4f} p10={stats['p10']:.4f} "
        f"median={stats['median']:.4f} mean={stats['mean']:.4f} max={stats['max']:.4f}"
    )
    print(f"  greedy == optimal (ratio==1): {opt['count']}/{opt['total']} ({opt['fraction']:.1%})")
    print(f"  ratio >= (1-1/e)={ge['threshold']:.4f}: {ge['count']}/{ge['total']}")
    print(f"  exhaustive subsets evaluated: {part_a['subsets_enumerated_total']}")
    if ge["count"] < ge["total"]:
        print("  WARNING: some ratio is below (1-1/e); this must not happen for coverage - investigate.")
    if part_a["degenerate_zero_optimum_instances"]:
        print(f"  degenerate zero-optimum instances: {part_a['degenerate_zero_optimum_instances']}")
    print("-" * 78)
    print("Part B - dual-channel impact (entities + evidence), greedy marginal-VOI curve:")
    print("  mean curve : " + " ".join(f"{v:.2f}" for v in part_b["mean_marginal_gain_curve"]))
    print("  median     : " + " ".join(f"{v:.2f}" for v in part_b["median_marginal_gain_curve"]))
    print(
        f"  mean-curve non-increase violations: strict={bmean['strict_increase_count']} "
        f"(with tol {MEAN_CURVE_TOL:g}: {bmean['increase_count_with_tolerance']})"
    )
    any_v = bviol['instances_with_any_violation']
    print(
        f"  per-instance violations: mean={bviol['counts_mean']:.3f} max={bviol['counts_max']} "
        f"instances_with_any={any_v['count']}/{any_v['total']}"
    )
    print("-" * 78)
    print(f"Conclusion: {conclusion}")
    print(f"JSON written to: {args.output}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
