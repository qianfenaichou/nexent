#!/usr/bin/env python3
"""P3 probe: impact-scope propagation via a precomputed two-layer inverted index.

Synthesizes the index chain of the standard-evolution aligner's impact-scope analysis
(tech-spec 4.2: changed paragraphs -> KG entities -> decision cards):
- layer 1: doc -> entities. Each of 80 docs cites 5..40 of 2000 entities (drawn with
  replacement), mirroring evidence-span anchoring.
- layer 2: entity -> decision cards. Each of 3000 cards cites 1..6 entities (drawn with
  replacement), mirroring evidence_chain kg_path references.

8 of 80 docs (10%) change. The affected entity set E_aff and affected decision-card set D_aff
come from two set-union index queries (no online graph traversal):
  E_aff = union(ent_of_doc[d] for d in changed docs)
  D_aff = union(cards_of_ent[e] for e in E_aff)

Reported: |E_aff| over n_entities and |D_aff| over n_cards (both with denominators), plus the
propagation wall-clock time in ms (mean/min/max over repeated timed runs after one warmup;
the counts themselves are deterministic given the seed).

Run: python3 competition/experiments/probe_p3_impact_prop.py [--seed N] [--output PATH]

适用范围：合成条件下的机制验证，非真实数据结论。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import defaultdict

SCOPE_NOTE = "适用范围：合成条件下的机制验证，非真实数据结论。"

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPETITION_DIR = os.path.dirname(EXPERIMENTS_DIR)
DEFAULT_OUTPUT = os.path.join(
    COMPETITION_DIR, "deliverables", "algorithm-probes", "probe_p3_impact_prop.json"
)

DEFAULT_SEED = 11
N_DOCS = 80
N_ENTITIES = 2000
N_CARDS = 3000
DOC_ENT_MIN, DOC_ENT_MAX = 5, 40   # entities cited per doc (uniform, inclusive)
CARD_ENT_MIN, CARD_ENT_MAX = 1, 6  # entities cited per decision card (uniform, inclusive)
N_CHANGED_DOCS = 8                 # 10% of docs change
TIMING_REPS = 50


def build_index(rng: random.Random) -> tuple[dict[str, list[int]], dict[int, list[int]], int, int]:
    """Build both inverted layers; also report the raw link counts (denominator context)."""
    ent_of_doc: dict[str, list[int]] = defaultdict(list)
    for d in range(N_DOCS):
        for _ in range(rng.randint(DOC_ENT_MIN, DOC_ENT_MAX)):
            ent_of_doc[f"doc{d}"].append(rng.randrange(N_ENTITIES))
    cards_of_ent: dict[int, list[int]] = defaultdict(list)
    for c in range(N_CARDS):
        for _ in range(rng.randint(CARD_ENT_MIN, CARD_ENT_MAX)):
            cards_of_ent[rng.randrange(N_ENTITIES)].append(c)
    n_doc_links = sum(len(v) for v in ent_of_doc.values())
    n_card_links = sum(len(v) for v in cards_of_ent.values())
    return ent_of_doc, cards_of_ent, n_doc_links, n_card_links


def propagate(ent_of_doc: dict, cards_of_ent: dict, changed_docs: list[str]) -> tuple[set[int], set[int]]:
    """Two index queries (no online graph traversal): docs -> entities -> decision cards."""
    ents = {e for d in changed_docs for e in ent_of_doc[d]}
    cards = {c for e in ents for c in cards_of_ent[e]}
    return ents, cards


def run_probe(seed: int) -> dict:
    rng = random.Random(seed)
    ent_of_doc, cards_of_ent, n_doc_links, n_card_links = build_index(rng)
    changed_docs = [f"doc{d}" for d in range(N_CHANGED_DOCS)]

    ents, cards = propagate(ent_of_doc, cards_of_ent, changed_docs)  # warmup + authoritative counts
    times_ms = []
    for _ in range(TIMING_REPS):
        t0 = time.perf_counter()
        propagate(ent_of_doc, cards_of_ent, changed_docs)
        times_ms.append((time.perf_counter() - t0) * 1000.0)

    return {
        "probe": "p3_impact_prop",
        "question": (
            "Can the impact scope of a document change (affected entities, affected decision "
            "cards) be obtained by precomputed index queries only, fast enough for interactive use?"
        ),
        "config": {
            "seed": seed,
            "n_docs": N_DOCS,
            "n_entities": N_ENTITIES,
            "n_cards": N_CARDS,
            "doc_entity_range": [DOC_ENT_MIN, DOC_ENT_MAX],
            "card_entity_range": [CARD_ENT_MIN, CARD_ENT_MAX],
            "n_changed_docs": N_CHANGED_DOCS,
            "changed_doc_selection": f"first {N_CHANGED_DOCS} doc ids (deterministic, 10% of docs)",
            "timing_reps": TIMING_REPS,
        },
        "results": {
            "changed_docs": {
                "count": N_CHANGED_DOCS, "n_total": N_DOCS, "fraction": N_CHANGED_DOCS / N_DOCS,
            },
            "affected_entities": {
                "count": len(ents), "n_total": N_ENTITIES, "fraction": len(ents) / N_ENTITIES,
            },
            "affected_decision_cards": {
                "count": len(cards), "n_total": N_CARDS, "fraction": len(cards) / N_CARDS,
            },
            "propagation_ms": {
                "mean": sum(times_ms) / len(times_ms),
                "min": min(times_ms),
                "max": max(times_ms),
                "n_timed_runs": TIMING_REPS,
                "method": (
                    "time.perf_counter around the full two-layer propagation (set-union index "
                    "queries), after 1 warmup run; counts are deterministic given the seed"
                ),
            },
            "index_sizes": {"n_doc_entity_links": n_doc_links, "n_entity_card_links": n_card_links},
            "scope_note": SCOPE_NOTE,
        },
    }


def print_table(payload: dict) -> None:
    r = payload["results"]
    print("== P3: impact-scope propagation via precomputed two-layer inverted index ==")
    cd, ae, dc, pm = (r["changed_docs"], r["affected_entities"],
                      r["affected_decision_cards"], r["propagation_ms"])
    print(f"  docs changed:         {cd['count']}/{cd['n_total']} ({cd['fraction']:.1%})")
    print(f"  affected entities:    {ae['count']}/{ae['n_total']} ({ae['fraction']:.2%})")
    print(f"  affected dec. cards:  {dc['count']}/{dc['n_total']} ({dc['fraction']:.2%})")
    print(f"  propagation time:     mean {pm['mean']:.3f} ms "
          f"(min {pm['min']:.3f}, max {pm['max']:.3f}, n={pm['n_timed_runs']})")
    print(f"  scope: {SCOPE_NOTE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P3 probe: impact-scope propagation via precomputed inverted index (synthetic)."
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
