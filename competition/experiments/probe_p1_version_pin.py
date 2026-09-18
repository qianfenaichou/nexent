#!/usr/bin/env python3
"""P1 probe: version-pinned traversal vs un-versioned retrieval on version-sensitive facts.

Synthesizes a 4-version temporal fact graph, one fact per (question, version) pair:
- Version-sensitive questions ("V questions", 20% of the pool, index % 5 == 0) flip their
  conclusion at every version boundary; each version's conclusion is valid only inside that
  version's time window, and the windows are disjoint.
- Stable questions ("F questions") keep a single conclusion across all versions.

Each of the 200 questions is asked under ALL 4 version time points (800 question-version
pairs per arm). Three arms are compared on the same pool:
- pinned:     answer taken from the as-of-version sub-graph only (facts filtered by the
              version time predicate valid_from <= t_v <= valid_to before answering);
- ret-random: answer taken from un-versioned retrieval, where any version's fact may surface;
- ret-latest: answer taken from recency-biased retrieval, which always returns the newest
              conclusion even when the question asks as-of an older version.

Reported per arm: accuracy on V questions, accuracy on F questions, and overall accuracy,
each with its n_ok / n_total denominator.

Run: python3 competition/experiments/probe_p1_version_pin.py [--seed N] [--output PATH]

适用范围：合成条件下的机制验证，非真实数据结论。
"""
from __future__ import annotations

import argparse
import json
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

SCOPE_NOTE = "适用范围：合成条件下的机制验证，非真实数据结论。"

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPETITION_DIR = os.path.dirname(EXPERIMENTS_DIR)
DEFAULT_OUTPUT = os.path.join(
    COMPETITION_DIR, "deliverables", "algorithm-probes", "probe_p1_version_pin.json"
)

DEFAULT_SEED = 20260918
N_QUESTIONS = 200
N_VERSIONS = 4
SENSITIVE_MODULUS = 5  # question index % 5 == 0 -> version-sensitive (20% of the pool)
WINDOW_DAYS = 365      # per-version validity window length; windows are disjoint
WINDOW_START = datetime(2020, 1, 1)


@dataclass(frozen=True)
class Fact:
    """One (question, conclusion) fact, valid on the closed interval [valid_from, valid_to]."""

    question: str
    conclusion: str
    valid_from: datetime  # inclusive
    valid_to: datetime    # inclusive


def build_graph() -> tuple[list[Fact], set[str]]:
    """Build the synthetic temporal graph. Fully deterministic (no randomness involved)."""
    facts: list[Fact] = []
    sensitive: set[str] = set()
    for i in range(N_QUESTIONS):
        q = f"q{i}"
        is_sensitive = i % SENSITIVE_MODULUS == 0
        if is_sensitive:
            sensitive.add(q)
        for v in range(N_VERSIONS):
            lo = WINDOW_START + timedelta(days=WINDOW_DAYS * v)
            hi = WINDOW_START + timedelta(days=WINDOW_DAYS * (v + 1) - 1)
            conclusion = f"rec_{i}_v{v}" if is_sensitive else f"rec_{i}_stable"
            facts.append(Fact(q, conclusion, lo, hi))
    return facts, sensitive


def facts_asof(facts: list[Fact], t: datetime) -> list[Fact]:
    """Version time predicate: keep only facts valid at time t (the pinned-view filter)."""
    return [f for f in facts if f.valid_from <= t <= f.valid_to]


def pinned_answer(asof_facts: list[Fact], q: str) -> str | None:
    """Answer from the as-of-version sub-graph only (version-pinned traversal)."""
    for f in asof_facts:
        if f.question == q:
            return f.conclusion
    return None


def retrieve_random(by_question: dict[str, list[Fact]], q: str, rng: random.Random) -> str | None:
    """Un-versioned retrieval: any version's fact may surface."""
    candidates = by_question.get(q, [])
    return rng.choice(candidates).conclusion if candidates else None


def retrieve_latest(by_question: dict[str, list[Fact]], q: str) -> str | None:
    """Recency-biased retrieval: always the newest conclusion, regardless of asked version."""
    candidates = by_question.get(q, [])
    return max(candidates, key=lambda f: f.valid_from).conclusion if candidates else None


def run_probe(seed: int) -> dict:
    rng = random.Random(seed)
    facts, sensitive = build_graph()
    by_question: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        by_question[f.question].append(f)
    version_points = [datetime(2020 + v, 6, 1) for v in range(N_VERSIONS)]
    arms = ("pinned", "ret-random", "ret-latest")
    # Per arm: correct/total counts, split by V (version-sensitive) vs F (stable) and by version.
    stats = {
        a: {"v_ok": [0] * N_VERSIONS, "v_total": [0] * N_VERSIONS,
            "f_ok": [0] * N_VERSIONS, "f_total": [0] * N_VERSIONS}
        for a in arms
    }
    for i in range(N_QUESTIONS):
        q = f"q{i}"
        for v, t in enumerate(version_points):
            asof = facts_asof(facts, t)
            truth = {f.conclusion for f in asof if f.question == q}
            answers = {
                "pinned": pinned_answer(asof, q),
                "ret-random": retrieve_random(by_question, q, rng),
                "ret-latest": retrieve_latest(by_question, q),
            }
            for arm, ans in answers.items():
                ok = ans in truth
                kind_total = "v_total" if q in sensitive else "f_total"
                kind_ok = "v_ok" if q in sensitive else "f_ok"
                stats[arm][kind_total][v] += 1
                if ok:
                    stats[arm][kind_ok][v] += 1

    def arm_metrics(s: dict) -> dict:
        v_ok, v_total = sum(s["v_ok"]), sum(s["v_total"])
        f_ok, f_total = sum(s["f_ok"]), sum(s["f_total"])
        return {
            "v_accuracy": {"n_ok": v_ok, "n_total": v_total, "acc": v_ok / v_total},
            "f_accuracy": {"n_ok": f_ok, "n_total": f_total, "acc": f_ok / f_total},
            "overall_accuracy": {
                "n_ok": v_ok + f_ok,
                "n_total": v_total + f_total,
                "acc": (v_ok + f_ok) / (v_total + f_total),
            },
            "v_ok_by_version": list(s["v_ok"]),
            "v_total_by_version": list(s["v_total"]),
            "f_ok_by_version": list(s["f_ok"]),
            "f_total_by_version": list(s["f_total"]),
        }

    return {
        "probe": "p1_version_pin",
        "question": (
            "Does version-pinned traversal answer version-sensitive questions correctly where "
            "un-versioned retrieval fails, without flipping stable conclusions (F regression guard)?"
        ),
        "config": {
            "seed": seed,
            "n_questions": N_QUESTIONS,
            "n_versions": N_VERSIONS,
            "sensitive_rule": f"question index % {SENSITIVE_MODULUS} == 0 (20% of pool are V questions)",
            "version_window_days": WINDOW_DAYS,
            "version_window_start": WINDOW_START.date().isoformat(),
            "version_query_points": [t.date().isoformat() for t in version_points],
            "arm_definitions": {
                "pinned": "answer from as-of-version sub-graph (valid_from <= t_v <= valid_to)",
                "ret-random": "answer from un-versioned retrieval; any version's fact may surface",
                "ret-latest": "answer from recency-biased retrieval; always the newest conclusion",
            },
        },
        "results": {
            "n_questions": N_QUESTIONS,
            "n_version_sensitive_questions": len(sensitive),
            "n_stable_questions": N_QUESTIONS - len(sensitive),
            "question_version_pairs_per_arm": N_QUESTIONS * N_VERSIONS,
            "arms": {a: arm_metrics(stats[a]) for a in arms},
            "reading_notes": [
                "pinned is correct on V questions by construction of the version predicate;",
                "ret-latest is correct on V questions only when asked at the newest version point",
                "(see v_ok_by_version: correct only in the last slot); ret-random succeeds only by",
                "chance (~1/n_versions). F accuracy stays 1.0 for all arms: pinning does not flip",
                "stable conclusions (regression guard).",
            ],
            "scope_note": SCOPE_NOTE,
        },
    }


def print_table(payload: dict) -> None:
    results = payload["results"]
    cfg = payload["config"]
    print(f"== P1: version-pinned walk vs unpinned retrieval "
          f"({results['n_questions']} questions x {cfg['n_versions']} versions) ==")
    print(f"  {'arm':11s} | {'V accuracy':20s} | {'F accuracy':20s} | overall")
    for arm, m in results["arms"].items():
        vm, fm, om = m["v_accuracy"], m["f_accuracy"], m["overall_accuracy"]
        v_s = f"{vm['acc']:.3f} ({vm['n_ok']}/{vm['n_total']})"
        f_s = f"{fm['acc']:.3f} ({fm['n_ok']}/{fm['n_total']})"
        o_s = f"{om['acc']:.3f} ({om['n_ok']}/{om['n_total']})"
        print(f"  {arm:11s} | {v_s:20s} | {f_s:20s} | {o_s}")
    print(f"  scope: {SCOPE_NOTE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P1 probe: version-pinned traversal vs un-versioned retrieval (synthetic)."
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
