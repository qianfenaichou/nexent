"""Tests for the L5 community-summary kernel (community_summary).

Spec anchors: workspace design doc
``competition/docs/tech-optimization-2026-09-28/l5-community-summary-design-2026-09-29.md``
and the dual contract ``knowevo/backend/services/knowevo/community_summary.py.md``.

Semantics pinned here (every asserted float / hash literal was verified by
actually running the implementation first - : never back-fill
expected values from mental simulation):

- default clustering is deterministic greedy modularity (CNM-style) with
  lexicographic pair tie-breaks; community ids are min(member_ids);
- modularity Q = sum_c [ E_c/m - (K_c/(2m))^2 ] with E_c counted ONCE per
  undirected intra edge (the earlier //2 on _edges_between was a bug and
  is fixed; two disjoint triangles must yield Q = 0.5, not -0.166...);
- skeleton fingerprint is sha256 over canonical sorted JSON of the
  community payload and is stable across double runs;
- global route rank ties break by (-score, -size, community_id);
- entities_from_hits expands hit-rank then sorted members, then a bounded
  deterministic one-hop walk (depth <= 3, beam counted only on newly
  admitted nodes);
- validation: Community invariants, top_k/max_claims/top_n/depth/beam
  positive ints, depth <= 3, language in {zh, en}, empty communities
  without cluster_fn.
"""
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.community_summary import (  # noqa: E402
    DEFAULT_BEAM,
    DEFAULT_DEPTH,
    DEFAULT_TOP_ENTITIES,
    GLOBAL_ROUTE,
    GlobalHit,
    Community,
    build_llm_prompt,
    cluster_connected_components,
    cluster_greedy_modularity,
    cluster_lpa,
    entities_from_hits,
    global_route,
    modularity,
    render_skeleton_text,
    score_communities,
    skeleton_summary,
    tokenize,
)

# ---------------------------------------------------------------------------
# Shared fixture: two disjoint triangles (run-verified partition + hashes).
# ---------------------------------------------------------------------------

NODES = ["a", "b", "c", "d", "e", "f"]
EDGES = [
    {"src": "a", "dst": "b", "rel_type": "r1", "claim": "ab claim"},
    {"src": "b", "dst": "c", "rel_type": "r1", "claim": "bc claim"},
    {"src": "a", "dst": "c", "rel_type": "r2", "claim": "ac claim"},
    {"src": "d", "dst": "e", "rel_type": "r3", "claim": "de claim"},
    {"src": "e", "dst": "f", "rel_type": "r3", "claim": "ef claim"},
    {"src": "d", "dst": "f", "rel_type": "r1", "claim": "df claim"},
]
NAMES = {n: n.upper() for n in NODES}

# Two cliques + one bridge (LPA's known collapse limit; greedy keeps 2).
BRIDGE_EDGES = [
    {"src": "a", "dst": "b", "rel_type": "r", "claim": "1"},
    {"src": "b", "dst": "c", "rel_type": "r", "claim": "2"},
    {"src": "a", "dst": "c", "rel_type": "r", "claim": "3"},
    {"src": "d", "dst": "e", "rel_type": "r", "claim": "4"},
    {"src": "e", "dst": "f", "rel_type": "r", "claim": "5"},
    {"src": "d", "dst": "f", "rel_type": "r", "claim": "6"},
    {"src": "c", "dst": "d", "rel_type": "bridge", "claim": "bridge claim"},
]

FP_COMM_A = (
    "883d5294c728de3843d6b30b9714bb7cf62b8f687175eb003d49ddd74056e0de"
)
FP_COMM_D = (
    "43d3b8709c34a31524e1387be227ee73cf2305381408f820cbacdba07d70c7f6"
)


def two_triangles():
    return cluster_greedy_modularity(NODES, EDGES)


# ---------------------------------------------------------------------------
# 1. Determinism: double run + edge-order shuffle
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_double_run_partition_and_fingerprints_identical(self):
        def run():
            comms = two_triangles()
            skels = [skeleton_summary(c, names=NAMES, edges=EDGES) for c in comms]
            return (
                [(c.community_id, c.member_ids) for c in comms],
                [s.fingerprint for s in skels],
                [(h.community_id, h.score, h.rank) for h in score_communities(
                    "r1 ab claim", skels
                )],
            )

        assert run() == run()

    def test_edge_insertion_order_does_not_change_partition(self):
        base = two_triangles()
        shuffled = list(reversed(EDGES))
        alt = cluster_greedy_modularity(NODES, shuffled)
        assert [(c.community_id, c.member_ids) for c in alt] == [
            (c.community_id, c.member_ids) for c in base
        ]
        assert modularity(alt, node_ids=NODES, edges=shuffled) == modularity(
            base, node_ids=NODES, edges=EDGES
        )

    def test_global_route_double_run_identical(self):
        def run():
            r = global_route(
                "r1 ab claim",
                communities=two_triangles(),
                names=NAMES,
                edges=EDGES,
                top_n=2,
                depth=2,
                beam=2,
            )
            return r.route, r.entity_ids, [
                (h.community_id, h.score, h.rank) for h in r.hits
            ]

        assert run() == run()


# ---------------------------------------------------------------------------
# 2. Clustering + modularity (hand-computed, run-confirmed)
# ---------------------------------------------------------------------------


class TestClusteringAndModularity:
    def test_greedy_splits_two_triangles(self):
        comms = two_triangles()
        assert [(c.community_id, c.member_ids) for c in comms] == [
            ("a", ("a", "b", "c")),
            ("d", ("d", "e", "f")),
        ]

    def test_modularity_two_triangles_is_half(self):
        # E_c=3, K_c=6, m=6 per triangle -> (3/6 - (6/12)^2)*2 = 0.5
        comms = two_triangles()
        assert modularity(comms, node_ids=NODES, edges=EDGES) == 0.5

    def test_modularity_single_community_over_all_is_zero(self):
        one = [Community(community_id="a", member_ids=tuple(sorted(NODES)))]
        assert modularity(one, node_ids=NODES, edges=EDGES) == 0.0

    def test_modularity_empty_and_edgeless_are_zero(self):
        assert modularity([], node_ids=[], edges=[]) == 0.0
        only = [Community(community_id="p", member_ids=("p",))]
        assert modularity(only, node_ids=["p"], edges=[]) == 0.0

    def test_bridge_graph_greedy_keeps_two_communities(self):
        # Run-verified: Q = 5/14; greedy does NOT collapse across the bridge.
        comms = cluster_greedy_modularity(NODES, BRIDGE_EDGES)
        assert [(c.community_id, c.member_ids) for c in comms] == [
            ("a", ("a", "b", "c")),
            ("d", ("d", "e", "f")),
        ]
        assert modularity(comms, node_ids=NODES, edges=BRIDGE_EDGES) == (
            0.3571428571428571
        )

    def test_lpa_collapses_across_bridge_known_limit(self):
        # Documented honest limit: LPA merges the two cliques via the bridge.
        comms = cluster_lpa(NODES, BRIDGE_EDGES)
        assert [(c.community_id, c.member_ids) for c in comms] == [
            ("a", ("a", "b", "c", "d", "e", "f")),
        ]
        assert modularity(comms, node_ids=NODES, edges=BRIDGE_EDGES) == 0.0

    def test_connected_components_match_greedy_on_disjoint_triangles(self):
        cc = cluster_connected_components(NODES, EDGES)
        assert [(c.community_id, c.member_ids) for c in cc] == [
            (c.community_id, c.member_ids) for c in two_triangles()
        ]

    def test_isolated_nodes_are_singleton_communities(self):
        comms = cluster_greedy_modularity(["p", "q"], [])
        assert [(c.community_id, c.member_ids) for c in comms] == [
            ("p", ("p",)),
            ("q", ("q",)),
        ]

    def test_empty_graph_yields_no_communities(self):
        assert cluster_greedy_modularity([], []) == []

    def test_community_invariants_reject_bad_shapes(self):
        with pytest.raises(ValueError, match="member_ids must be non-empty"):
            Community(community_id="x", member_ids=())
        with pytest.raises(ValueError, match="sorted ascending"):
            Community(community_id="a", member_ids=("b", "a"))
        with pytest.raises(ValueError, match="min\\(member_ids\\)"):
            Community(community_id="b", member_ids=("a", "b"))


# ---------------------------------------------------------------------------
# 3. Skeleton summary + fingerprint
# ---------------------------------------------------------------------------


class TestSkeletonAndFingerprint:
    def test_skeleton_fields_run_verified(self):
        comms = two_triangles()
        skel = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        assert skel.community_id == "a"
        assert skel.size == 3
        assert skel.top_entities == (("a", "A", 2), ("b", "B", 2), ("c", "C", 2))
        assert skel.rel_type_counts == (("r1", 2), ("r2", 1))
        assert skel.bridge_claims == ("ab claim", "ac claim", "bc claim")

    def test_fingerprint_stable_and_run_verified(self):
        comms = two_triangles()
        skel_a = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        skel_d = skeleton_summary(comms[1], names=NAMES, edges=EDGES)
        assert skel_a.fingerprint == FP_COMM_A
        assert skel_d.fingerprint == FP_COMM_D
        # Rebuild -> identical digest.
        again = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        assert again.fingerprint == skel_a.fingerprint

    def test_fingerprint_changes_when_claim_changes(self):
        comms = two_triangles()
        base = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        mutated = list(EDGES)
        mutated[0] = {**EDGES[0], "claim": "AB CLAIM CHANGED"}
        other = skeleton_summary(comms[0], names=NAMES, edges=mutated)
        assert other.fingerprint != base.fingerprint

    def test_missing_name_falls_back_to_id(self):
        comms = two_triangles()
        skel = skeleton_summary(comms[0], names={}, edges=EDGES)
        assert skel.top_entities[0] == ("a", "a", 2)
        # Different names -> different fingerprint (names are in the payload).
        named = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        assert skel.fingerprint != named.fingerprint

    def test_render_skeleton_text_run_verified(self):
        comms = two_triangles()
        skel = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        assert render_skeleton_text(skel) == (
            "community=a ; size=3 ; top_entities=A(2), B(2), C(2) ; "
            "rel_types=r1:2, r2:1 ; claims=ab claim | ac claim | bc claim"
        )

    def test_searchable_text_contains_names_rels_claims(self):
        comms = two_triangles()
        skel = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        text = skel.searchable_text
        for token in ("A", "B", "C", "r1", "r2", "ab claim"):
            assert token in text

    def test_skeleton_validates_arguments(self):
        comms = two_triangles()
        with pytest.raises(TypeError, match="community must be Community"):
            skeleton_summary("x", names=NAMES, edges=EDGES)
        with pytest.raises(TypeError, match="names must be a Mapping"):
            skeleton_summary(comms[0], names=None, edges=EDGES)
        with pytest.raises(ValueError, match="top_k must be >= 1"):
            skeleton_summary(comms[0], names=NAMES, edges=EDGES, top_k=0)
        with pytest.raises(ValueError, match="max_claims must be >= 1"):
            skeleton_summary(comms[0], names=NAMES, edges=EDGES, max_claims=0)


# ---------------------------------------------------------------------------
# 4. Global route ranking + entity expansion (L6 graph-route shape)
# ---------------------------------------------------------------------------


class TestGlobalRouteRanking:
    def test_score_communities_orders_by_overlap_then_size(self):
        comms = two_triangles()
        skels = [skeleton_summary(c, names=NAMES, edges=EDGES) for c in comms]
        hits = score_communities("r1 ab claim", skels, top_n=3)
        assert [(h.community_id, h.score, h.rank) for h in hits] == [
            ("a", 3, 1),
            ("d", 2, 2),
        ]
        assert hits[0].matched_terms == ("ab", "claim", "r1")
        assert hits[1].matched_terms == ("claim", "r1")

    def test_rank_tie_breaks_by_size_then_community_id(self):
        big = Community(community_id="a", member_ids=("a", "b"))
        small = Community(community_id="x", member_ids=("x",))
        e2 = [{"src": "a", "dst": "b", "rel_type": "zz", "claim": "zz"}]
        s_big = skeleton_summary(big, names={"a": "alpha", "b": "beta"}, edges=e2)
        s_small = skeleton_summary(small, names={"x": "alpha"}, edges=[])
        hits = score_communities("alpha", [s_small, s_big], top_n=2)
        # Both score 1; larger community first; input order must not matter.
        assert [(h.community_id, h.score, h.rank) for h in hits] == [
            ("a", 1, 1),
            ("x", 1, 2),
        ]

    def test_zero_score_tie_still_larger_size_first(self):
        big = Community(community_id="a", member_ids=("a", "b"))
        small = Community(community_id="x", member_ids=("x",))
        e2 = [{"src": "a", "dst": "b", "rel_type": "zz", "claim": "zz"}]
        s_big = skeleton_summary(big, names={}, edges=e2)
        s_small = skeleton_summary(small, names={}, edges=[])
        hits = score_communities("qqqq", [s_big, s_small], top_n=2)
        assert [h.community_id for h in hits] == ["a", "x"]

    def test_entities_from_hits_seed_order_then_bounded_expansion(self):
        # Hit rank 1 community x seeds first; rank 2 community a,b next.
        c_ab = Community(community_id="a", member_ids=("a", "b"))
        c_x = Community(community_id="x", member_ids=("x",))
        h_x = GlobalHit(community_id="x", score=1, matched_terms=("alpha",), rank=1)
        h_a = GlobalHit(community_id="a", score=1, matched_terms=("alpha",), rank=2)
        e2 = [{"src": "a", "dst": "b", "rel_type": "r", "claim": "c"}]
        ids = entities_from_hits([h_x, h_a], [c_ab, c_x], edges=e2, depth=1, beam=1)
        assert ids == ("x", "a", "b")

    def test_entities_from_hits_beam_counts_only_new_nodes(self):
        # a-b already both in the community; the walk admits zz (new) and
        # skips b (already ordered) without consuming beam.
        c_ab = Community(community_id="a", member_ids=("a", "b"))
        h_a = GlobalHit(community_id="a", score=1, matched_terms=(), rank=1)
        e3 = [
            {"src": "a", "dst": "b", "rel_type": "r", "claim": "c"},
            {"src": "a", "dst": "zz", "rel_type": "r", "claim": "c"},
        ]
        ids = entities_from_hits([h_a], [c_ab], edges=e3, depth=1, beam=1)
        assert ids == ("a", "b", "zz")

    def test_entities_from_hits_without_edges_returns_seed_only(self):
        comms = two_triangles()
        skels = [skeleton_summary(c, names=NAMES, edges=EDGES) for c in comms]
        hits = score_communities("r1 ab claim", skels, top_n=1)
        ids = entities_from_hits(hits, comms, edges=(), depth=3, beam=3)
        assert ids == ("a", "b", "c")

    def test_global_route_end_to_end_run_verified(self):
        r = global_route(
            "r1 ab claim",
            communities=two_triangles(),
            names=NAMES,
            edges=EDGES,
            top_n=2,
            depth=2,
            beam=2,
        )
        assert r.route == GLOBAL_ROUTE == "G"
        assert [(h.community_id, h.score, h.rank) for h in r.hits] == [
            ("a", 3, 1),
            ("d", 2, 2),
        ]
        assert r.entity_ids == ("a", "b", "c", "d", "e", "f")
        assert (r.depth, r.beam) == (2, 2)

    def test_global_route_clusters_when_communities_empty(self):
        r = global_route(
            "r1 ab claim",
            communities=(),
            names=NAMES,
            edges=EDGES,
            top_n=1,
            depth=1,
            beam=1,
            cluster_fn=cluster_greedy_modularity,
        )
        assert [(h.community_id, h.rank) for h in r.hits] == [("a", 1)]
        assert r.entity_ids == ("a", "b", "c")

    def test_global_route_requires_communities_or_cluster_fn(self):
        with pytest.raises(ValueError, match="cluster_fn not provided"):
            global_route(
                "q", communities=(), names=NAMES, edges=EDGES
            )

    def test_route_constants_are_additive_only(self):
        # GLOBAL_ROUTE is a new additive value; the existing R/M/RM set is
        # untouched (schemas.py itself is a wiring file, not edited here).
        assert GLOBAL_ROUTE == "G"
        assert DEFAULT_DEPTH == 3
        assert DEFAULT_BEAM == 3
        assert DEFAULT_TOP_ENTITIES == 5


# ---------------------------------------------------------------------------
# 5. LLM prompt protocol stub (no model call)
# ---------------------------------------------------------------------------


class TestLlmPromptStub:
    def test_prompt_contains_skeleton_and_rules(self):
        comms = two_triangles()
        skel = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        prompt = build_llm_prompt(skel, question="what binds ab?", language="zh")
        assert prompt.startswith(
            "You are summarizing ONE knowledge-graph community"
        )
        assert "question=what binds ab?" in prompt
        assert "community=a ; size=3" in prompt
        assert "respond in Chinese." in prompt
        assert "ground every claim in the skeleton" in prompt
        assert "invent entities, numbers, or relations" in prompt

    def test_prompt_omits_question_line_when_none(self):
        comms = two_triangles()
        skel = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        prompt = build_llm_prompt(skel, language="en")
        assert "question=" not in prompt
        assert "respond in English." in prompt

    def test_prompt_language_validation(self):
        comms = two_triangles()
        skel = skeleton_summary(comms[0], names=NAMES, edges=EDGES)
        with pytest.raises(ValueError, match="language must be"):
            build_llm_prompt(skel, language="fr")
        with pytest.raises(TypeError, match="skel must be SkeletonSummary"):
            build_llm_prompt("x", language="zh")


# ---------------------------------------------------------------------------
# 6. Tokenizer + validation of route helpers
# ---------------------------------------------------------------------------


class TestTokenizeAndValidation:
    def test_tokenize_ascii_and_cjk_run_verified(self):
        assert tokenize("Hello world 你好世界") == (
            "hello",
            "world",
            "你",
            "你好",
            "好",
            "好世",
            "世",
            "世界",
            "界",
        )

    def test_tokenize_rejects_non_str(self):
        with pytest.raises(TypeError, match="text must be str"):
            tokenize(123)

    def test_score_communities_validates(self):
        comms = two_triangles()
        skels = [skeleton_summary(c, names=NAMES, edges=EDGES) for c in comms]
        with pytest.raises(TypeError, match="query must be str"):
            score_communities(123, skels)
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            score_communities("q", skels, top_n=0)
        with pytest.raises(TypeError, match="SkeletonSummary"):
            score_communities("q", ["not-a-skel"])

    def test_entities_from_hits_validates(self):
        comms = two_triangles()
        hits = [
            GlobalHit(community_id="a", score=1, matched_terms=(), rank=1)
        ]
        with pytest.raises(ValueError, match="depth must be >= 1"):
            entities_from_hits(hits, comms, edges=EDGES, depth=0)
        with pytest.raises(ValueError, match="depth must be <= 3"):
            entities_from_hits(hits, comms, edges=EDGES, depth=9)
        with pytest.raises(TypeError, match="GlobalHit"):
            entities_from_hits(["x"], comms)
        with pytest.raises(TypeError, match="Community"):
            entities_from_hits(hits, ["x"])

    def test_global_route_validates_top_n(self):
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            global_route(
                "q",
                communities=two_triangles(),
                names=NAMES,
                edges=EDGES,
                top_n=0,
            )
