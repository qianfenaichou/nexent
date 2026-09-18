#!/usr/bin/env python3
"""P2 probe: VOI-ordered human review vs confidence-ordered review under a fixed expert budget.

Synthesizes a pool of 120 ontology-change proposals:
- impact       ~ U(1, 30)        (value of a confirmed proposal)
- p_confirm    ~ U(0.2, 0.95)    (estimated probability the expert confirms it)
- true_quality ~ Bernoulli(0.55) (latent ground truth, not observable by the ranker)

Two orderings of the SAME pool are compared under review budgets {10, 20, 30, 50}:
- voi:        proposals sorted by value-of-information p_confirm * impact, descending;
- confidence: proposals sorted by p_confirm alone, descending.

Review model (kept identical to the /tmp reference protocol): walking each arm's ordered list
front to front, a reviewed proposal is accepted with P=0.9 if truly good and P=0.1 otherwise;
acceptance coins are drawn at review time per (budget, arm) evaluation, so each budget row is
an independent trial of that arm's review process. Reported per budget per arm: good / bad
accepted counts over n_reviewed = budget, net_good = good - bad, and
net_good_gain = voi net_good - confidence net_good.

Honesty note: in this pool true_quality is independent of both ranking scores, so the EXPECTED
unweighted net_good difference is exactly 0 and any single-trial gain is sampling noise. A
seed-sensitivity appendix (gain distribution over 20 extra seeds) makes that noise band
visible; the headline numbers validate the mechanism plumbing (ordering decides which
proposals enter the limited review window), not a guaranteed quality gain.

Run: python3 competition/experiments/probe_p2_voi_budget.py [--seed N] [--output PATH]

适用范围：合成条件下的机制验证，非真实数据结论。
"""
from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass

SCOPE_NOTE = "适用范围：合成条件下的机制验证，非真实数据结论。"

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPETITION_DIR = os.path.dirname(EXPERIMENTS_DIR)
DEFAULT_OUTPUT = os.path.join(
    COMPETITION_DIR, "deliverables", "algorithm-probes", "probe_p2_voi_budget.json"
)

DEFAULT_SEED = 7
N_POOL = 120
BUDGETS = (10, 20, 30, 50)
P_ACCEPT_GOOD = 0.9    # P(expert accepts | proposal is truly good)
P_ACCEPT_BAD = 0.1     # P(expert accepts | proposal is not truly good)
P_TRUE_QUALITY = 0.55  # base rate of truly good proposals in the pool
SENSITIVITY_SEEDS = tuple(range(1, 21))  # extra seeds for the gain-noise appendix


@dataclass
class Proposal:
    pid: str
    impact: float
    p_confirm: float
    true_quality: bool


def build_pool(rng: random.Random) -> list[Proposal]:
    """Draw order (impact, p_confirm, true_quality) matches the /tmp reference protocol."""
    return [
        Proposal(f"p{i}", rng.uniform(1, 30), rng.uniform(0.2, 0.95), rng.random() < P_TRUE_QUALITY)
        for i in range(N_POOL)
    ]


def simulate_review(order: list[Proposal], budget: int, rng: random.Random) -> tuple[int, int]:
    """Review the first `budget` proposals in `order`; return (good_accepted, bad_accepted)."""
    good = bad = 0
    for p in order[:budget]:
        if rng.random() < (P_ACCEPT_GOOD if p.true_quality else P_ACCEPT_BAD):
            if p.true_quality:
                good += 1
            else:
                bad += 1
    return good, bad


def run_budgets(seed: int) -> dict:
    rng = random.Random(seed)
    props = build_pool(rng)
    voi = sorted(props, key=lambda p: p.p_confirm * p.impact, reverse=True)
    conf = sorted(props, key=lambda p: p.p_confirm, reverse=True)
    rows = []
    for budget in BUDGETS:
        g1, b1 = simulate_review(voi, budget, rng)
        g2, b2 = simulate_review(conf, budget, rng)
        rows.append({
            "budget": budget,
            "voi": {"good": g1, "bad": b1, "net_good": g1 - b1, "n_reviewed": budget},
            "confidence": {"good": g2, "bad": b2, "net_good": g2 - b2, "n_reviewed": budget},
            "net_good_gain": (g1 - b1) - (g2 - b2),
        })
    n_good = sum(1 for p in props if p.true_quality)
    return {
        "pool": {
            "n_proposals": N_POOL,
            "n_true_quality": n_good,
            "true_quality_rate": {"n_ok": n_good, "n_total": N_POOL, "rate": n_good / N_POOL},
        },
        "rows": rows,
    }


def gain_sensitivity() -> dict:
    """Distribution of net_good_gain over extra seeds: shows the single-trial noise band."""
    per_budget: dict[int, list[int]] = {b: [] for b in BUDGETS}
    for seed in SENSITIVITY_SEEDS:
        for row in run_budgets(seed)["rows"]:
            per_budget[row["budget"]].append(row["net_good_gain"])
    out = {}
    for b, gains in per_budget.items():
        out[str(b)] = {
            "min": min(gains),
            "mean": sum(gains) / len(gains),
            "max": max(gains),
            "n_gain_positive": sum(1 for g in gains if g > 0),
            "n_seeds": len(gains),
        }
    return out


def run_probe(seed: int) -> dict:
    run = run_budgets(seed)
    return {
        "probe": "p2_voi_budget",
        "question": (
            "Under a fixed expert-review budget, does VOI-ordered review confirm more good "
            "proposals than confidence-ordered review on the same proposal pool?"
        ),
        "config": {
            "seed": seed,
            "n_pool": N_POOL,
            "budgets": list(BUDGETS),
            "impact_range": [1, 30],
            "p_confirm_range": [0.2, 0.95],
            "p_true_quality": P_TRUE_QUALITY,
            "p_accept_if_true_quality": P_ACCEPT_GOOD,
            "p_accept_otherwise": P_ACCEPT_BAD,
            "orderings": {
                "voi": "sorted by p_confirm * impact descending",
                "confidence": "sorted by p_confirm descending",
            },
            "review_protocol": (
                "acceptance coins drawn at review time per (budget, arm) evaluation; "
                "arm order per budget: voi then confidence (matches the /tmp reference protocol)"
            ),
        },
        "results": {
            "pool": run["pool"],
            "budgets": run["rows"],
            "net_good_gain_seed_sensitivity": {
                "n_extra_seeds": len(SENSITIVITY_SEEDS),
                "extra_seeds": list(SENSITIVITY_SEEDS),
                "net_good_gain_by_budget": gain_sensitivity(),
                "note": (
                    "true_quality is independent of both ranking scores in this pool model, so the "
                    "expected unweighted net_good_gain is 0; this distribution quantifies the "
                    "single-trial sampling noise around that expectation"
                ),
            },
            "scope_note": SCOPE_NOTE,
        },
    }


def print_table(payload: dict) -> None:
    results = payload["results"]
    print(f"== P2: VOI-ordered review vs confidence-ordered review "
          f"(pool={results['pool']['n_proposals']}, P(accept|good)={P_ACCEPT_GOOD}, "
          f"P(accept|bad)={P_ACCEPT_BAD}) ==")
    print("  budget | VOI good/bad (n)  | CONF good/bad (n) | net-good gain")
    for row in results["budgets"]:
        v, c = row["voi"], row["confidence"]
        print(f"  {row['budget']:6d} | {v['good']:3d}/{v['bad']:<3d} ({v['n_reviewed']:2d})    | "
              f"{c['good']:3d}/{c['bad']:<3d} ({c['n_reviewed']:2d})    | {row['net_good_gain']:+d}")
    sens = results["net_good_gain_seed_sensitivity"]
    print(f"  gain noise band over {sens['n_extra_seeds']} extra seeds (min/mean/max; "
          f"expectation 0 under this pool model):")
    for b in (str(x) for x in BUDGETS):
        s = sens["net_good_gain_by_budget"][b]
        print(f"    budget {b:>2s}: {s['min']:+d} / {s['mean']:+.1f} / {s['max']:+d} "
              f"(positive in {s['n_gain_positive']}/{s['n_seeds']} seeds)")
    print(f"  scope: {SCOPE_NOTE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P2 probe: VOI-ordered review vs confidence-ordered review under fixed budget."
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"random seed (default: {DEFAULT_SEED})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output JSON path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    payload = run_probe(args.seed)
    print_table(payload)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[written] {args.output}")


if __name__ == "__main__":
    main()
