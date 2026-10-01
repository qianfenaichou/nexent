"""Tests for the L6 three-way RRF fusion kernel (services/knowevo/rrf_fusion.py).

Spec anchors: competition/docs/tech-optimization-2026-09-28/
l6-es-rrf-design-2026-09-29.md and knowevo/backend/services/knowevo/
rrf_fusion.py.md. Formula is Cormack et al. 2009 reciprocal rank fusion:

    score(d) = sum over lists of 1 / (k + rank(d in that list))

with 1-based ranks. Expected values below are derived from THAT formula
(independent rational arithmetic), never from mental simulation of the
implementation.

Seams under test (public only):
  * fuse(three_lists, k=...)          - the fusion kernel
  * retrieve_three_way(query, ...)    - injected-retriever orchestration seam

Type errors raise TypeError, bad values raise ValueError (ruff TRY004).
"""
from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.rrf_fusion import (  # noqa: E402
    DEFAULT_RRF_K,
    FusedHit,
    fuse,
    retrieve_three_way,
)


def ids_of(hits):
    return [h.id for h in hits]


class TestHandWorkedExample:
    """One small worked example, scores as exact rationals from the spec."""

    def test_three_list_order_and_scores(self):
        # list0: A, B      list1: B, C      list2: empty
        # A: 1/61 ; B: 1/62 + 1/61 = 123/3782 ; C: 1/62
        # B > A > C
        hits = fuse([["A", "B"], ["B", "C"], []], k=60)
        assert ids_of(hits) == ["B", "A", "C"]
        by_id = {h.id: h for h in hits}
        assert by_id["A"].score == pytest.approx(float(Fraction(1, 61)))
        assert by_id["B"].score == pytest.approx(float(Fraction(123, 3782)))
        assert by_id["C"].score == pytest.approx(float(Fraction(1, 62)))
        assert by_id["A"].ranks == (1, None, None)
        assert by_id["B"].ranks == (2, 1, None)
        assert by_id["C"].ranks == (None, 2, None)
        assert by_id["A"].best_rank == 1
        assert by_id["B"].best_rank == 1
        assert by_id["C"].best_rank == 2

    def test_default_k_is_60(self):
        assert DEFAULT_RRF_K == 60
        a = fuse([["A"]])
        b = fuse([["A"]], k=60)
        assert a == b


class TestDegenerateLists:
    def test_empty_outer_returns_empty(self):
        assert fuse([]) == []

    def test_all_lists_empty_returns_empty(self):
        assert fuse([[], [], []]) == []

    def test_single_list_preserves_rank_order(self):
        hits = fuse([["a", "b", "c"]], k=60)
        assert ids_of(hits) == ["a", "b", "c"]
        assert [h.score for h in hits] == pytest.approx(
            [float(Fraction(1, 61)), float(Fraction(1, 62)),
             float(Fraction(1, 63))]
        )

    def test_two_lists_with_one_empty(self):
        hits = fuse([["a", "b"], []], k=60)
        assert ids_of(hits) == ["a", "b"]

    def test_shared_hit_beats_single_list_hit(self):
        # B appears in both lists -> 1/62+1/61 > 1/61 (A, list0 rank1 only)
        hits = fuse([["A", "B"], ["B"]], k=60)
        assert ids_of(hits) == ["B", "A"]


class TestDeterminism:
    def test_tie_breaks_by_best_rank_then_id(self):
        # k=0 so 1/rank is the contribution. X: rank 2 alone -> 1/2.
        # Y: rank 3 + rank 6 -> 1/3+1/6 = 1/2. Equal score; X best_rank=2
        # beats Y best_rank=3. Dummy ids sit at the other ranks so the
        # construction does not need empty slots.
        hits = fuse(
            [["d0", "X", "Y"], ["d1", "d2", "d3", "d4", "d5", "Y"]], k=0
        )
        order = ids_of(hits)
        assert order.index("X") < order.index("Y")
        by_id = {h.id: h for h in hits}
        assert by_id["X"].score == pytest.approx(0.5)
        assert by_id["Y"].score == pytest.approx(0.5)
        assert by_id["X"].best_rank == 2
        assert by_id["Y"].best_rank == 3

    def test_equal_score_equal_best_rank_breaks_by_id(self):
        # z: ranks (2, 3); a: ranks (3, 2). score 1/62+1/63 both; best 2 both.
        # p and q sit at rank 1 of one list each (score 1/61, below a/z).
        hits = fuse([["p", "z", "a"], ["q", "a", "z"]], k=60)
        order = ids_of(hits)
        assert order[:2] == ["a", "z"]  # id tie-break after equal score
        assert order[2:] == ["p", "q"]
        assert hits[0].score == pytest.approx(hits[1].score)
        assert hits[0].best_rank == hits[1].best_rank == 2

    def test_list_permutation_preserves_ids_and_scores(self):
        # RRF is symmetric in the lists: permuting slots moves the ranks
        # tuple but not the fused order or the scores.
        lists = [["A", "B"], ["B", "C"], ["C", "A"]]
        base = fuse(lists)
        permuted = fuse([lists[2], lists[0], lists[1]])
        assert ids_of(base) == ids_of(permuted)
        assert [h.score for h in base] == pytest.approx(
            [h.score for h in permuted]
        )
        # ranks tuple tracks the slot order, so it changes under permutation
        by_id_base = {h.id: h for h in base}
        by_id_perm = {h.id: h for h in permuted}
        assert by_id_base["A"].ranks == (1, None, 2)
        assert by_id_perm["A"].ranks == (2, 1, None)

    def test_same_input_twice_identical(self):
        lists = [["A", "B", "C"], ["B", "C"], ["C"]]
        assert fuse(lists) == fuse(lists)


class TestDuplicateIds:
    def test_duplicate_within_list_keeps_best_rank_only(self):
        # X at rank 1 and rank 3 in list0: only 1/(k+1) counts, not 1/4+1/6.
        hits = fuse([["X", "Y", "X"]], k=60)
        by_id = {h.id: h for h in hits}
        assert by_id["X"].score == pytest.approx(float(Fraction(1, 61)))
        assert by_id["X"].ranks == (1,)
        assert by_id["Y"].score == pytest.approx(float(Fraction(1, 62)))

    def test_duplicate_across_lists_sums_contributions(self):
        hits = fuse([["X"], ["X"], ["X"]], k=60)
        assert len(hits) == 1
        assert hits[0].score == pytest.approx(3 * float(Fraction(1, 61)))
        assert hits[0].ranks == (1, 1, 1)

    def test_first_seen_payload_is_kept(self):
        hits = fuse([[{"id": "X", "tag": "first"}], [{"id": "X", "tag": "second"}]])
        assert hits[0].first_seen["tag"] == "first"


class TestIdExtraction:
    def test_bare_string_ids(self):
        assert ids_of(fuse([["a"]])) == ["a"]

    def test_dict_id_key(self):
        assert ids_of(fuse([[{"id": "a"}]])) == ["a"]

    def test_dict_stable_id_key(self):
        assert ids_of(fuse([[{"stable_id": "a"}]])) == ["a"]

    def test_object_id_attr(self):
        class Card:
            def __init__(self, i):
                self.id = i

        assert ids_of(fuse([[Card("a")]])) == ["a"]

    def test_object_stable_id_attr(self):
        class Card:
            def __init__(self, i):
                self.stable_id = i

        assert ids_of(fuse([[Card("a")]])) == ["a"]

    def test_id_preferred_over_stable_id(self):
        assert ids_of(fuse([[{"id": "a", "stable_id": "b"}]])) == ["a"]

    def test_id_none_falls_back_to_stable_id_mapping(self):
        """id=None is unset, not a bad id -> fall back (object path parity)."""
        assert ids_of(fuse([[{"id": None, "stable_id": "ok"}]])) == ["ok"]

    def test_id_none_falls_back_to_stable_id_object(self):
        class Card:
            def __init__(self):
                self.id = None
                self.stable_id = "ok"
        assert ids_of(fuse([[Card()]])) == ["ok"]

    def test_non_str_id_with_fallback_still_prefers_then_type_errors(self):
        """id=123 is present but non-str: TypeError, no silent fallback."""
        with pytest.raises(TypeError):
            fuse([[{"id": 123, "stable_id": "ok"}]])

    def test_missing_id_raises(self):
        with pytest.raises(TypeError):
            fuse([[{"title": "no id"}]])

    def test_non_str_id_raises(self):
        with pytest.raises(TypeError):
            fuse([[{"id": 123}]])

    def test_empty_id_raises(self):
        with pytest.raises(ValueError):
            fuse([[""]])


class TestValidation:
    def test_negative_k_raises(self):
        with pytest.raises(ValueError):
            fuse([["a"]], k=-1)

    def test_bool_k_raises(self):
        with pytest.raises(TypeError):
            fuse([["a"]], k=True)

    def test_non_numeric_k_raises(self):
        with pytest.raises(TypeError):
            fuse([["a"]], k="60")

    def test_inner_list_as_str_raises(self):
        # a bare string is a Sequence but not a ranked list of hits
        with pytest.raises(TypeError):
            fuse(["abc"])

    def test_k_zero_is_allowed(self):
        hits = fuse([["a", "b"]], k=0)
        assert hits[0].score == pytest.approx(1.0)
        assert hits[1].score == pytest.approx(0.5)


class TestFusedHitShape:
    def test_fields(self):
        hit = fuse([["a"]])[0]
        assert isinstance(hit, FusedHit)
        assert hit.id == "a"
        assert isinstance(hit.score, float)
        assert hit.ranks == (1,)
        assert hit.best_rank == 1
        assert hit.first_seen == "a"

    def test_ranks_length_matches_n_lists(self):
        hit = fuse([["a"], [], ["a"], []])[0]
        assert hit.ranks == (1, None, 1, None)


class TestRetrieveThreeWay:
    def test_calls_all_three_and_fuses(self):
        calls = []

        def bm25(q):
            calls.append(("bm25", q))
            return ["A", "B"]

        def dense(q):
            calls.append(("dense", q))
            return ["B", "C"]

        def graph(q):
            calls.append(("graph", q))
            return ["C"]

        hits = retrieve_three_way("q", bm25=bm25, dense=dense, graph=graph, k=60)
        assert calls == [("bm25", "q"), ("dense", "q"), ("graph", "q")]
        assert ids_of(hits) == ["B", "C", "A"]

    def test_one_retriever_empty_is_fine(self):
        hits = retrieve_three_way(
            "q",
            bm25=lambda q: ["A"],
            dense=lambda q: [],
            graph=lambda q: ["A", "B"],
        )
        assert ids_of(hits) == ["A", "B"]

    def test_k_forwarded(self):
        hits = retrieve_three_way(
            "q", bm25=lambda q: ["A"], dense=lambda q: [], graph=lambda q: [],
            k=0,
        )
        assert hits[0].score == pytest.approx(1.0)
