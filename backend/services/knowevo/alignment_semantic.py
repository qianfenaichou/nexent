"""Semantic topic alignment with statistical false-positive control.

Why this module exists (2026-09-23, session B · algorithm-optimization work order
§3.2 / review-feedback §5.3 C-1..C-4)
--------------------------------------------------------------------------
The T-21 topic-level calibrator (:func:`alignment_service.calibrate_topic`)
matches a gold topic to a machine group when they share at least
``min_shared_tokens`` *discriminative* tokens, where discriminative means
``df(token) <= max(3, 0.05 * n_groups)``. On the real 691-item run that rule
turned out to sit on a cliff: recall is 9/9 at ``min_shared_tokens=2`` and
collapses to 3/9 at ``min_shared_tokens=3``.

Measured root cause (this session, zero-LLM inspection of
``competition/deliverables/alignment-diff.json``)
--------------------------------------------------------------------
* 682 of 691 change items carry exactly one ``points`` entry, and for the 684
  ``source == "deterministic"`` items that entry is a *structural placeholder*:
  ``"removed paragraph"`` (142), ``"added paragraph"`` (80), ``"row N: changed"``,
  ``"removed section: /1 000"``. Only the 7 ``source == "llm"`` items carry real
  prose.
* Those placeholders are unioned into the group's token set by
  ``aggregate_change_groups``, so the group vocabulary is dominated by
  ``section`` (df=428), ``removed`` (218), ``new`` (215), ``paragraph`` (56),
  ``added`` (46), ``moved`` (29), ``table`` (24) -- *structural boilerplate*.
* Because the df ceiling is computed over that polluted distribution, the filter
  inverts: it *deletes real domain signal* (``胰岛素`` df=33, ``t2dm`` df=41 both
  exceed the ceiling of 25) while *keeping junk* (``table`` df=24 survives).
  A hard count threshold on a set containing both is brittle by construction.

What this module does instead
-----------------------------
1. **Provenance hygiene** -- group tokens come from ``section_anchor`` always,
   and from ``points`` only when the point is content-bearing
   (``source == "llm"``). Group *keys* are unchanged, so the machine side is
   still comparable with T-21's 517 groups.
2. **Continuous similarity** -- an idf-weighted cosine replaces the
   "count of shared tokens >= k" step function. There is no cliff to fall off:
   similarity degrades smoothly.
3. **Optimal assignment** -- a max-weight bipartite assignment (the existing
   pure-stdlib Hungarian, reused from :mod:`alignment_service`) replaces
   "any group over the threshold matches", with a per-topic capacity so one
   topic may legitimately explain several groups without unbounded inflation.
4. **Monte-Carlo significance + Benjamini-Hochberg FDR** -- each assigned pair
   gets a randomisation p-value; BH controls the expected false-discovery
   proportion. This yields an explicit *false-positive count*, which the old
   ``precision_lower_bound`` structurally could not produce (its denominator was
   every machine group and its numerator counted *relatedness*, not correctness).
5. **A negative-control arm** -- the same pipeline is run over semantically
   unrelated decoy topics. Groups those decoys also "discover" have a measured
   spurious witness, which turns the point estimate into one that is
   **specificity-controlled**:

       alignment_precision_pooled = |R \\ S| / |R|

   where R is the group set declared for the real topics and S the group set
   declared for the decoys. ``|R ∩ S|`` is the false-positive count.

Estimand discipline (work order §3.2, mandatory)
------------------------------------------------
``alignment_precision_pooled`` is **not** the same quantity as T-21's
``64/517``. That number is a *relatedness rate* whose denominator is every
machine group and which carries no false-positive count. These two must never be
placed side by side as if they measured the same thing.

Dependencies
------------
Standard library only, plus intra-package reuse of
:mod:`services.knowevo.alignment_service` (:func:`topic_tokens`,
:func:`normalize_title`, :func:`assign`). No numpy, no scipy, no networkx, no
sklearn: ``alignment_service`` explicitly promises "zero new dependencies:
stdlib only", and ``pyproject.toml`` is a protected wiring file.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from services.knowevo.alignment_service import (
    assign,
    normalize_title,
    topic_tokens,
)

__all__ = [
    "DecoyArm",
    "SemanticCalibration",
    "benjamini_hochberg",
    "build_groups",
    "idf_weights",
    "semantic_calibrate",
    "weighted_cosine",
    "wilson_interval",
]

# ``source`` values whose ``points`` carry real prose rather than a structural
# placeholder. Measured on the 691-item run: 'llm' -> 7 items with genuine
# sentences; 'deterministic' -> 684 items with "added paragraph" /
# "removed paragraph" / "row N: changed" / "removed section: ...".
CONTENT_BEARING_SOURCES = frozenset({"llm"})

# Why ``alignment_precision_pooled`` must not be quoted as a precision on this
# corpus. A valid in-domain negative set would need topics that provably did not
# change between the two guideline versions. The gold seed is explicitly
# *non-exhaustive* (it lists 9 verified + 7 unverified topics out of a real
# revision of unknown size), so no such set is constructible: every candidate
# "unchanged" section may in fact be a change the seed simply does not list.
# The control arms therefore bound nothing, and a group declared for a control
# topic cannot be adjudicated as spurious rather than unlisted-but-real.
PRECISION_CAVEAT = (
    "alignment_precision_pooled 是特异性代理量而非已标定的 precision："
    "本语料的金标是**非穷尽**的（9 verified + 7 unverified），因此不存在"
    "可构造的域内阴性集——任何被当作'未变更'的章节都可能是金标未列入的真实变更，"
    "对照臂的声明无法被裁定为假阳还是'真实但漏列'。"
    "故 precision 轴在本数据上**不可辨识**（insufficient_data），"
    "该值只可用于同口径下的相对比较，不得对外当作 precision 宣称。"
)


@dataclass
class Group:
    """A machine change group: several paragraph items sharing a
    (change_type, normalised section anchor) key."""

    change_type: str
    section_anchor: str
    count: int = 0
    tokens: set[str] = field(default_factory=set)


@dataclass
class DecoyArm:
    """Outcome of one negative-control arm."""

    n_decoy_topics: int
    declared_groups: int
    declared_group_keys: list[str]
    pairs_tested: int
    # True when the arm never even produced a testable pair (similarity 0 for
    # every candidate). A silent arm has *no power*: it cannot falsify anything,
    # so a zero from it must never be read as "no false positives exist".
    degenerate: bool


@dataclass
class SemanticCalibration:
    """Result of the semantic-alignment calibration."""

    recall: float | None
    matched_topics: int
    gold_total: int
    declared_groups: int
    machine_groups: int
    # specificity-controlled precision proxy + its explicit false-positive count.
    # ``false_positives`` is measured against the **hard** (in-domain) arm, which
    # is the only one with the power to fire.
    alignment_precision_pooled: float | None
    false_positives: int
    wilson_low: float | None
    wilson_high: float | None
    false_positives_easy: int
    # Whether ``alignment_precision_pooled`` may be *read as a precision*.
    # False on the real corpus, and deliberately so -- see PRECISION_CAVEAT.
    precision_identifiable: bool
    precision_caveat: str
    hard_negative_slot_rate: float | None
    # bookkeeping so the number can be reproduced or challenged
    capacity: int
    top_k: int
    fdr_q: float
    n_perm: int
    seed: int
    pairs_tested: int
    pairs_rejected: int
    legacy_params_accepted_but_inert: dict[str, Any] = field(default_factory=dict)
    topic_groups: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. group construction with provenance hygiene
# ---------------------------------------------------------------------------


def build_groups(machine: Sequence[dict[str, Any]]) -> list[Group]:
    """Collapse change items into groups, **excluding structural placeholders**.

    The grouping key is exactly the one used by
    :func:`alignment_service.aggregate_change_groups` -- ``(change_type,
    normalize_title(section_anchor))``, skipping ``UNCHANGED`` -- so the group
    count stays comparable with the T-21 caliber. Only the *token* set differs:
    tokens are taken from the section anchor unconditionally, and from a
    ``points`` entry only when that item's ``source`` marks the entry as real
    prose. Including the placeholders is what poisoned the df statistics.
    """
    buckets: dict[tuple[str, str], Group] = {}
    for item in machine:
        change_type = str(item.get("change_type") or "")
        if change_type == "UNCHANGED":
            continue
        anchor = str(item.get("section_anchor") or "")
        key = (change_type, normalize_title(anchor))
        group = buckets.get(key)
        if group is None:
            group = Group(change_type=change_type, section_anchor=anchor)
            buckets[key] = group
        group.count += 1
        group.tokens |= topic_tokens(anchor)
        if str(item.get("source") or "") in CONTENT_BEARING_SOURCES:
            for point in item.get("points") or ():
                if isinstance(point, str):
                    group.tokens |= topic_tokens(point)
    return list(buckets.values())


def group_key(group: Group) -> str:
    return f"{group.change_type}|{normalize_title(group.section_anchor)}"


# ---------------------------------------------------------------------------
# 2. idf weighting + continuous similarity
# ---------------------------------------------------------------------------


def idf_weights(groups: Sequence[Group]) -> dict[str, float]:
    """Smoothed inverse document frequency over the group collection.

    ``idf(t) = ln((N + 1) / (df(t) + 1)) + 1``. This *replaces* the hard
    ``df <= max_df_fraction * N`` filter: a token that occurs everywhere gets a
    weight near 1 and simply contributes nothing, instead of being deleted and
    taking genuinely discriminative neighbours with it.
    """
    n = len(groups)
    df: dict[str, int] = {}
    for group in groups:
        for token in group.tokens:
            df[token] = df.get(token, 0) + 1
    return {t: math.log((n + 1) / (d + 1)) + 1.0 for t, d in df.items()}


def _norm(tokens: set[str], idf: dict[str, float]) -> float:
    return math.sqrt(sum(idf.get(t, 1.0) ** 2 for t in tokens))


def weighted_cosine(
    a: set[str], b: set[str], idf: dict[str, float],
    norm_a: float | None = None, norm_b: float | None = None,
) -> float:
    """idf-weighted cosine similarity in [0, 1] (continuous, no threshold)."""
    if not a or not b:
        return 0.0
    shared = a & b
    if not shared:
        return 0.0
    num = sum(idf.get(t, 1.0) ** 2 for t in shared)
    na = norm_a if norm_a is not None else _norm(a, idf)
    nb = norm_b if norm_b is not None else _norm(b, idf)
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return max(0.0, min(1.0, num / (na * nb)))


# ---------------------------------------------------------------------------
# 3. statistics helpers
# ---------------------------------------------------------------------------


def benjamini_hochberg(pvalues: Sequence[float], q: float) -> list[bool]:
    """Benjamini-Hochberg step-up. Returns a reject/keep flag per input index."""
    m = len(pvalues)
    if m == 0:
        return []
    if not (0.0 < q < 1.0):
        raise ValueError(f"q must be in (0, 1), got {q!r}")
    order = sorted(range(m), key=lambda i: (pvalues[i], i))
    thresholds = [pvalues[i] <= (rank + 1) / m * q for rank, i in enumerate(order)]
    k_max = -1
    for rank, ok in enumerate(thresholds):
        if ok:
            k_max = rank
    flags = [False] * m
    if k_max >= 0:
        for rank in range(k_max + 1):
            flags[order[rank]] = True
    return flags


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Wilson score interval (95% by default). Returns (None, None) when n == 0."""
    if n <= 0:
        return (None, None)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _gold_tokens(row: dict[str, Any]) -> set[str]:
    """Gold-side token set: anchor plus field, exactly as T-21 does."""
    return topic_tokens(str(row.get("section_anchor") or "")) | topic_tokens(
        str(row.get("field") or "")
    )


def _eligible(gold: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in gold
        if str(row.get("status", "")).lower() in ("verified", "corrected")
    ]


# ---------------------------------------------------------------------------
# 4. the pipeline
# ---------------------------------------------------------------------------


def _declared_for(
    gold_rows: Sequence[dict[str, Any]],
    groups: Sequence[Group],
    idf: dict[str, float],
    globals_: dict[str, float],
    *,
    capacity: int,
    top_k: int,
    fdr_q: float,
    n_perm: int,
    rng: random.Random,
    vocab: list[str],
) -> tuple[set[str], dict[str, list[str]], int, int]:
    """Run steps 2-4 for one arm (real topics, or decoys).

    Returns ``(declared_group_keys, topic -> declared keys, pairs_tested,
    pairs_rejected)``.
    """
    if not gold_rows or not groups:
        return set(), {}, 0, 0

    group_freq: list[set[str]] = [g.tokens for g in groups]
    group_norms = [_norm(t, idf) for t in group_freq]
    gkeys = [group_key(g) for g in groups]

    topics: list[set[str]] = []
    for row in gold_rows:
        toks = _gold_tokens(row)
        if not toks:
            toks = topic_tokens(str(row.get("id") or ""))
        topics.append(toks)

    # -- step 2: similarity of every topic to every group -------------------
    sim_rows: list[list[float]] = []
    for toks in topics:
        na = _norm(toks, idf)
        sim_rows.append([
            weighted_cosine(toks, group_freq[j], idf, na, group_norms[j])
            for j in range(len(groups))
        ])

    # -- step 3: candidate generation + optimal assignment ------------------
    # Candidates are the per-topic top-K by similarity. Assignment happens on
    # the reduced matrix; this keeps the Hungarian exact over the candidate set
    # (matrix stays far below alignment_service.MAX_ASSIGN_CELLS) but is *not*
    # a global optimum over all 517 groups -- stated plainly rather than implied.
    cand: list[int] = []
    seen: set[int] = set()
    per_topic_cand: list[list[int]] = []
    for row in sim_rows:
        order = sorted(range(len(groups)), key=lambda j: (-row[j], j))[:top_k]
        per_topic_cand.append(order)
        for j in order:
            if j not in seen:
                seen.add(j)
                cand.append(j)
    if not cand:
        return set(), {}, 0, 0

    rows = len(topics) * capacity
    matrix = [[0.0] * len(cand) for _ in range(rows)]
    slot_owner: list[int] = []
    for t, order in enumerate(per_topic_cand):
        order_set = {j: pos for pos, j in enumerate(cand) if j in set(order)}
        for rep in range(capacity):
            r = t * capacity + rep
            slot_owner.append(t)
            for j, pos in order_set.items():
                matrix[r][pos] = sim_rows[t][j]

    assignments = assign(matrix, low=0.0)

    # -- step 4: Monte-Carlo significance + BH-FDR --------------------------
    # Randomisation null: draw a token set of the same size as the group,
    # uniformly from the observed vocabulary, and ask how often an unrelated
    # set would score at least this high. Cached per (topic, |group tokens|).
    null_cache: dict[tuple[int, int], list[float]] = {}

    def null_dist(t: int, size: int) -> list[float]:
        key = (t, size)
        hit = null_cache.get(key)
        if hit is not None:
            return hit
        toks = topics[t]
        na = _norm(toks, idf)
        draws: list[float] = []
        for _ in range(n_perm):
            sample = set(rng.sample(vocab, min(size, len(vocab))))
            draws.append(weighted_cosine(toks, sample, idf, na, None))
        null_cache[key] = draws
        return draws

    pvals: list[float] = []
    meta: list[tuple[int, int]] = []  # (topic index, group index)
    for a in assignments:
        r, pos = a.old_index, a.new_index
        if r >= len(slot_owner):
            continue
        t = slot_owner[r]
        j = cand[pos]
        obs = a.similarity
        if obs <= 0.0:
            continue
        draws = null_dist(t, len(group_freq[j]))
        ge = sum(1 for d in draws if d >= obs)
        p = (ge + 1) / (n_perm + 1)
        pvals.append(p)
        meta.append((t, j))

    flags = benjamini_hochberg(pvals, fdr_q)

    declared: set[str] = set()
    by_topic: dict[str, list[str]] = {}
    rejected = 0
    for flag, (t, j) in zip(flags, meta):
        if not flag:
            continue
        rejected += 1
        declared.add(gkeys[j])
        topic_id = str(gold_rows[t].get("id") or gold_rows[t].get("section_anchor") or t)
        by_topic.setdefault(topic_id, []).append(gkeys[j])
    return declared, by_topic, len(pvals), rejected


def semantic_calibrate(
    machine: Sequence[dict[str, Any]],
    gold: Sequence[dict[str, Any]],
    *,
    capacity: int = 3,
    top_k: int = 15,
    fdr_q: float = 0.10,
    n_perm: int = 1000,
    seed: int = 20260923,
    decoy_topics: Sequence[str] = (),
    hard_negative_topics: Sequence[str] = (),
    # accepted for grid-compatibility with the legacy calibrator, deliberately
    # unused -- see the class docstring: the whole point is that these two
    # parameters stop being load-bearing.
    max_df_fraction: float | None = None,
    min_shared_tokens: int | None = None,
) -> tuple[SemanticCalibration, DecoyArm, DecoyArm]:
    """Calibrate topic alignment semantically, with FDR-controlled false positives.

    Two negative-control arms are run, and their difference matters:

    * the **easy** arm is off-domain (rockets, blockchains, ...). It shares no
      tokens with the corpus, so it cannot fail -- a zero there is *not*
      evidence of specificity. Arms that never produce a testable pair are
      flagged ``degenerate``.
    * the **hard** arm is in-domain: real diabetes-guideline section topics that
      the seed does not claim as changes. These share the ubiquitous trigram
      ``糖尿病`` (and friends) with genuine groups, so they *do* stress the
      matcher and can fire.

    ``false_positives`` -- and therefore ``alignment_precision_pooled`` -- is
    measured against the **hard** arm only. The easy arm's count is reported
    separately as ``false_positives_easy`` and must never be quoted as the
    precision basis.

    Residual caveat, stated rather than buried: the gold seed is
    *non-exhaustive*, so a group declared for a hard negative is only
    **probably** spurious -- it could be a real change the seed happens not to
    list. ``alignment_precision_pooled`` is therefore a specificity-controlled
    point estimate, not a validated precision.
    """
    rng = random.Random(seed)
    groups = build_groups(machine)
    idf = idf_weights(groups)
    vocab = sorted(idf)
    eligible = _eligible(gold)

    def arm(topics: Sequence[str]) -> tuple[set[str], int]:
        if not topics:
            return set(), 0
        rows = [
            {"id": f"ctl{i}", "change_type": "UPDATE", "section_anchor": t,
             "status": "verified", "field": ""}
            for i, t in enumerate(topics, start=1)
        ]
        declared, _, tested, _ = _declared_for(
            rows, groups, idf, {}, capacity=capacity, top_k=top_k,
            fdr_q=fdr_q, n_perm=n_perm, rng=rng, vocab=vocab,
        )
        return declared, tested

    declared, by_topic, tested, rejected = _declared_for(
        eligible, groups, idf, {}, capacity=capacity, top_k=top_k,
        fdr_q=fdr_q, n_perm=n_perm, rng=rng, vocab=vocab,
    )
    recall = (len(by_topic) / len(eligible)) if eligible else None

    easy_keys, easy_tested = arm(decoy_topics)
    hard_keys, hard_tested = arm(hard_negative_topics)

    fp_hard = declared & hard_keys
    fp_easy = declared & easy_keys
    kept = len(declared) - len(fp_hard)
    precision = (kept / len(declared)) if declared else None
    lo, hi = wilson_interval(kept, len(declared))

    return (
        SemanticCalibration(
            recall=recall,
            matched_topics=len(by_topic),
            gold_total=len(eligible),
            declared_groups=len(declared),
            machine_groups=len(groups),
            alignment_precision_pooled=precision,
            false_positives=len(fp_hard),
            wilson_low=lo,
            wilson_high=hi,
            false_positives_easy=len(fp_easy),
            precision_identifiable=False,
            precision_caveat=PRECISION_CAVEAT,
            hard_negative_slot_rate=(
                len(hard_keys) / (len(hard_negative_topics) * capacity)
                if hard_negative_topics and capacity > 0 else None
            ),
            capacity=capacity,
            top_k=top_k,
            fdr_q=fdr_q,
            n_perm=n_perm,
            seed=seed,
            pairs_tested=tested,
            pairs_rejected=rejected,
            legacy_params_accepted_but_inert={
                "max_df_fraction": max_df_fraction,
                "min_shared_tokens": min_shared_tokens,
            },
            topic_groups=by_topic,
        ),
        DecoyArm(
            n_decoy_topics=len(decoy_topics),
            declared_groups=len(easy_keys),
            declared_group_keys=sorted(easy_keys),
            pairs_tested=easy_tested,
            degenerate=easy_tested == 0,
        ),
        DecoyArm(
            n_decoy_topics=len(hard_negative_topics),
            declared_groups=len(hard_keys),
            declared_group_keys=sorted(hard_keys),
            pairs_tested=hard_tested,
            degenerate=hard_tested == 0,
        ),
    )
