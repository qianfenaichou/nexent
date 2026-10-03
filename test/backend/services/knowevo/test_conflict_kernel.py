"""Tests for the conflict-reconciliation kernel (conflict_kernel).

Spec anchors: workspace archive doc
``archive/旧计划书/05-项目综合评估与优化路线-独立评审.md`` §3.4 (three-layer
algorithm: detect / classify / resolve, frozen attribution vocabulary) and
``competition/docs/tech-optimization-2026-09-28/a4-conflict-design-2026-09-30.md``.

Semantics pinned here (every non-obvious expectation was verified by
actually running the implementation first - : never back-fill
expected values from mental simulation):

- conflict key: relation = (subject, predicate, object); attribute =
  (subject, predicate). Value disagreement AND half-open [valid_at,
  invalid_at) window overlap are both required.
- windows: start inclusive, end exclusive (graph_store '[)' convention);
  touching endpoints do NOT overlap; ``valid_at == invalid_at`` is an empty
  window and never conflicts.
- classification vocabulary is closed: EVOLUTION / SOURCE_AUTHORITY /
  EXTRACTION_ERROR / SAME_SOURCE; first matching rule wins (extraction flag,
  same-source-same-version, same-lineage-different-version, else source).
- resolution: kind defaults (EVOLUTION -> version axis, SOURCE_AUTHORITY ->
  authority axis) and an explicit ``prefer`` override; smaller authority
  rank wins (platform scale 1..4); version axis prefers current-at-clock
  then later valid_at; full tie falls back to lexicographic fact_id.
- LLM seam: never invoked when ``llm=None``; invoked only for SAME_SOURCE
  when a callable is supplied.
- determinism: same facts in any input order -> identical candidates and
  records (conflict_id / replay_key included).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.conflict_kernel import (
    CONFLICT_KINDS,
    PREFER_AUTHORITY,
    PREFER_VERSION,
    RESOLUTION_AUTHORITY_WIN,
    RESOLUTION_HUMAN_REVIEW,
    RESOLUTION_LLM_SEAM,
    RESOLUTION_REQUEUE_EXTRACTION,
    RESOLUTION_TIE_BREAK_ID,
    RESOLUTION_VERSION_WIN,
    ClassifyContext,
    ConflictKind,
    Fact,
    classify_conflict,
    detect_conflicts,
    reconcile,
    resolve_conflict,
    valid_at_clock,
    windows_overlap,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=30)
T2 = T0 + timedelta(days=60)
T3 = T0 + timedelta(days=90)
T4 = T0 + timedelta(days=120)
CLOCK = T3  # default adjudication clock: after both overlapping windows start


def fact(
    fid,
    *,
    ftype="attribute",
    subject="drug:aspirin",
    predicate="max_dose",
    obj="",
    value="100mg",
    source_id="doc:label",
    source_group="",
    version="",
    authority=3,
    valid_at=T0,
    invalid_at=T4,
    claim="",
):
    """Shorthand builder keeping fixture tables readable."""
    return Fact(
        fact_id=fid,
        fact_type=ftype,
        subject=subject,
        predicate=predicate,
        object=obj,
        value=value,
        source_id=source_id,
        source_group=source_group,
        version=version,
        authority_level=authority,
        valid_at=valid_at,
        invalid_at=invalid_at,
        claim=claim,
    )


def rel(fid, **kw):
    kw.setdefault("ftype", "relation")
    kw.setdefault("subject", "drug:aspirin")
    kw.setdefault("predicate", "indicated_for")
    kw.setdefault("obj", "disease:mi")
    kw.setdefault("value", "indicated")
    return fact(fid, **kw)


# ---------------------------------------------------------------------------
# 1. Detection: same key + disagreeing value + overlapping window
# ---------------------------------------------------------------------------


class TestDetectConflicts:
    def test_attribute_value_conflict_detected(self):
        a = fact("a", value="100mg", source_id="doc:label")
        b = fact("b", value="300mg", source_id="doc:guideline")
        cands = detect_conflicts([a, b])
        assert len(cands) == 1
        c = cands[0]
        assert (c.left.fact_id, c.right.fact_id) == ("a", "b")
        assert c.key == ("attribute", "drug:aspirin", "max_dose", "")

    def test_relation_same_triple_value_conflict(self):
        a = rel("r1", value="indicated", source_id="doc:label")
        b = rel("r2", value="contraindicated", source_id="doc:guideline")
        cands = detect_conflicts([a, b])
        assert len(cands) == 1
        assert cands[0].key == (
            "relation",
            "drug:aspirin",
            "indicated_for",
            "disease:mi",
        )

    def test_relation_different_object_is_not_same_key(self):
        a = rel("r1", obj="disease:mi", value="indicated")
        b = rel("r2", obj="disease:stroke", value="contraindicated")
        assert detect_conflicts([a, b]) == []

    def test_agreeing_values_are_not_conflicts(self):
        a = fact("a", value="100mg")
        b = fact("b", value="100mg", source_id="doc:other")
        assert detect_conflicts([a, b]) == []

    def test_attribute_and_relation_keys_do_not_collide(self):
        a = fact("a", ftype="attribute", predicate="max_dose", value="100mg")
        b = rel("b", value="contraindicated")
        assert detect_conflicts([a, b]) == []

    def test_three_way_group_yields_all_disagreeing_pairs(self):
        a = fact("a", value="100mg")
        b = fact("b", value="200mg")
        c = fact("c", value="100mg")  # agrees with a -> only two pairs
        cands = detect_conflicts([a, b, c])
        pairs = [(x.left.fact_id, x.right.fact_id) for x in cands]
        assert pairs == [("a", "b"), ("b", "c")]

    def test_duplicate_fact_id_rejected(self):
        a = fact("dup", value="100mg")
        b = fact("dup", value="200mg")
        with pytest.raises(ValueError, match="duplicate fact_id"):
            detect_conflicts([a, b])

    def test_conflict_id_stable_and_order_independent(self):
        a = fact("a", value="100mg")
        b = fact("b", value="200mg")
        assert detect_conflicts([a, b])[0].conflict_id == detect_conflicts([b, a])[0].conflict_id
        assert len(detect_conflicts([a, b])[0].conflict_id) == 16

    def test_empty_input(self):
        assert detect_conflicts([]) == []


# ---------------------------------------------------------------------------
# 2. Bi-temporal window boundaries (half-open [lo, hi))
# ---------------------------------------------------------------------------


class TestValidAtBoundaries:
    def test_start_inclusive_end_exclusive(self):
        w = fact("w", valid_at=T0, invalid_at=T2)
        assert valid_at_clock(w, T0) is True
        assert valid_at_clock(w, T2 - timedelta(seconds=1)) is True
        assert valid_at_clock(w, T2) is False
        assert valid_at_clock(w, T0 - timedelta(seconds=1)) is False

    def test_open_end_boundary_no_overlap_with_touching_start(self):
        # [T0, T1) and [T1, T2) touch at T1 but do not overlap under [).
        a = fact("a", value="100mg", valid_at=T0, invalid_at=T1)
        b = fact("b", value="200mg", valid_at=T1, invalid_at=T2)
        assert windows_overlap(a, b) is False
        assert detect_conflicts([a, b]) == []

    def test_zero_overlap_disjoint_windows(self):
        a = fact("a", value="100mg", valid_at=T0, invalid_at=T1)
        b = fact("b", value="200mg", valid_at=T2, invalid_at=T3)
        assert windows_overlap(a, b) is False
        assert detect_conflicts([a, b]) == []

    def test_single_point_touch_is_not_overlap(self):
        # Share exactly the instant T1: zero-length intersection under [).
        a = fact("a", value="100mg", valid_at=T0, invalid_at=T1)
        b = fact("b", value="200mg", valid_at=T1, invalid_at=T1 + timedelta(hours=1))
        assert windows_overlap(a, b) is False

    def test_instantaneous_window_valid_at_equals_invalid_at_is_empty(self):
        inst = fact("i", value="100mg", valid_at=T1, invalid_at=T1)
        other = fact("o", value="200mg", valid_at=T0, invalid_at=T2)
        assert windows_overlap(inst, other) is False
        assert valid_at_clock(inst, T1) is False
        assert detect_conflicts([inst, other]) == []

    def test_open_ended_window_overlaps_closed_one(self):
        # invalid_at=None -> +infinity; valid_at=None -> -infinity.
        a = fact("a", value="100mg", valid_at=T1, invalid_at=None)
        b = fact("b", value="200mg", valid_at=None, invalid_at=T2)
        assert windows_overlap(a, b) is True
        assert len(detect_conflicts([a, b])) == 1

    def test_degenerate_window_invalid_before_valid_rejected(self):
        with pytest.raises(ValueError, match="invalid_at"):
            fact("x", valid_at=T2, invalid_at=T0)

    def test_one_instant_shared_at_endpoint_uses_open_end(self):
        # b starts exactly when a ends: the shared instant belongs to b only.
        a = fact("a", value="100mg", valid_at=T0, invalid_at=T1)
        b = fact("b", value="200mg", valid_at=T1, invalid_at=T2)
        assert valid_at_clock(a, T1) is False
        assert valid_at_clock(b, T1) is True


# ---------------------------------------------------------------------------
# 3. Classification: the frozen four-way vocabulary
# ---------------------------------------------------------------------------


class TestClassify:
    def _cand(self, a, b):
        return detect_conflicts([a, b])[0]

    def test_source_authority_when_sources_differ(self):
        a = fact("a", value="100mg", source_id="doc:label", source_group="label")
        b = fact("b", value="300mg", source_id="doc:guideline", source_group="guide")
        assert classify_conflict(self._cand(a, b)) is ConflictKind.SOURCE_AUTHORITY

    def test_same_source_same_version_is_same_source(self):
        a = fact("a", value="100mg", source_id="doc:guideline", version="v1")
        b = fact("b", value="300mg", source_id="doc:guideline", version="v1")
        assert classify_conflict(self._cand(a, b)) is ConflictKind.SAME_SOURCE

    def test_same_source_empty_version_is_same_source(self):
        a = fact("a", value="100mg", source_id="doc:note")
        b = fact("b", value="300mg", source_id="doc:note")
        assert classify_conflict(self._cand(a, b)) is ConflictKind.SAME_SOURCE

    def test_same_source_different_version_is_evolution(self):
        a = fact("a", value="100mg", source_id="doc:guideline", version="v1")
        b = fact("b", value="300mg", source_id="doc:guideline", version="v2")
        assert classify_conflict(self._cand(a, b)) is ConflictKind.EVOLUTION

    def test_same_group_different_source_different_version_is_evolution(self):
        a = fact(
            "a",
            value="100mg",
            source_id="doc:g2020",
            source_group="guideline:copd",
            version="2020",
        )
        b = fact(
            "b",
            value="300mg",
            source_id="doc:g2024",
            source_group="guideline:copd",
            version="2024",
        )
        assert classify_conflict(self._cand(a, b)) is ConflictKind.EVOLUTION

    def test_same_group_same_version_different_source_is_source_authority(self):
        a = fact(
            "a",
            value="100mg",
            source_id="doc:x",
            source_group="series",
            version="v1",
        )
        b = fact(
            "b",
            value="300mg",
            source_id="doc:y",
            source_group="series",
            version="v1",
        )
        assert classify_conflict(self._cand(a, b)) is ConflictKind.SOURCE_AUTHORITY

    def test_extraction_error_flag_wins_over_other_kinds(self):
        a = fact("a", value="100mg", source_id="doc:guideline", version="v1")
        b = fact("b", value="300mg", source_id="doc:guideline", version="v1")
        ctx = ClassifyContext(extraction_error_ids=frozenset({"a"}))
        assert (
            classify_conflict(self._cand(a, b), ctx) is ConflictKind.EXTRACTION_ERROR
        )

    def test_extraction_error_beats_evolution(self):
        a = fact("a", value="100mg", source_id="doc:g", version="v1")
        b = fact("b", value="300mg", source_id="doc:g", version="v2")
        ctx = ClassifyContext(extraction_error_ids=frozenset({"b"}))
        assert (
            classify_conflict(self._cand(a, b), ctx) is ConflictKind.EXTRACTION_ERROR
        )

    def test_vocabulary_is_closed_four_strings(self):
        assert CONFLICT_KINDS == (
            "EVOLUTION",
            "SOURCE_AUTHORITY",
            "EXTRACTION_ERROR",
            "SAME_SOURCE",
        )
        assert {k.value for k in ConflictKind} == set(CONFLICT_KINDS)


# ---------------------------------------------------------------------------
# 4. Resolution: authority vs version, configurable priority
# ---------------------------------------------------------------------------


class TestResolveAxes:
    def _cand(self, a, b):
        return detect_conflicts([a, b])[0]

    def test_authority_beats_version_by_default_on_source_authority(self):
        # a: better authority (1) but older window start; b: worse authority
        # (4) but newer and still current at CLOCK. Windows overlap so the
        # pair is a conflict candidate.
        a = fact(
            "a",
            value="100mg",
            source_id="doc:std",
            authority=1,
            valid_at=T0,
            invalid_at=T4,
        )
        b = fact(
            "b",
            value="300mg",
            source_id="doc:pop",
            authority=4,
            valid_at=T2,
            invalid_at=None,
        )
        rank = {"doc:std": 1, "doc:pop": 4}
        rec = resolve_conflict(
            self._cand(a, b), authority_rank=rank, version_clock=CLOCK
        )
        assert rec.kind == ConflictKind.SOURCE_AUTHORITY.value
        assert rec.winner_id == "a"
        assert rec.resolution == RESOLUTION_AUTHORITY_WIN
        assert rec.policy == PREFER_AUTHORITY
        assert rec.contested is False

    def test_version_beats_authority_when_prefer_version(self):
        a = fact(
            "a",
            value="100mg",
            source_id="doc:std",
            authority=1,
            valid_at=T0,
            invalid_at=T4,
        )
        b = fact(
            "b",
            value="300mg",
            source_id="doc:pop",
            authority=4,
            valid_at=T2,
            invalid_at=None,
        )
        rank = {"doc:std": 1, "doc:pop": 4}
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank=rank,
            version_clock=CLOCK,
            prefer=PREFER_VERSION,
        )
        assert rec.winner_id == "b"
        assert rec.resolution == RESOLUTION_VERSION_WIN
        assert rec.policy == PREFER_VERSION

    def test_evolution_kind_defaults_to_version_axis(self):
        a = fact(
            "a",
            value="100mg",
            source_id="doc:g",
            version="v1",
            authority=1,
            valid_at=T0,
            invalid_at=T4,
        )
        b = fact(
            "b",
            value="300mg",
            source_id="doc:g",
            version="v2",
            authority=4,
            valid_at=T2,
            invalid_at=None,
        )
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank={"doc:g": 4},
            version_clock=CLOCK,
        )
        # Spec EVOLUTION -> version first: newer fact wins despite worse authority.
        assert rec.kind == ConflictKind.EVOLUTION.value
        assert rec.winner_id == "b"
        assert rec.resolution == RESOLUTION_VERSION_WIN
        assert rec.policy == PREFER_VERSION
        assert rec.superseded_ids == ("a",)

    def test_evolution_honours_explicit_prefer_authority_override(self):
        a = fact(
            "a",
            value="100mg",
            source_id="doc:g",
            version="v1",
            authority=1,
            valid_at=T0,
            invalid_at=T4,
        )
        b = fact(
            "b",
            value="300mg",
            source_id="doc:g",
            version="v2",
            authority=4,
            valid_at=T2,
            invalid_at=None,
        )
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank={"doc:g": 1},  # both same source_id -> same rank
            version_clock=CLOCK,
            prefer=PREFER_AUTHORITY,
        )
        # ranks tie (both doc:g) -> version is the secondary axis -> b wins,
        # but recorded as the version secondary, not the authority primary.
        assert rec.winner_id == "b"
        assert rec.resolution == RESOLUTION_VERSION_WIN

    def test_current_at_clock_beats_stale_on_version_axis(self):
        # Both authority-tied; a already invalid at CLOCK, b still current.
        a = fact(
            "a",
            value="100mg",
            source_id="doc:x",
            authority=2,
            valid_at=T0,
            invalid_at=T1,
        )
        b = fact(
            "b",
            value="300mg",
            source_id="doc:y",
            authority=2,
            valid_at=T0,
            invalid_at=None,
        )
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank={"doc:x": 2, "doc:y": 2},
            version_clock=CLOCK,
            prefer=PREFER_VERSION,
        )
        assert rec.winner_id == "b"

    def test_tie_break_by_fact_id_when_both_axes_tie(self):
        a = fact("zeta", value="100mg", source_id="doc:x", authority=2)
        b = fact("alpha", value="200mg", source_id="doc:y", authority=2)
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank={"doc:x": 2, "doc:y": 2},
            version_clock=CLOCK,
        )
        assert rec.winner_id == "alpha"
        assert rec.resolution == RESOLUTION_TIE_BREAK_ID

    def test_authority_rank_mapping_overrides_fact_level(self):
        a = fact("a", value="100mg", source_id="doc:x", authority=4)
        b = fact("b", value="200mg", source_id="doc:y", authority=4)
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank={"doc:x": 1, "doc:y": 5},
            version_clock=CLOCK,
        )
        assert rec.winner_id == "a"
        assert rec.winner_authority == 1
        assert rec.loser_authority == 5

    def test_missing_rank_falls_back_to_fact_authority_level(self):
        a = fact("a", value="100mg", source_id="doc:x", authority=1)
        b = fact("b", value="200mg", source_id="doc:y", authority=4)
        rec = resolve_conflict(
            self._cand(a, b), authority_rank=None, version_clock=CLOCK
        )
        assert rec.winner_id == "a"
        assert rec.winner_authority == 1

    def test_invalid_prefer_rejected(self):
        c = detect_conflicts([fact("a", value="1"), fact("b", value="2")])[0]
        with pytest.raises(ValueError, match="prefer"):
            resolve_conflict(
                c, authority_rank=None, version_clock=CLOCK, prefer="recency"
            )

    def test_version_clock_accepts_version_clock_like_object(self):
        class _Clock:
            as_of = CLOCK

        c = detect_conflicts([fact("a", value="1"), fact("b", value="2")])[0]
        rec = resolve_conflict(c, authority_rank=None, version_clock=_Clock())
        assert rec.version_clock_iso == CLOCK.isoformat()

    def test_version_clock_rejects_junk(self):
        c = detect_conflicts([fact("a", value="1"), fact("b", value="2")])[0]
        with pytest.raises(TypeError, match="version_clock"):
            resolve_conflict(c, authority_rank=None, version_clock="2024-01-01")


# ---------------------------------------------------------------------------
# 5. Kind-specific resolution actions (spec §3.4)
# ---------------------------------------------------------------------------


class TestKindActions:
    def _cand(self, a, b):
        return detect_conflicts([a, b])[0]

    def test_extraction_error_requeues_and_loses(self):
        a = fact("a", value="100mg", source_id="doc:label", authority=1)
        b = fact("b", value="300mg", source_id="doc:guideline", authority=2)
        ctx = ClassifyContext(extraction_error_ids=frozenset({"b"}))
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank=None,
            version_clock=CLOCK,
            context=ctx,
        )
        assert rec.kind == ConflictKind.EXTRACTION_ERROR.value
        assert rec.resolution == RESOLUTION_REQUEUE_EXTRACTION
        assert rec.winner_id == "a"
        assert rec.loser_id == "b"
        assert rec.superseded_ids == ("b",)
        assert rec.contested is False

    def test_both_sides_extraction_error_is_contested(self):
        a = fact("a", value="100mg")
        b = fact("b", value="300mg")
        ctx = ClassifyContext(extraction_error_ids=frozenset({"a", "b"}))
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank=None,
            version_clock=CLOCK,
            context=ctx,
        )
        assert rec.contested is True
        assert rec.resolution == RESOLUTION_REQUEUE_EXTRACTION
        assert rec.winner_id == "a"  # deterministic provisional

    def test_same_source_is_contested_human_review(self):
        a = fact("a", value="100mg", source_id="doc:note", authority=2)
        b = fact("b", value="300mg", source_id="doc:note", authority=2)
        rec = resolve_conflict(
            self._cand(a, b), authority_rank=None, version_clock=CLOCK
        )
        assert rec.kind == ConflictKind.SAME_SOURCE.value
        assert rec.resolution == RESOLUTION_HUMAN_REVIEW
        assert rec.contested is True

    def test_to_wire_matches_schemas_triple(self):
        a = fact("a", value="100mg", source_id="doc:x", authority=1)
        b = fact("b", value="200mg", source_id="doc:y", authority=2)
        rec = resolve_conflict(
            self._cand(a, b), authority_rank=None, version_clock=CLOCK
        )
        wire = rec.to_wire()
        assert set(wire) == {"conflict_id", "type", "resolution"}
        assert wire["conflict_id"] == rec.conflict_id
        assert wire["type"] == rec.kind
        assert wire["resolution"] == rec.resolution


# ---------------------------------------------------------------------------
# 6. LLM seam
# ---------------------------------------------------------------------------


class TestLlmSeam:
    def _cand(self, a, b):
        return detect_conflicts([a, b])[0]

    def test_llm_none_never_called_for_rule_classes(self):
        pairs = [
            # SOURCE_AUTHORITY
            (fact("a", value="1", source_id="d1"), fact("b", value="2", source_id="d2"), None),
            # EVOLUTION
            (
                fact("a", value="1", source_id="d", version="v1"),
                fact("b", value="2", source_id="d", version="v2"),
                None,
            ),
            # EXTRACTION_ERROR
            (
                fact("a", value="1", source_id="d1"),
                fact("b", value="2", source_id="d2"),
                ClassifyContext(extraction_error_ids=frozenset({"a"})),
            ),
        ]
        for left, right, ctx in pairs:
            rec = resolve_conflict(
                self._cand(left, right),
                authority_rank=None,
                version_clock=CLOCK,
                llm=None,
                context=ctx,
            )
            assert rec.llm_called is False

    def test_llm_not_called_for_non_same_source_even_when_provided(self):
        calls = []

        def spy(_candidate):
            calls.append(1)
            return "x"

        a = fact("a", value="1", source_id="d1")
        b = fact("b", value="2", source_id="d2")
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank=None,
            version_clock=CLOCK,
            llm=spy,
        )
        assert rec.llm_called is False
        assert calls == []

    def test_llm_called_only_for_same_source_when_provided(self):
        calls = []

        def spy(_candidate):
            calls.append(1)
            return "x"

        a = fact("a", value="1", source_id="d")
        b = fact("b", value="2", source_id="d")
        rec = resolve_conflict(
            self._cand(a, b),
            authority_rank=None,
            version_clock=CLOCK,
            llm=spy,
        )
        assert rec.llm_called is True
        assert calls == [1]
        assert rec.resolution == RESOLUTION_LLM_SEAM
        assert rec.contested is True  # seam does not silently un-contest

    def test_same_source_without_llm_keeps_human_review(self):
        a = fact("a", value="1", source_id="d")
        b = fact("b", value="2", source_id="d")
        rec = resolve_conflict(
            self._cand(a, b), authority_rank=None, version_clock=CLOCK, llm=None
        )
        assert rec.llm_called is False
        assert rec.resolution == RESOLUTION_HUMAN_REVIEW


# ---------------------------------------------------------------------------
# 7. Determinism, order independence, empty input, batch reconcile
# ---------------------------------------------------------------------------


class TestReconcileAndDeterminism:
    def _pool(self):
        return [
            # SOURCE_AUTHORITY pair (own key)
            fact("s1", subject="e:aspirin", predicate="max_dose", value="100mg",
                 source_id="doc:label", authority=1, valid_at=T0, invalid_at=T4),
            fact("s2", subject="e:aspirin", predicate="max_dose", value="300mg",
                 source_id="doc:guide", authority=2, valid_at=T0, invalid_at=None),
            # EVOLUTION pair (same group, versions differ)
            fact("e1", subject="e:metformin", predicate="max_dose", value="low",
                 source_id="doc:g2020", source_group="g", version="2020",
                 valid_at=T0, invalid_at=T4),
            fact("e2", subject="e:metformin", predicate="max_dose", value="high",
                 source_id="doc:g2024", source_group="g", version="2024",
                 valid_at=T1, invalid_at=None),
            # SAME_SOURCE pair
            fact("m1", subject="e:warfarin", predicate="inr_target", value="x",
                 source_id="doc:note", valid_at=T0, invalid_at=None),
            fact("m2", subject="e:warfarin", predicate="inr_target", value="y",
                 source_id="doc:note", valid_at=T0, invalid_at=None),
            # EXTRACTION_ERROR pair
            fact("x1", subject="e:heparin", predicate="dose", value="p",
                 source_id="doc:a", valid_at=T0, invalid_at=None),
            fact("x2", subject="e:heparin", predicate="dose", value="q",
                 source_id="doc:b", valid_at=T0, invalid_at=None),
            # clean singleton on a fifth key
            fact("ok", subject="e:other", predicate="other", value="z",
                 source_id="doc:c", valid_at=T0, invalid_at=None),
        ]

    def test_empty_input(self):
        res = reconcile([], authority_rank=None, version_clock=CLOCK)
        assert res.n_conflicts == 0
        assert res.records == ()
        assert res.kind_counts == {k: 0 for k in CONFLICT_KINDS}
        assert res.superseded_fact_ids == ()
        assert res.n_contested == 0

    def test_batch_covers_all_four_kinds(self):
        pool = self._pool()
        ctx = ClassifyContext(extraction_error_ids=frozenset({"x2"}))
        res = reconcile(pool, authority_rank=None, version_clock=CLOCK, context=ctx)
        assert res.n_conflicts == 4
        assert res.kind_counts == {
            "EVOLUTION": 1,
            "SOURCE_AUTHORITY": 1,
            "EXTRACTION_ERROR": 1,
            "SAME_SOURCE": 1,
        }
        assert res.n_contested == 1  # only SAME_SOURCE
        # s2 loses on authority (s1 rank 1); e1 loses on version; m2 loses the
        # provisional id tie-break; x2 is the flagged extraction error.
        assert res.superseded_fact_ids == ("e1", "m2", "s2", "x2")

    def test_input_order_independence(self):
        pool = self._pool()
        ctx = ClassifyContext(extraction_error_ids=frozenset({"x2"}))
        forward = reconcile(pool, authority_rank=None, version_clock=CLOCK, context=ctx)
        backward = reconcile(
            list(reversed(pool)), authority_rank=None, version_clock=CLOCK, context=ctx
        )
        assert [c.conflict_id for c in forward.candidates] == [
            c.conflict_id for c in backward.candidates
        ]
        assert [r.replay_key for r in forward.records] == [
            r.replay_key for r in backward.records
        ]
        assert [(r.winner_id, r.loser_id) for r in forward.records] == [
            (r.winner_id, r.loser_id) for r in backward.records
        ]
        assert forward.kind_counts == backward.kind_counts

    def test_replay_key_stable_across_calls(self):
        pool = self._pool()
        ctx = ClassifyContext(extraction_error_ids=frozenset({"x2"}))
        r1 = reconcile(pool, authority_rank=None, version_clock=CLOCK, context=ctx)
        r2 = reconcile(pool, authority_rank=None, version_clock=CLOCK, context=ctx)
        assert [r.replay_key for r in r1.records] == [r.replay_key for r in r2.records]
        for rec in r1.records:
            assert len(rec.replay_key) == 16

    def test_records_follow_candidate_order(self):
        pool = self._pool()
        res = reconcile(pool, authority_rank=None, version_clock=CLOCK)
        assert [r.conflict_id for r in res.records] == [
            c.conflict_id for c in res.candidates
        ]


# ---------------------------------------------------------------------------
# 8. Fact validation (fail-fast at construction)
# ---------------------------------------------------------------------------


class TestFactValidation:
    def test_empty_fact_id_rejected(self):
        with pytest.raises(ValueError, match="fact_id"):
            Fact(fact_id="", fact_type="attribute", subject="s", predicate="p")

    def test_unknown_fact_type_rejected(self):
        with pytest.raises(ValueError, match="fact_type"):
            Fact(fact_id="a", fact_type="edge", subject="s", predicate="p")

    def test_relation_requires_object(self):
        with pytest.raises(ValueError, match="object"):
            Fact(fact_id="a", fact_type="relation", subject="s", predicate="p")

    def test_authority_level_must_be_positive_int(self):
        with pytest.raises(ValueError, match="authority_level"):
            Fact(
                fact_id="a",
                fact_type="attribute",
                subject="s",
                predicate="p",
                authority_level=0,
            )

    def test_naive_datetimes_are_utc(self):
        f = Fact(
            fact_id="a",
            fact_type="attribute",
            subject="s",
            predicate="p",
            valid_at=datetime(2024, 1, 1),  # noqa: DTZ001 - naive on purpose
            invalid_at=datetime(2024, 2, 1),  # noqa: DTZ001 - naive on purpose
        )
        assert f.valid_at.tzinfo is not None
        other = Fact(
            fact_id="b",
            fact_type="attribute",
            subject="s",
            predicate="p",
            value="x",
            valid_at=T0,
            invalid_at=T4,
        )
        assert windows_overlap(f, other) is True
