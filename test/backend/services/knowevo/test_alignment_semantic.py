"""Unit tests for services/knowevo/alignment_semantic.py (semantic aligner).

Acceptance anchor (knowevo/backend/services/knowevo/alignment_semantic.py.md
"验收锚点" :120): group keys identical to 's ``aggregate_change_groups``
(same key function), ``weighted_cosine`` = 0 on orthogonal (disjoint) token
sets, ``benjamini_hochberg`` raises ``ValueError`` on out-of-range ``q``,
``wilson_interval(0, 0)`` returns ``(None, None)``, and ``semantic_calibrate``
reports ``precision_identifiable=False`` on the fixture.

Zero LLM / zero DB / zero network: the module under test imports no LLM
client at all (stdlib + intra-package reuse of
``alignment_service.{topic_tokens, normalize_title, assign}``), so these
tests are pure computation over plain-data fixtures with the frozen default
seed (20260923). Fixture design note: the machine side carries the two real
content-bearing groups plus six structural-placeholder groups so the
permutation null draws from a realistic vocabulary -- on a two-group toy the
uniform null too easily reproduces the topic overlap and BH rejects a true
pair (a small-vocab artifact, not a module defect).
"""
import dataclasses
import math
import sys
from pathlib import Path

# Upstream convention: repo backend/ on sys.path, import via the
# services.* prefix (see test_kg_service.py header for the shadowing note).
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo import alignment_semantic as asem
from services.knowevo import alignment_service as als

# ---------------------------------------------------------------------------
# Fixtures as plain data (the reference test style: no pytest fixtures)
# ---------------------------------------------------------------------------

# Two real groups (one with an llm-source prose point, one deterministic
# placeholder) + six placeholder-only groups to widen the permutation
# vocabulary. UNCHANGED must be skipped by both groupers.
MACHINE = [
    {"change_type": "UPDATE", "section_anchor": "3.2 药物治疗",
     "source": "llm", "points": ["二甲双胍是2型糖尿病的一线首选药物。"]},
    {"change_type": "UPDATE", "section_anchor": "3.2 药物治疗",
     "source": "deterministic", "points": ["added paragraph"]},
    {"change_type": "ADD", "section_anchor": "4.3 胰岛素治疗",
     "source": "deterministic", "points": ["added paragraph"]},
] + [
    {"change_type": "UPDATE", "section_anchor": anchor,
     "source": "deterministic", "points": ["added paragraph"]}
    for anchor in ("5.1 血糖监测", "5.2 低血糖的识别与处理",
                   "6.1 糖化血红蛋白检测", "6.2 血糖控制目标",
                   "7.1 降糖药物路径", "7.2 心血管危险因素管理")
]

# t-unverified is deliberately unverified: it must leave both the numerator
# and the denominator of recall (the honesty rule).
GOLD = [
    {"id": "t-drug", "section_anchor": "3.2 药物治疗", "field": "二甲双胍",
     "status": "verified"},
    {"id": "t-insulin", "section_anchor": "4.3 胰岛素治疗", "field": "胰岛素",
     "status": "corrected"},
    {"id": "t-unverified", "section_anchor": "9.9 未核实章节", "field": "",
     "status": "unverified"},
]

# Off-domain decoys share no token with the corpus -> the easy arm can never
# fire and must come out degenerate (contract: its 0 is not specificity
# evidence). The hard arm is in-domain ("二甲双胍" trigrams) so it can.
DECOY_TOPICS = ("火箭发射轨道计算", "区块链挖矿能耗")
HARD_NEGATIVES = ("4.1 二甲双胍剂量",)

# Frozen contract field lists (alignment_semantic.py.md "接口冻结").
CONTRACT_GROUP_FIELDS = ("change_type", "section_anchor", "count", "tokens")
CONTRACT_DECOY_ARM_FIELDS = (
    "n_decoy_topics", "declared_groups", "declared_group_keys",
    "pairs_tested", "degenerate",
)
CONTRACT_CALIBRATION_FIELDS = (
    "recall", "matched_topics", "gold_total", "declared_groups",
    "machine_groups", "alignment_precision_pooled", "false_positives",
    "wilson_low", "wilson_high", "false_positives_easy",
    "precision_identifiable", "precision_caveat", "hard_negative_slot_rate",
    "capacity", "top_k", "fdr_q", "n_perm", "seed", "pairs_tested",
    "pairs_rejected", "legacy_params_accepted_but_inert", "topic_groups",
)


def _machine_as_change_items():
    """The same machine items as alignment_service.ChangeItem (side)."""
    return [
        als.ChangeItem(
            change_type=item["change_type"],
            section_anchor=item["section_anchor"],
            points=list(item["points"]),
            source=item["source"],
        )
        for item in MACHINE
    ]


def _run(**kwargs):
    """One semantic_calibrate run with the frozen defaults (seed 20260923)."""
    return asem.semantic_calibrate(
        MACHINE, GOLD,
        decoy_topics=DECOY_TOPICS, hard_negative_topics=HARD_NEGATIVES,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. group key parity with (anchor: aggregate_change_groups 同款键函数)
# ---------------------------------------------------------------------------

def test_group_keys_match_t21_aggregate_change_groups():
    sem_groups = asem.build_groups(MACHINE)
    t21_groups = als.aggregate_change_groups(_machine_as_change_items())

    sem_keys = {
        (g.change_type, als.normalize_title(g.section_anchor))
        for g in sem_groups
    }
    t21_keys = {
        (g.change_type, als.normalize_title(g.section_anchor))
        for g in t21_groups
    }
    assert sem_keys == t21_keys
    # Same key function => same grouping, including per-key item counts and
    # the UNCHANGED exclusion (4 items -> 2 groups, placeholder groups aside).
    assert len(sem_groups) == len(t21_groups)
    sem_counts = sorted(
        (g.change_type, als.normalize_title(g.section_anchor), g.count)
        for g in sem_groups
    )
    t21_counts = sorted(
        (g.change_type, als.normalize_title(g.section_anchor), g.count)
        for g in t21_groups
    )
    assert sem_counts == t21_counts
    assert ("UNCHANGED", als.normalize_title("3.1 生活方式干预")) not in sem_keys

    # The string join key used at runtime is the same f"{type}|{anchor}" form.
    assert {asem.group_key(g) for g in sem_groups} == {
        f"{g.change_type}|{als.normalize_title(g.section_anchor)}"
        for g in t21_groups
    }


def test_provenance_hygiene_tokens():
    """Anchor always contributes tokens; points only when source == "llm"."""
    groups = {asem.group_key(g): g for g in asem.build_groups(MACHINE)}
    drug = groups["UPDATE|药物治疗"]
    insulin = groups["ADD|胰岛素治疗"]

    # anchor tokens: unconditional
    assert {"药物治", "物治疗"} <= drug.tokens
    assert "胰岛素" in insulin.tokens
    # llm-source prose point: content-bearing -> taken
    assert {"二甲双", "甲双胍"} <= drug.tokens
    # deterministic placeholder points: structural boilerplate -> excluded
    assert "added" not in drug.tokens
    assert "paragraph" not in drug.tokens
    assert "added" not in insulin.tokens

    # The documented "唯一区别在 token 集": 's grouper unions *all* points,
    # so its token sets are supersets that do contain the placeholders.
    t21 = {
        f"{g.change_type}|{als.normalize_title(g.section_anchor)}": g
        for g in als.aggregate_change_groups(_machine_as_change_items())
    }
    assert "added" in t21["UPDATE|药物治疗"].tokens
    assert drug.tokens <= t21["UPDATE|药物治疗"].tokens


# ---------------------------------------------------------------------------
# 2. idf weighting + continuous similarity (anchor: 正交集上 = 0)
# ---------------------------------------------------------------------------

def test_idf_weights_smoothed_formula():
    groups = [
        asem.Group(change_type="UPDATE", section_anchor="a",
                   tokens={"shared", "rare", "ubiq"}),
        asem.Group(change_type="ADD", section_anchor="b",
                   tokens={"shared", "ubiq"}),
    ]
    idf = asem.idf_weights(groups)
    # idf(t) = ln((N+1)/(df(t)+1)) + 1, N=2: df=1 -> ln(3/2)+1, df=2 -> ln(3/3)+1
    assert idf["rare"] == pytest.approx(math.log(3 / 2) + 1.0)
    assert idf["shared"] == pytest.approx(1.0)
    # A token occurring everywhere keeps weight 1 (it is never *deleted*).
    assert idf["ubiq"] == pytest.approx(1.0)
    # Unknown tokens default to 1.0 downstream, and empty input is empty.
    assert asem.idf_weights([]) == {}


def test_weighted_cosine_zero_on_orthogonal_sets():
    idf = {"糖尿病": 1.4, "二甲双": 1.4, "火箭": 1.4, "轨道": 1.4}
    # Orthogonal (disjoint) token sets -> exactly 0, no threshold involved.
    assert asem.weighted_cosine({"糖尿病", "二甲双"}, {"火箭", "轨道"}, idf) == 0.0
    # Empty either side -> 0.
    assert asem.weighted_cosine(set(), {"糖尿病"}, idf) == 0.0
    assert asem.weighted_cosine({"糖尿病"}, set(), idf) == 0.0
    # Positive overlap: strictly inside (0, 1], identical sets -> exactly 1.
    overlap = asem.weighted_cosine({"糖尿病", "火箭"}, {"糖尿病", "轨道"}, idf)
    assert 0.0 < overlap < 1.0
    assert asem.weighted_cosine({"糖尿病", "火箭"}, {"糖尿病", "火箭"}, idf) == 1.0
    # Precomputed norms must not change the value.
    a = {"糖尿病", "火箭"}
    b = {"糖尿病", "轨道"}
    assert asem.weighted_cosine(
        a, b, idf, asem._norm(a, idf), asem._norm(b, idf)
    ) == pytest.approx(overlap)


# ---------------------------------------------------------------------------
# 3. statistics helpers (anchors: BH q 越界抛 ValueError / wilson(0,0))
# ---------------------------------------------------------------------------

def test_benjamini_hochberg_rejects_out_of_range_q():
    pvalues = [0.01, 0.04, 0.03]
    for bad_q in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            asem.benjamini_hochberg(pvalues, bad_q)
    # Empty input short-circuits before validation (implementation-defined).
    assert asem.benjamini_hochberg([], 0.5) == []


def test_benjamini_hochberg_step_up_flags():
    # Classic BH worked example: all four <= their rank threshold -> all reject.
    assert asem.benjamini_hochberg([0.01, 0.04, 0.03, 0.005], 0.05) == [
        True, True, True, True
    ]
    # Step-up: k_max is the largest rank whose p <= (rank+1)/m*q; everything
    # up to it rejects, everything after keeps. Flags stay per input index.
    assert asem.benjamini_hochberg([0.02, 0.5], 0.1) == [True, False]
    assert asem.benjamini_hochberg([1.0], 0.05) == [False]


def test_wilson_interval_zero_n_returns_none_pair():
    # The anchor: no denominator -> no interval, never (0.0, 0.0).
    assert asem.wilson_interval(0, 0) == (None, None)
    assert asem.wilson_interval(5, 0) == (None, None)
    # n > 0: bounds sane; the all-failure / all-success edges clamp to [0, 1].
    lo, hi = asem.wilson_interval(0, 10)
    assert lo == 0.0 and 0.0 < hi < 1.0
    lo, hi = asem.wilson_interval(10, 10)
    assert 0.0 < lo < 1.0 and hi == 1.0
    lo, hi = asem.wilson_interval(5, 10)
    assert 0.0 <= lo <= hi <= 1.0
    # Cross-check against the frozen probe number (probe_p8: pooled 26/26 ->
    # Wilson 95% [0.8713, 1.0000]).
    lo, hi = asem.wilson_interval(26, 26)
    assert lo == pytest.approx(0.8713, abs=1e-3) and hi == 1.0


# ---------------------------------------------------------------------------
# 4. frozen dataclass surfaces
# ---------------------------------------------------------------------------

def test_dataclass_fields_match_frozen_contract():
    assert tuple(asem.Group.__dataclass_fields__) == CONTRACT_GROUP_FIELDS
    assert tuple(asem.DecoyArm.__dataclass_fields__) == CONTRACT_DECOY_ARM_FIELDS
    assert (
        tuple(asem.SemanticCalibration.__dataclass_fields__)
        == CONTRACT_CALIBRATION_FIELDS
    )


# ---------------------------------------------------------------------------
# 5. the pipeline (anchor: precision_identifiable=False on the fixture)
# ---------------------------------------------------------------------------

def test_semantic_calibrate_precision_not_identifiable():
    res, _easy, _hard = _run()
    # Estimand discipline: on real (non-exhaustive-gold) corpora the precision
    # axis is NOT identifiable, and the module must say so itself.
    assert res.precision_identifiable is False
    assert res.precision_caveat == asem.PRECISION_CAVEAT
    assert res.precision_caveat  # non-empty, and carries the estimand caveat


def test_semantic_calibrate_honesty_excludes_unverified_gold():
    res, _easy, _hard = _run()
    # Denominator excludes the unverified row: 2 eligible, not 3.
    assert res.gold_total == 2
    # Both eligible topics match their groups: no threshold cliff on the toy.
    assert res.matched_topics == 2
    assert res.recall == pytest.approx(1.0)
    assert res.machine_groups == len(asem.build_groups(MACHINE))
    assert set(res.topic_groups) == {"t-drug", "t-insulin"}


def test_semantic_calibrate_false_positives_measured_on_hard_arm():
    res, easy, hard = _run()
    declared = {k for keys in res.topic_groups.values() for k in keys}
    assert len(declared) == res.declared_groups
    # Contract: false_positives is the hard-arm intersection ONLY; the easy
    # arm's count is reported separately and never enters the pooled estimate.
    assert res.false_positives == len(declared & set(hard.declared_group_keys))
    assert res.false_positives_easy == len(declared & set(easy.declared_group_keys))
    kept = len(declared) - res.false_positives
    assert res.alignment_precision_pooled == pytest.approx(kept / len(declared))


def test_easy_arm_is_degenerate_without_power():
    res, easy, _hard = _run()
    assert easy.n_decoy_topics == len(DECOY_TOPICS)
    # Off-domain decoys share no token: no testable pair, no power. The flag
    # is the contract's guard so the arm's 0 is never read as "no false
    # positives exist" -- it must be True here, not merely paired with 0s.
    assert easy.pairs_tested == 0
    assert easy.degenerate is True
    assert easy.declared_groups == 0
    assert res.false_positives_easy == 0


def test_hard_arm_can_fire_and_drives_slot_rate():
    res, _easy, hard = _run()
    # The in-domain negative shares 二甲双/甲双胍 trigrams with the corpus, so
    # the arm produces a testable pair (it has power, unlike the easy arm).
    assert hard.n_decoy_topics == len(HARD_NEGATIVES)
    assert hard.pairs_tested >= 1
    assert hard.degenerate is False
    # slot rate = declared hard keys / (n_hard_topics * capacity).
    assert res.hard_negative_slot_rate == pytest.approx(
        len(hard.declared_group_keys) / (len(HARD_NEGATIVES) * res.capacity)
    )


def test_semantic_calibrate_deterministic_per_seed():
    res, easy, hard = _run()
    res2, easy2, hard2 = _run()
    assert dataclasses.asdict(res) == dataclasses.asdict(res2)
    assert dataclasses.asdict(easy) == dataclasses.asdict(easy2)
    assert dataclasses.asdict(hard) == dataclasses.asdict(hard2)
    assert res.seed == 20260923  # the frozen contract default


def test_legacy_params_accepted_but_inert():
    res, easy, hard = _run()
    # Contract: max_df_fraction / min_shared_tokens are grid-compatibility
    # only -- passing them must not move a single number.
    legacy = {"max_df_fraction": 0.05, "min_shared_tokens": 3}
    res_l, easy_l, hard_l = _run(**legacy)
    base = (dataclasses.asdict(res), dataclasses.asdict(easy),
            dataclasses.asdict(hard))
    with_legacy = (dataclasses.asdict(res_l), dataclasses.asdict(easy_l),
                   dataclasses.asdict(hard_l))
    for calib in (with_legacy[0], base[0]):
        calib.pop("legacy_params_accepted_but_inert")
    assert with_legacy[0] == base[0]
    assert with_legacy[1] == base[1]
    assert with_legacy[2] == base[2]
    # ...and the run records what it was given, inert or not.
    assert res_l.legacy_params_accepted_but_inert == legacy
    assert res.legacy_params_accepted_but_inert == {
        "max_df_fraction": None, "min_shared_tokens": None,
    }
