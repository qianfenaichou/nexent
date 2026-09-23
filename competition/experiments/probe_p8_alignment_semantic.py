#!/usr/bin/env python3
"""P8 probe: semantic alignment clustering with FDR-controlled false positives.

Companion to ``probe_p5_alignment_caliber.py``. P5 *audits* the T-21 token-overlap
caliber; P8 *replaces* it and reports the replacement's numbers side by side with
the caliber it supersedes.

What it measures
----------------
1. **Legacy sensitivity grid** -- re-runs the production
   ``Engine.topic_level(max_df_fraction, min_shared_tokens)`` over the 10-point
   grid P5 already uses, to reproduce the documented cliff
   (``min_shared_tokens`` 2 -> 3 collapses recall 9/9 -> 3/9) on this machine.
2. **Semantic calibration** -- the new pipeline in
   ``services.knowevo.alignment_semantic``: provenance-cleaned group tokens,
   idf-weighted cosine, optimal bipartite assignment with per-topic capacity,
   Monte-Carlo randomisation p-values, Benjamini-Hochberg FDR, and a
   negative-control (decoy) arm that yields an explicit false-positive count.
3. **Threshold-sensitivity contrast** -- the *same* 10-point legacy grid, run
   through the new pipeline. The legacy knob is accepted but inert by design, so
   the grid must come out flat; that is the point (the cliff is structural, not
   a tuning problem).
4. **The new method's own sensitivity** -- sweep the knobs that *do* carry
   load now (``top_k``, ``fdr_q``, ``capacity``) so the replacement is not
   itself a magic constant.

Outputs the estimand ``alignment_precision_pooled`` (specificity-controlled,
with an explicit false-positive count and a Wilson interval). It is **not**
comparable with T-21's ``64/517`` relatedness rate -- see ``ESTIMAND_NOTE``.

Run (from ``nexent/``, either interpreter works):

    backend/.venv/bin/python competition/experiments/probe_p8_alignment_semantic.py
    backend/.venv/bin/python competition/experiments/probe_p8_alignment_semantic.py \
        --output competition/deliverables/algorithm-probes/probe_p8_alignment_semantic.json

Zero LLM, zero DB, zero network; seed fixed. Deterministic given the seed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPETITION_DIR = os.path.dirname(EXPERIMENTS_DIR)
REPO_ROOT = os.path.dirname(COMPETITION_DIR)
if EXPERIMENTS_DIR not in sys.path:
    sys.path.insert(0, EXPERIMENTS_DIR)

import probe_p5_alignment_caliber as p5

DEFAULT_SEED = 20260923
# The 10-point grid P5 uses, so the contrast is apples-to-apples.
LEGACY_GRID = [(df, ms) for df in (0.02, 0.05, 0.10, 0.20, 1.00) for ms in (2, 3)]
# Cheaper permutation budget for grid rows; the headline result uses N_PERM.
N_PERM_GRID = 200
N_PERM = 1000

# In-domain HARD negatives: real diabetes-guideline section topics that the seed
# does not claim as changes. They share the ubiquitous trigram "糖尿病" with
# genuine groups, so unlike the off-domain decoys they can actually fire and
# expose over-matching. Chosen to avoid every verified *and* unverified gold
# anchor in corpus/guideline_diff_seed.md.
HARD_NEGATIVES = [
    "糖尿病教育", "医学营养治疗", "运动治疗", "戒烟", "血压控制",
    "血脂管理", "糖尿病足病", "糖尿病视网膜病变", "糖尿病神经病变",
    "糖尿病酮症酸中毒", "糖尿病急性并发症", "糖尿病慢性并发症",
]

ESTIMAND_NOTE = (
    "alignment_precision_pooled 是**含假阳计数的特异性受控对齐质量点估计**"
    "（分母 = 本次声明的对齐组，假阳由阴性对照臂实测）。"
    "它与 T-21 的 64/517 **不可直接比较**：后者是相关率（分母 = 全体机器组，无假阳计数）。"
    "两者 estimand 不同。"
)


def _sem(machine, gold, *, n_perm, seed, **kw):
    from services.knowevo import alignment_semantic as sem

    return sem.semantic_calibrate(
        machine, gold, n_perm=n_perm, seed=seed,
        decoy_topics=p5.NEGATIVE_TOPICS,
        hard_negative_topics=HARD_NEGATIVES,
        **kw,
    )


def run_probe(run_path: str, gold_path: str, seed: int, engine) -> dict:
    artifact = p5.load_artifact(run_path)
    machine = artifact["changes"]
    gold = p5.load_gold(gold_path, engine)

    eligible = [
        r for r in gold
        if str(r.get("status", "")).lower() in ("verified", "corrected")
    ]

    # (1) legacy grid -- reproduce the documented cliff
    legacy_grid = []
    for df, ms in LEGACY_GRID:
        cal = engine.topic_level(
            machine, gold, max_df_fraction=df, min_shared_tokens=ms
        )
        legacy_grid.append({
            "max_df_fraction": df,
            "min_shared_tokens": ms,
            "recall_matched": cal["matched_topics"],
            "recall_total": cal["gold_total"],
            "recall": cal["recall"],
            "precision_lower_bound": cal["precision_lower_bound"],
            "matched_groups": cal["matched_groups"],
            "machine_groups": cal["machine_groups"],
        })

    # (2) headline semantic result
    headline, decoy, hard = _sem(machine, eligible, n_perm=N_PERM, seed=seed)
    headline_payload = {
        "recall_matched": headline.matched_topics,
        "recall_total": headline.gold_total,
        "recall": headline.recall,
        "declared_groups": headline.declared_groups,
        "machine_groups": headline.machine_groups,
        "false_positives": headline.false_positives,
        "false_positives_easy": headline.false_positives_easy,
        "alignment_precision_pooled": headline.alignment_precision_pooled,
        "precision_identifiable": headline.precision_identifiable,
        "precision_caveat": headline.precision_caveat,
        "hard_negative_slot_rate": headline.hard_negative_slot_rate,
        "wilson_95": [headline.wilson_low, headline.wilson_high],
        "pairs_tested": headline.pairs_tested,
        "pairs_rejected": headline.pairs_rejected,
        "params": {
            "capacity": headline.capacity, "top_k": headline.top_k,
            "fdr_q": headline.fdr_q, "n_perm": headline.n_perm, "seed": headline.seed,
        },
        "topic_groups": headline.topic_groups,
    }

    # (3) the SAME legacy grid through the NEW pipeline -> must be flat
    semantic_grid = []
    for df, ms in LEGACY_GRID:
        cal, _, _ = _sem(
            machine, eligible, n_perm=N_PERM_GRID, seed=seed,
            max_df_fraction=df, min_shared_tokens=ms,
        )
        semantic_grid.append({
            "max_df_fraction": df,
            "min_shared_tokens": ms,
            "recall_matched": cal.matched_topics,
            "recall": cal.recall,
            "declared_groups": cal.declared_groups,
            "false_positives": cal.false_positives,
            "alignment_precision_pooled": cal.alignment_precision_pooled,
        })

    # (4) the replacement's own knobs
    own_sweep = {"top_k": [], "fdr_q": [], "capacity": []}
    for k in (5, 10, 15, 25, 40):
        cal, _, _ = _sem(machine, eligible, n_perm=N_PERM_GRID, seed=seed, top_k=k)
        own_sweep["top_k"].append({
            "top_k": k, "recall_matched": cal.matched_topics,
            "declared_groups": cal.declared_groups,
            "false_positives": cal.false_positives,
            "alignment_precision_pooled": cal.alignment_precision_pooled,
        })
    for q in (0.01, 0.05, 0.10, 0.20, 0.50):
        cal, _, _ = _sem(machine, eligible, n_perm=N_PERM_GRID, seed=seed, fdr_q=q)
        own_sweep["fdr_q"].append({
            "fdr_q": q, "recall_matched": cal.matched_topics,
            "declared_groups": cal.declared_groups,
            "false_positives": cal.false_positives,
            "alignment_precision_pooled": cal.alignment_precision_pooled,
        })
    for c in (1, 2, 3, 5):
        cal, _, _ = _sem(machine, eligible, n_perm=N_PERM_GRID, seed=seed, capacity=c)
        own_sweep["capacity"].append({
            "capacity": c, "recall_matched": cal.matched_topics,
            "declared_groups": cal.declared_groups,
            "false_positives": cal.false_positives,
            "alignment_precision_pooled": cal.alignment_precision_pooled,
        })

    legacy_recalls = [r["recall_matched"] for r in legacy_grid]
    sem_recalls = [r["recall_matched"] for r in semantic_grid]
    total = len(eligible) or 1

    return {
        "probe": "p8_alignment_semantic",
        "purpose": (
            "Replace the min_shared_tokens token-overlap matcher with a "
            "semantically-clustered, FDR-controlled alignment and report the "
            "replacement's recall / alignment_precision_pooled / false positives "
            "/ Wilson interval, plus a threshold-sensitivity contrast."
        ),
        "engine": engine.kind,
        "engine_path": engine.path_note,
        "inputs": {"run": run_path, "gold": gold_path},
        "seed": seed,
        "gold_eligible_topics": len(eligible),
        "negative_control_topics": len(p5.NEGATIVE_TOPICS),
        "legacy_grid": legacy_grid,
        "semantic_headline": headline_payload,
        "semantic_grid_on_legacy_params": semantic_grid,
        "semantic_own_sweep": own_sweep,
        "decoy_arm": {
            "n_decoy_topics": decoy.n_decoy_topics,
            "declared_groups": decoy.declared_groups,
            "declared_group_keys": decoy.declared_group_keys,
            "pairs_tested": decoy.pairs_tested,
            "degenerate": decoy.degenerate,
        },
        "hard_negative_arm": {
            "n_hard_topics": hard.n_decoy_topics,
            "declared_groups": hard.declared_groups,
            "declared_group_keys": hard.declared_group_keys,
            "pairs_tested": hard.pairs_tested,
            "degenerate": hard.degenerate,
        },
        "sensitivity_contrast": {
            "recall_span": {
                "legacy_grid": [f"{min(legacy_recalls)}/{total}", f"{max(legacy_recalls)}/{total}"],
                "semantic_grid": [f"{min(sem_recalls)}/{total}", f"{max(sem_recalls)}/{total}"],
            },
            "legacy_recall_range": max(legacy_recalls) - min(legacy_recalls),
            "semantic_recall_range": max(sem_recalls) - min(sem_recalls),
        },
        "estimand_note": ESTIMAND_NOTE,
        "scope": p5.SCOPE_NOTE,
    }


def print_report(payload: dict) -> None:
    print("== P8: semantic alignment clustering + FDR-controlled false positives ==")
    print(f"  engine: {payload['engine']}  ({payload['engine_path']})")
    print(f"  gold eligible topics: {payload['gold_eligible_topics']}"
          f"   decoy topics: {payload['negative_control_topics']}")
    print()

    print("  [A] legacy min_shared_tokens / df grid  (the caliber being replaced)")
    print("      df      min_shared   recall     p_lb        matched_groups/groups")
    for r in payload["legacy_grid"]:
        print(f"      {r['max_df_fraction']:<7} {r['min_shared_tokens']:<12}"
              f" {r['recall_matched']}/{r['recall_total']:<8}"
              f" {r['precision_lower_bound']:<11.6f}"
              f" {r['matched_groups']}/{r['machine_groups']}")
    print()

    h = payload["semantic_headline"]
    print("  [B] semantic calibration (headline)")
    print(f"      recall                     : {h['recall_matched']}/{h['recall_total']}")
    print(f"      declared aligned groups    : {h['declared_groups']}"
          f"  (of {h['machine_groups']} machine groups)")
    print(f"      false positives (measured) : {h['false_positives']}")
    ap = h["alignment_precision_pooled"]
    print(f"      alignment_precision_pooled : "
          f"{'n/a' if ap is None else format(ap, '.6f')}")
    lo, hi = h["wilson_95"]
    print(f"      Wilson 95%                 : "
          f"[{'n/a' if lo is None else format(lo, '.4f')}, "
          f"{'n/a' if hi is None else format(hi, '.4f')}]")
    print(f"      pairs tested / rejected    : {h['pairs_tested']} / {h['pairs_rejected']}")
    print(f"      params                     : {h['params']}")
    print()

    print("  [C] the SAME legacy grid through the NEW pipeline (must be flat)")
    for r in payload["semantic_grid_on_legacy_params"]:
        ap = r["alignment_precision_pooled"]
        print(f"      df={r['max_df_fraction']:<6} min_shared={r['min_shared_tokens']:<3}"
              f" recall={r['recall_matched']}/{payload['gold_eligible_topics']:<7}"
              f" declared={r['declared_groups']:<6} fp={r['false_positives']:<4}"
              f" ap_pooled={'n/a' if ap is None else format(ap, '.6f')}")
    print()

    print("  [D] the replacement's own knobs")
    for knob, rows in payload["semantic_own_sweep"].items():
        for r in rows:
            ap = r["alignment_precision_pooled"]
            print(f"      {knob}={r[knob]:<6} recall={r['recall_matched']}"
                  f"/{payload['gold_eligible_topics']:<3} declared={r['declared_groups']:<6}"
                  f" fp={r['false_positives']:<4}"
                  f" ap_pooled={'n/a' if ap is None else format(ap, '.6f')}")
        print()

    sc = payload["sensitivity_contrast"]
    print("  [E] sensitivity contrast")
    print(f"      legacy recall span   : {sc['recall_span']['legacy_grid']}")
    print(f"      semantic recall span : {sc['recall_span']['semantic_grid']}")
    print()

    print("  [F] negative-control arms")
    d = payload["decoy_arm"]
    print(f"      EASY (off-domain)  topics={d['n_decoy_topics']:<3}"
          f" pairs_tested={d['pairs_tested']:<5} declared={d['declared_groups']:<3}"
          f" degenerate={d['degenerate']}")
    h = payload["hard_negative_arm"]
    print(f"      HARD (in-domain)   topics={h['n_hard_topics']:<3}"
          f" pairs_tested={h['pairs_tested']:<5} declared={h['declared_groups']:<3}"
          f" degenerate={h['degenerate']}")
    if d["degenerate"]:
        print("      ⚠️  the EASY arm never produced a testable pair -> it has NO power;")
        print("          its zero must not be quoted as evidence of specificity.")
    print(f"      false positives: hard={payload['semantic_headline']['false_positives']}"
          f"  easy={payload['semantic_headline']['false_positives_easy']}")
    hsr = payload["semantic_headline"]["hard_negative_slot_rate"]
    print(f"      HARD arm slot-fill rate: "
          f"{'n/a' if hsr is None else format(hsr, '.3f')}"
          f"   (declared / (topics x capacity))")
    print(f"      ⚠️  precision_identifiable = "
          f"{payload['semantic_headline']['precision_identifiable']}"
          f" -> the precision axis is insufficient_data on this corpus")
    print()

    print(f"  estimand: {payload['estimand_note']}")
    print()
    print(f"  scope: {payload['scope']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P8 probe: semantic alignment clustering + BH-FDR (zero LLM)."
    )
    parser.add_argument("--run", default=p5.DEFAULT_RUN)
    parser.add_argument("--gold", default=p5.DEFAULT_GOLD)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", default=None,
                        help="optional JSON output path (default: stdout only)")
    args = parser.parse_args()

    engine = p5.Engine()
    payload = run_probe(
        os.path.abspath(args.run), os.path.abspath(args.gold), args.seed, engine
    )
    print_report(payload)
    if args.output:
        out = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"[written] {out}")


if __name__ == "__main__":
    main()
