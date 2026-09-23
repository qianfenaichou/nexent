#!/usr/bin/env python3
"""P9 probe: path-validity pruning completeness (属性测试脚手架).

WHY this probe exists (sessionB-receipt §13 #1 = T4): the differentiation
claim of innovation ② is "可学习排序 vs 可证前置约束", and the latter is
supposed to yield a *pruning-completeness proof*: illegal edges never enter
the candidate set  ⟺  no legal path is missed. This probe tests that claim
on **synthetic small graphs** with a FIXED seed, zero DB, zero LLM.

Two independent checks, both machine-rerunnable:

CHECK A — predicate equivalence (Lemma 1 in the proof draft):
    For every edge, the boolean predicate
        valid_at <= t_v AND (invalid_at IS NULL OR invalid_at > t_v)
    must agree with the range-containment predicate
        tstzrange(valid_at, COALESCE(invalid_at, 'infinity'), '[)') @> t_v
    (transcribed in pure Python). On a degenerate row (invalid_at < valid_at)
    the range form RAISES while the boolean form returns False — we record
    that divergence explicitly (it is the honest caveat in the proof draft §5.2.3).

CHECK B — pruning completeness (Theorem 1 in the proof draft):
    Brute force: enumerate ALL simple paths over the FULL edge set, then keep
        those whose every edge satisfies the predicate.  -> reference set R
    Pushdown:    filter edges to E_v first (predicate pushdown), then enumerate
        ALL simple paths over E_v.                          -> candidate set P
    Assert R == P (both directions: no illegal path slips in, no legal path
    is dropped). This isolates the VERSION PREDICATE's completeness from the
    beam/depth caps of the real multi_hop walk (graph_store.py:528-529).

SCOPE NOTE: synthetic-graph mechanism validation of the predicate algebra and
candidate-set equivalence. It does NOT prove PostgreSQL's GiST index returns
exactly E_v at runtime (that needs EXPLAIN; see proof draft §5.2.1).

Run: python3 competition/experiments/probe_p9_path_validity.py [--seed N] [--graphs G] [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SCOPE_NOTE = (
    "适用范围：合成小图上的谓词代数与候选集等价机制验证，非真实库/索引层结论。"
)

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPETITION_DIR = os.path.dirname(EXPERIMENTS_DIR)
DEFAULT_OUTPUT = os.path.join(
    COMPETITION_DIR, "deliverables", "algorithm-probes", "probe_p9_path_validity.json"
)

DEFAULT_SEED = 20260924
DEFAULT_N_GRAPHS = 40
DEFAULT_N_NODES = 14
DEFAULT_N_EDGES = 26
DEFAULT_MAX_DEPTH = 4
DEFAULT_DEGENERATE_PROB = 0.05  # chance an edge is a degenerate (invalid_at < valid_at) row

INFINITY = datetime.max.replace(tzinfo=timezone.utc)  # stand-in for PostgreSQL 'infinity'
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Predicate transcriptions (kept faithful to the code so the test is a real
# cross-check, not a rubber stamp).
# ---------------------------------------------------------------------------
def edge_in_version_bool(valid_at: datetime | None, invalid_at: datetime | None,
                         t_v: datetime) -> bool:
    """Transcription of version_pin.py:160-174 (edge_in_version), boolean form.

    valid_at None means "always existed" (NOT NULL in schema; None only in
    fixtures) -> treated as -infinity (never starts_after). invalid_at None
    means "still valid" (-> never expires_before).
    """
    starts_after = valid_at is not None and valid_at > t_v
    expires_before = invalid_at is not None and invalid_at <= t_v
    return not (starts_after or expires_before)


def edge_in_version_range(valid_at: datetime | None, invalid_at: datetime | None,
                          t_v: datetime) -> bool:
    """Transcription of knowevo_db.py:407-443 (valid_range_contains).

    [valid_at, COALESCE(invalid_at, 'infinity')) @> t_v
    RAISES on a degenerate row (invalid_at < valid_at) — mirrors PostgreSQL's
    "range lower bound must be less than or equal to range upper bound".
    """
    lo = valid_at if valid_at is not None else datetime.min.replace(tzinfo=timezone.utc)
    hi = invalid_at if invalid_at is not None else INFINITY
    if hi < lo:
        raise ValueError("range lower bound must be less than or equal to range upper bound")
    # half-open [lo, hi) contains t_v  <=>  lo <= t_v < hi
    return lo <= t_v < hi


# Best-effort: cross-check our transcription against the LIVE version_pin
# predicate under test. If import fails (heavy DB import chain), we fall back
# to the transcription only and flag it in the output.
_LIVE_PREDICATE = None
try:  # pragma: no cover - environment dependent
    import sys
    # backend lives at nexent/backend, a sibling of nexent/competition
    _backend_dir = os.path.join(os.path.dirname(COMPETITION_DIR), "backend")
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
    from services.knowevo.version_pin import edge_in_version as _LIVE_PREDICATE  # type: ignore  # noqa: I001
except Exception:  # noqa: BLE001 - optional cross-check only
    _LIVE_PREDICATE = None


@dataclass(frozen=True)
class Edge:
    eid: int
    src: str
    dst: str
    rel_type: str
    valid_at: datetime
    invalid_at: datetime | None  # None == still valid


def build_random_graph(rng: random.Random, n_nodes: int, n_edges: int,
                       degenerate_prob: float) -> tuple[list[Edge], list[datetime]]:
    """Deterministic random temporal graph (fixed rng). Returns (edges, t_v_points)."""
    nodes = [f"n{i}" for i in range(n_nodes)]
    edges: list[Edge] = []
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    span_days = 365 * 4
    for eid in range(n_edges):
        a, b = rng.sample(nodes, 2)
        # random validity window somewhere inside [t0, t0 + span]
        lo = t0 + timedelta(days=rng.randint(0, span_days - 1))
        # window length 30..400 days
        length = rng.randint(30, 400)
        hi = lo + timedelta(days=length)
        invalid_at: datetime | None = hi
        if rng.random() < degenerate_prob:
            # degenerate row: invalid_at strictly BEFORE valid_at
            invalid_at = lo - timedelta(days=rng.randint(1, 20))
        edges.append(Edge(eid, a, b, f"rel{rng.randint(0, 3)}", lo, invalid_at))
    # a few query time points spread across the span (some inside, some outside windows)
    t_v_points = [
        t0 + timedelta(days=int(span_days * f)) for f in (0.1, 0.35, 0.5, 0.7, 0.9)
    ]  # already tz-aware via t0
    return edges, t_v_points


def _adjacency(edges: list[Edge]) -> dict[str, list[Edge]]:
    adj: dict[str, list[Edge]] = {}
    for e in edges:
        adj.setdefault(e.src, []).append(e)
        adj.setdefault(e.dst, []).append(e)
    return adj


def enumerate_simple_paths(edges: list[Edge], seeds: list[str], max_depth: int) -> set[tuple[int, ...]]:
    """Enumerate ALL simple paths (no repeated node) up to max_depth hops.

    Undirected: at each node we may leave via either endpoint of an incident
    edge. A path is represented as the ordered tuple of edge ids so brute and
    pushdown enumerations are directly comparable as sets.
    """
    adj = _adjacency(edges)
    results: set[tuple[int, ...]] = set()

    def dfs(node: str, used_nodes: set[str], path: list[int]) -> None:
        if len(path) >= max_depth:
            return
        for e in adj.get(node, ()):
            nxt = e.dst if e.src == node else e.src
            if nxt in used_nodes:
                continue  # simple path: no repeated nodes
            path.append(e.eid)
            used_nodes.add(nxt)
            results.add(tuple(path))
            dfs(nxt, used_nodes, path)
            path.pop()
            used_nodes.discard(nxt)

    for s in seeds:
        dfs(s, {s}, [])
    return results


def check_b_completeness(edges: list[Edge], t_v: datetime, seeds: list[str],
                        max_depth: int) -> dict:
    """Theorem 1: brute (full edges, post-filter Ψ) == pushdown (E_v first).

    Returns per-t_v stats and the equality flag.
    """
    # Reference set R: enumerate over FULL edge set, keep only Ψ-valid paths.
    full_paths = enumerate_simple_paths(edges, seeds, max_depth)
    R: set[tuple[int, ...]] = set()
    edge_by_id = {e.eid: e for e in edges}
    for path in full_paths:
        if all(edge_in_version_bool(edge_by_id[e].valid_at, edge_by_id[e].invalid_at, t_v)
               for e in path):
            R.add(path)

    # Candidate set P: filter edges to E_v, then enumerate over E_v.
    E_v = [e for e in edges
           if edge_in_version_bool(e.valid_at, e.invalid_at, t_v)]
    P = enumerate_simple_paths(E_v, seeds, max_depth)

    sound = P.issubset(R)        # no illegal path slipped into P
    complete = R.issubset(P)     # no legal path dropped from P
    equal = (R == P)
    return {
        "t_v": t_v.date().isoformat(),
        "n_edges_total": len(edges),
        "n_edges_Ev": len(E_v),
        "n_valid_paths_brute_R": len(R),
        "n_paths_pushdown_P": len(P),
        "sound_P_subset_R": sound,
        "complete_R_subset_P": complete,
        "equal_R_eq_P": equal,
    }


def check_a_equivalence(edges: list[Edge], t_v: datetime) -> dict:
    """Lemma 1: boolean predicate vs range-containment predicate per edge.

    Records agreement count, and the degenerate-row divergence explicitly.
    """
    agree = 0
    disagree = 0
    degenerate_raise = 0
    degenerate_bool_false = 0
    examples: list[dict] = []
    for e in edges:
        b = edge_in_version_bool(e.valid_at, e.invalid_at, t_v)
        try:
            r = edge_in_version_range(e.valid_at, e.invalid_at, t_v)
        except ValueError:
            # range form raises on degenerate row
            degenerate_raise += 1
            if b is False:
                degenerate_bool_false += 1
            else:
                # degenerate but boolean says True -> invariant violated AND
                # the two would disagree; record as a real conflict.
                disagree += 1
                if len(examples) < 5:
                    examples.append({"eid": e.eid, "bool": b, "range": "RAISE"})
            continue
        if b == r:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 5:
                examples.append({"eid": e.eid, "bool": b, "range": r})
        # live cross-check (optional)
        if _LIVE_PREDICATE is not None:
            live = _LIVE_PREDICATE(e.valid_at, e.invalid_at, t_v)
            if live != b:
                disagree += 1
                if len(examples) < 5:
                    examples.append({"eid": e.eid, "bool": b, "live": live})
    return {
        "t_v": t_v.date().isoformat(),
        "agree": agree,
        "disagree": disagree,
        "degenerate_range_raise": degenerate_raise,
        "degenerate_bool_false": degenerate_bool_false,
        "examples": examples,
    }


def run_probe(seed: int, n_graphs: int, n_nodes: int, n_edges: int,
              max_depth: int, degenerate_prob: float) -> dict:
    rng = random.Random(seed)
    check_b_all: list[dict] = []
    check_a_all: list[dict] = []
    ratio_sum = 0.0
    ratio_n = 0
    for g in range(n_graphs):
        edges, t_v_points = build_random_graph(rng, n_nodes, n_edges, degenerate_prob)
        seeds = [f"n{0}", f"n{1}"]
        for t_v in t_v_points:
            check_b_all.append(check_b_completeness(edges, t_v, seeds, max_depth))
            check_a_all.append(check_a_equivalence(edges, t_v))
            ev = sum(1 for e in edges
                     if edge_in_version_bool(e.valid_at, e.invalid_at, t_v))
            if len(edges) > 0:
                ratio_sum += ev / len(edges)
                ratio_n += 1

    n_b = len(check_b_all)
    n_a = len(check_a_all)
    b_equal = sum(1 for r in check_b_all if r["equal_R_eq_P"])
    b_sound = sum(1 for r in check_b_all if r["sound_P_subset_R"])
    b_complete = sum(1 for r in check_b_all if r["complete_R_subset_P"])
    a_agree = sum(r["agree"] for r in check_a_all)
    a_disagree = sum(r["disagree"] for r in check_a_all)
    a_degen = sum(r["degenerate_range_raise"] for r in check_a_all)
    live_used = _LIVE_PREDICATE is not None

    return {
        "probe": "p9_path_validity",
        "question": (
            "Does version-predicate pushdown prune exactly E_v (no illegal edge in, no "
            "legal path dropped), and is the boolean predicate identical to the range "
            "predicate on well-formed rows?"
        ),
        "config": {
            "seed": seed,
            "n_graphs": n_graphs,
            "n_nodes": n_nodes,
            "n_edges": n_edges,
            "max_depth": max_depth,
            "degenerate_prob": degenerate_prob,
            "live_version_pin_imported": live_used,
            "scope_note": SCOPE_NOTE,
        },
        "results": {
            "check_B_pruning_completeness": {
                "n_cases": n_b,
                "equal_R_eq_P": b_equal,
                "sound_P_subset_R": b_sound,
                "complete_R_subset_P": b_complete,
                "all_pass": b_equal == n_b,
            },
            "check_A_predicate_equivalence": {
                "n_cases": n_a,
                "agree_rows": a_agree,
                "disagree_rows": a_disagree,
                "degenerate_range_raise_rows": a_degen,
                "all_pass": a_disagree == 0,
            },
            "search_space_compression": {
                "mean_Ev_over_E": (ratio_sum / ratio_n) if ratio_n else None,
                "note": "mean fraction of edges that survive the predicate pushdown; "
                        "empirical companion to proof draft §2.1 complexity table.",
            },
            "scope_note": SCOPE_NOTE,
        },
    }


def print_table(payload: dict) -> None:
    cfg = payload["config"]
    res = payload["results"]
    b = res["check_B_pruning_completeness"]
    a = res["check_A_predicate_equivalence"]
    print(f"== P9: path-validity pruning completeness "
          f"({cfg['n_graphs']} graphs x {cfg['n_nodes']} nodes x {cfg['n_edges']} edges, "
          f"seed={cfg['seed']}) ==")
    print(f"  [CHECK B pruning completeness]  cases={b['n_cases']}  "
          f"equal(R==P)={b['equal_R_eq_P']}  sound={b['sound_P_subset_R']}  "
          f"complete={b['complete_R_subset_P']}  -> {'PASS' if b['all_pass'] else 'FAIL'}")
    print(f"  [CHECK A predicate equivalence] cases={a['n_cases']}  "
          f"agree_rows={a['agree_rows']}  disagree_rows={a['disagree_rows']}  "
          f"degenerate_raise={a['degenerate_range_raise_rows']}  "
          f"-> {'PASS' if a['all_pass'] else 'FAIL'}")
    comp = res["search_space_compression"]
    mean = comp["mean_Ev_over_E"]
    print(f"  [search-space compression]  mean |E_v|/|E| = "
          f"{mean:.3f}" if mean is not None else "n/a")
    print(f"  live version_pin predicate imported: {cfg['live_version_pin_imported']}")
    print(f"  scope: {SCOPE_NOTE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P9 probe: path-validity pruning completeness (synthetic, zero DB/LLM)."
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"random seed (default: {DEFAULT_SEED})")
    parser.add_argument("--graphs", type=int, default=DEFAULT_N_GRAPHS,
                        help=f"number of random graphs (default: {DEFAULT_N_GRAPHS})")
    parser.add_argument("--nodes", type=int, default=DEFAULT_N_NODES,
                        help=f"nodes per graph (default: {DEFAULT_N_NODES})")
    parser.add_argument("--edges", type=int, default=DEFAULT_N_EDGES,
                        help=f"edges per graph (default: {DEFAULT_N_EDGES})")
    parser.add_argument("--depth", type=int, default=DEFAULT_MAX_DEPTH,
                        help=f"max path depth (default: {DEFAULT_MAX_DEPTH})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output JSON path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    payload = run_probe(args.seed, args.graphs, args.nodes, args.edges,
                        args.depth, DEFAULT_DEGENERATE_PROB)
    print_table(payload)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[written] {args.output}")


if __name__ == "__main__":
    main()
