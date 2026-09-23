"""
Unit tests for services/knowevo/decision_service.py (T-09) and the
version-pinned walk it drives - routing, beam search with a fake store,
evidence-chain fusion, decision-card rendering, calibration, refusal.

Layer 1 (always runs): everything except the two persistence methods that
touch decision_card_t. The LLM is injected as a fake async callable
matching the llm_client contract, and the store is an in-memory fake that
honours ``as_of`` the way PgJsonbGraphStore does - which is what lets the
pinned/unpinned ablation be tested without Postgres.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): persist to decision_card_t, and
rerun_marked recording the old-vs-new conclusion diff (Q2 ledger material).
"""
import json
import os
import sys
import time
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from pydantic import ValidationError

from services.knowevo.decision_service import (
    HEALTHCARE_DISCLAIMER,
    LOOKUP_RULES,
    PROBE_FAILED,
    REASONING_LATENCY_BUDGET_MS,
    ROUTER_EXAMPLES,
    VERSION_COMPARE_WORDS,
    DecisionService,
    _overlap,
    _parse_json,
    _question_id,
    _signature,
)
from services.knowevo.graph_store import EdgeCard, Subgraph
from services.knowevo.schemas import (
    CHANNEL_DOC,
    CHANNEL_KG,
    CHANNEL_KG_DOC,
    DECISION_INSUFFICIENT,
    DECISION_RECOMMEND,
    ROUTE_BOTH,
    ROUTE_REASONING,
    ROUTE_RETRIEVAL,
    DecisionCard,
    DocHit,
    EvidenceChain,
    EvidenceItem,
    KnowledgeStamp,
    Provenance,
)
from services.knowevo.version_pin import VersionClock

T_V = datetime(2025, 1, 1, tzinfo=UTC)
BEFORE = datetime(2024, 1, 1, tzinfo=UTC)
AFTER = datetime(2026, 1, 1, tzinfo=UTC)
TENANT = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    """Async callable matching the frozen llm contract.

    ``replies`` maps a substring of the prompt to the text to return, so a
    test can route the same service through different scripted answers
    (router vs card) without knowing call order.
    """

    def __init__(self, replies=None, default="{}"):
        self.replies = replies or {}
        self.default = default
        self.calls: list[dict] = []

    async def __call__(self, prompt, *, kind, tier="mid", temperature=0.0):
        self.calls.append({"prompt": prompt, "kind": kind, "tier": tier,
                           "temperature": temperature})
        for needle, text in self.replies.items():
            if needle in prompt:
                return text
        return self.default


class FakeStore:
    """In-memory graph with the as_of semantics of PgJsonbGraphStore.

    Seeded as (src, dst, rel_type, claim, valid_at, invalid_at); a
    neighborhood query honours the cutoff exactly as the SQL predicate
    does, so the service's pinned walk is exercised for real.
    """

    def __init__(self, edges=None):
        self.edges: list[EdgeCard] = []
        for idx, (src, dst, rel_type, claim, valid_at, invalid_at) in enumerate(
                edges or []):
            self.edges.append(EdgeCard(
                id=f"edge-{idx}", src=src, dst=dst, rel_type=rel_type,
                claim=claim, props={"evidence_id": f"ev-{idx}"},
                valid_at=valid_at, invalid_at=invalid_at))
        self.queries: list[datetime | None] = []

    async def neighbors(self, tenant_id, entity_ids, rel_types=None,
                        hop=1, valid_view=True, as_of=None):
        self.queries.append(as_of)
        frontier = list(entity_ids)
        seen = set(entity_ids)
        out: list[EdgeCard] = []
        for _ in range(max(1, hop)):
            nxt: list[str] = []
            for edge in self.edges:
                if edge.src not in frontier and edge.dst not in frontier:
                    continue
                if rel_types and edge.rel_type not in rel_types:
                    continue
                if valid_view and as_of is not None:
                    if edge.valid_at is not None and edge.valid_at > as_of:
                        continue
                    if edge.invalid_at is not None and edge.invalid_at <= as_of:
                        continue
                elif valid_view:
                    if edge.invalid_at is not None:
                        continue
                if edge.src not in seen or edge.dst not in seen:
                    out.append(edge)
                for sid in (edge.src, edge.dst):
                    if sid not in seen:
                        seen.add(sid)
                        nxt.append(sid)
            frontier = nxt
        return Subgraph(entities=[], edges=out)


def _chain_with_claim(claim="eGFR 45 时 SGLT2i 仍可起始", pinned=True,
                      channel=CHANNEL_KG, doc="指南2024版"):
    chain = EvidenceChain()
    chain.items.append(EvidenceItem(
        claim=claim,
        provenance=Provenance(doc=doc, span="§9.2 用药",
                              kg_path=["Drug:sglt2i", "CKD_G3a"],
                              version_pinned=pinned),
        tag="EXTRACTED", source_channel=channel))
    return chain


CARD_JSON = """{
  "candidates": [{
    "option": "首选 SGLT2i（恩格列净）",
    "score": 0.82, "confidence_calibrated": 0.9,
    "evidence_chain": [{
      "claim": "eGFR 45 时 SGLT2i 仍可起始",
      "provenance": {"doc": "指南2024版", "span": "§9.2 用药",
                     "kg_path": ["Drug:sglt2i -> CKD_G3a"],
                     "version_pinned": true},
      "tag": "EXTRACTED", "source_channel": "kg+doc"}],
    "risks": ["eGFR 持续下降至<30 需停用"],
    "counterfactual": {"not_choose": "若选二甲双胍常规剂量：风险↑",
                       "tag": "INFERRED"}},
   {"option": "首选二甲双胍减量",
    "score": 0.5, "confidence_calibrated": 0.4,
    "evidence_chain": [], "risks": [],
    "counterfactual": {"not_choose": "不应为 top-1 生成", "tag": "INFERRED"}}],
  "decision": "RECOMMEND",
  "conflict_adjudications": [{"conflict_id": "c1", "type": "guideline_vs_label",
                              "resolution": "以指南为准并标注"}],
  "uncertainty_notes": ["说明书剂量与指南表述存在差异"]
}"""


# ---------------------------------------------------------------------------
# Routing (02-tech-plan 3.1)
# ---------------------------------------------------------------------------

class TestRouting:
    def test_lookup_rule_hit_is_retrieval(self):
        svc = DecisionService()
        route = svc.route("二甲双胍是哪一类降糖药？")
        assert route.route == ROUTE_RETRIEVAL
        assert route.level == "L1" and route.confidence == 1.0

    def test_every_lookup_rule_has_a_pattern_and_a_name(self):
        assert len(LOOKUP_RULES) == 6, "memo 04-K3 freezes six rules"
        for rule in LOOKUP_RULES:
            assert rule["name"] and rule["pattern"]

    def test_lookup_rule_vetoed_by_multihop_marker(self):
        # "首选是什么" matches a lookup rule; "以及为什么" makes it plainly
        # multi-hop, and the veto is what keeps L1 from being greedy.
        svc = DecisionService()
        route = svc.route("一线治疗首选是什么，以及为什么？")
        assert route.route == ROUTE_BOTH
        assert route.level == "L3"

    def test_version_comparison_wording_is_rm(self):
        svc = DecisionService()
        route = svc.route("2024版指南的HbA1c目标与2020版相比有何变化？")
        assert route.route == ROUTE_BOTH and route.level == "L1"

    def test_version_wording_beats_a_lookup_rule(self):
        # Contains "是什么" (a lookup rule) *and* "最新版": version wins.
        svc = DecisionService()
        route = svc.route("最新版的诊断标准是什么？")
        assert route.route == ROUTE_BOTH

    def test_undecided_without_llm_defaults_to_rm(self):
        svc = DecisionService(llm=None)
        route = svc.route("SGLT2抑制剂和GLP-1哪个更适合这位患者？")
        assert route.route == ROUTE_BOTH and route.level == "L3"

    def test_signature_failure_escalates_to_rm(self):
        svc = DecisionService()
        q = "二甲双胍是哪一类降糖药？"
        svc.route_hit_feedback(q, ROUTE_RETRIEVAL, correct=False)
        svc.route_hit_feedback(q, ROUTE_RETRIEVAL, correct=False)
        route = svc.route(q)
        assert route.level == "L3" and "escalate" in route.reason

    def test_single_failure_does_not_escalate(self):
        svc = DecisionService()
        q = "二甲双胍是哪一类降糖药？"
        svc.route_hit_feedback(q, ROUTE_RETRIEVAL, correct=False)
        assert svc.route(q).route == ROUTE_RETRIEVAL

    def test_route_hit_rate_aggregates_feedback(self):
        svc = DecisionService()
        svc.route_hit_feedback("q1", ROUTE_RETRIEVAL, correct=True)
        svc.route_hit_feedback("q1", ROUTE_RETRIEVAL, correct=True)
        svc.route_hit_feedback("q2", ROUTE_BOTH, correct=False)
        assert svc.route_hit_rate() == pytest.approx(2 / 3)

    def test_route_hit_rate_empty_is_zero(self):
        assert DecisionService().route_hit_rate() == 0.0

    @pytest.mark.asyncio
    async def test_async_router_uses_l2_classification(self):
        llm = FakeLLM(replies={"route": '{"route": "M", "confidence": 0.91, '
                                       '"reason": "机制题"}'})
        svc = DecisionService(llm=llm)
        route = await svc.route_async("SGLT2抑制剂和GLP-1哪个更适合？")
        assert route.route == ROUTE_REASONING and route.level == "L2"
        assert llm.calls and llm.calls[0]["kind"] == "route_llm"
        assert llm.calls[0]["tier"] == "small"

    @pytest.mark.asyncio
    async def test_async_router_low_confidence_defaults_to_rm(self):
        llm = FakeLLM(replies={"route": '{"route": "R", "confidence": 0.4}'})
        svc = DecisionService(llm=llm)
        route = await svc.route_async("这个药怎么样？")
        assert route.route == ROUTE_BOTH and route.level == "L3"
        assert "0.4" in route.reason

    @pytest.mark.asyncio
    async def test_async_router_survives_llm_failure(self):
        class Boom(FakeLLM):
            async def __call__(self, *a, **k):
                raise RuntimeError("provider down")

        svc = DecisionService(llm=Boom())
        route = await svc.route_async("这个药怎么样？")
        assert route.route == ROUTE_BOTH and route.level == "L3"

    @pytest.mark.asyncio
    async def test_async_router_rejects_unknown_label(self):
        llm = FakeLLM(replies={"route": '{"route": "X", "confidence": 0.99}'})
        route = await DecisionService(llm=llm).route_async("这个药怎么样？")
        assert route.route == ROUTE_BOTH

    def test_router_examples_cover_all_three_routes(self):
        assert {ex["route"] for ex in ROUTER_EXAMPLES} == {
            ROUTE_RETRIEVAL, ROUTE_REASONING, ROUTE_BOTH}

    def test_version_words_list_is_not_empty(self):
        assert len(VERSION_COMPARE_WORDS) >= 10


# ---------------------------------------------------------------------------
# Version-pinned beam search (B2)
# ---------------------------------------------------------------------------

def _pinned_store():
    """a -> b -> c, with the a->b edge superseded at T_V.

    Seeded so the pinned and unpinned walks must disagree:
      a ->b valid [BEFORE, T_V)   (expired by the cutoff)
      b ->c valid [BEFORE, None)  (still in force)
      a ->d valid [AFTER, None)   (does not exist yet at the cutoff)
    """
    return FakeStore([
        ("Drug:a", "Disease:b", "treats", "旧版：a 治疗 b", BEFORE, T_V),
        ("Disease:b", "Drug:c", "contraindicates", "b 与 c 有禁忌",
         BEFORE, None),
        ("Drug:a", "Drug:d", "replaced_by", "新版：a 已被 d 取代",
         AFTER, None),
    ])


class TestVersionPinnedWalk:
    @pytest.mark.asyncio
    async def test_pinned_walk_excludes_expired_edge(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"],
                                     version="v1.0.0", as_of=T_V, depth=2)
        claims = [c for p in result.paths for c in p.claims]
        assert not any("旧版" in c for c in claims), (
            "an edge invalidated at the cutoff must not appear in a pinned "
            "walk")
        assert not any("新版" in c for c in claims), (
            "an edge that only becomes valid after the cutoff must not "
            "appear either")

    @pytest.mark.asyncio
    async def test_unpinned_walk_sees_the_future_edge(self):
        """The ablation counterpart: without pinning the walk crosses into
        facts that did not exist at the requested version."""
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"],
                                     pin_version=False, depth=1)
        claims = [c for p in result.paths for c in p.claims]
        assert any("新版" in c for c in claims)

    @pytest.mark.asyncio
    async def test_expired_paths_are_flagged_not_dropped(self):
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "旧版：a 治疗 b", BEFORE, T_V),
        ])
        svc = DecisionService(store=store, tenant_id=TENANT)
        result = await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"],
                                     version="v1.0.0", as_of=T_V, depth=1)
        assert result.paths == []
        assert result.failed, "expired paths must survive as counterfactual"
        assert result.failed[0].invalid_edge_reason
        assert "v1.0.0" in result.failed[0].invalid_edge_reason

    @pytest.mark.asyncio
    async def test_pin_is_pushed_into_the_store_query(self):
        store = _pinned_store()
        svc = DecisionService(store=store, tenant_id=TENANT)
        await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"],
                            version="v1.0.0", as_of=T_V, depth=1)
        assert store.queries and store.queries[0] == T_V, (
            "the version predicate must reach SQL, not be applied only "
            "after the fact")

    @pytest.mark.asyncio
    async def test_store_without_as_of_support_degrades_to_postfilter(self):
        class OldStore:
            def __init__(self, inner):
                self.inner = inner

            async def neighbors(self, tenant_id, entity_ids, rel_types=None,
                                hop=1, valid_view=True):
                return await self.inner.neighbors(
                    tenant_id, entity_ids, rel_types=rel_types, hop=hop,
                    valid_view=valid_view, as_of=None)

        svc = DecisionService(store=OldStore(_pinned_store()),
                              tenant_id=TENANT)
        result = await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"],
                                     version="v1.0.0", as_of=T_V, depth=1)
        assert result.paths == [], (
            "without store support the path-level re-verification must "
            "still exclude the expired edge")

    @pytest.mark.asyncio
    async def test_no_seeds_returns_empty(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("？", seeds=[], depth=3)
        assert result.paths == [] and result.scored == []

    @pytest.mark.asyncio
    async def test_no_store_returns_empty(self):
        svc = DecisionService(store=None, tenant_id=TENANT)
        result = await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"])
        assert result.paths == []

    @pytest.mark.asyncio
    async def test_walk_stops_early_once_a_claim_matches_the_question(self):
        """Early termination, stated as the behaviour it is.

        The chain test asserts the walk reaches the end of the chain; that
        only holds while no intermediate claim covers the question's
        content words. This is the other half: when the first hop's claim
        already answers the question, the walk stops there rather than
        paying for more hops (02-tech-plan 3.2 answerable test).
        """
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "a 治疗什么的问题", BEFORE, None),
            ("Disease:b", "Drug:c", "contraindicates", "b 与 c 有禁忌",
             BEFORE, None)])
        svc = DecisionService(store=store, tenant_id=TENANT, max_depth=3)
        result = await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"],
                                     depth=3, pin_version=False)
        assert result.early_stopped is True
        longest = max((len(p.entities) for p in result.paths), default=0)
        assert longest == 2, "the walk answered at hop 1 and stopped there"

    @pytest.mark.asyncio
    async def test_depth_is_clamped_to_max(self):
        # A fully current four-entity chain, so the clamp is the only thing
        # that can shorten the walk (the question shares no content words
        # with any claim, so the answerable early stop never fires).
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "P1", BEFORE, None),
            ("Disease:b", "Drug:c", "contraindicates", "P2", BEFORE, None),
            ("Drug:c", "Drug:e", "replaced_by", "P3", BEFORE, None)])
        svc = DecisionService(store=store, tenant_id=TENANT, max_depth=3)
        result = await svc.multi_hop("zzz", seeds=["Drug:a"],
                                     depth=99, pin_version=False)
        longest = max((len(p.entities) for p in result.paths), default=0)
        # Equality, not an upper bound: a clamp to 3 must actually reach 4
        # entities on a three-edge chain. "<= max+1" is satisfied by a walk
        # that returns nothing at all - the pitfall #24 defect class where a
        # vacuous pass looks like a green test. This also locks the beam's
        # early-stop behaviour (pitfall #33).
        assert longest == svc.max_depth + 1

    @pytest.mark.asyncio
    async def test_version_clock_lands_on_the_result(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("a", seeds=["Drug:a"], version="v1.0.0",
                                     as_of=T_V, depth=1)
        assert result.clock.ontology_version == "v1.0.0"
        assert result.clock.as_of == T_V and result.version_pinned is True

    @pytest.mark.asyncio
    async def test_unpinned_result_reports_no_version(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("a", seeds=["Drug:a"],
                                     pin_version=False, depth=1)
        assert result.version_pinned is False
        assert result.clock.ontology_version is None

    @pytest.mark.asyncio
    async def test_whole_path_pruned_when_second_hop_leaves_the_version(self):
        """The per-hop predicate composed at depth 2 (properties 1 + 3).

        The wrapper here cannot push ``as_of`` into SQL, so every edge
        reaches the walk and the service's own path-level re-verification
        is the only thing between the out-of-version second hop and the
        result: a first hop inside the version must not launder a second
        hop that only became valid after the cutoff, and the pruned route
        must survive as a failed entry whose reason quotes the version and
        the offending edge's own window.
        """
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "第一跳：a 关联 b", BEFORE, None),
            ("Disease:b", "Drug:c", "treats", "第二跳：b 关联 c", AFTER, None),
        ])

        class OldStore:
            def __init__(self, inner):
                self.inner = inner

            async def neighbors(self, tenant_id, entity_ids,
                                rel_types=None, hop=1, valid_view=True):
                return await self.inner.neighbors(
                    tenant_id, entity_ids, rel_types=rel_types, hop=hop,
                    valid_view=valid_view, as_of=None)

        svc = DecisionService(store=OldStore(store), tenant_id=TENANT)
        result = await svc.multi_hop("zzz", seeds=["Drug:a"],
                                     version="v9.9.9", as_of=T_V, depth=2)
        assert result.paths == [], (
            "a valid first hop cannot carry a second hop that only became "
            "true after the cutoff")
        two_hop = [f for f in result.failed if len(f.path.entities) == 3]
        assert two_hop, "the pruned route must survive as a failed entry"
        reason = two_hop[0].invalid_edge_reason
        assert "v9.9.9" in reason
        assert "2026" in reason, (
            "the reason must quote the excluded edge's own window (valid "
            "2026), not just the cutoff (2025)")

    @pytest.mark.asyncio
    async def test_edge_superseded_mid_walk_is_reported_not_swallowed(self):
        """Property 2 composed across hops, with property 5's full reason.

        The second hop's edge is superseded exactly at the cutoff (the
        boundary), so the store drops it mid-walk: the in-version prefix
        must survive, and the boundary probe must surface the removed edge
        with a reason quoting the version AND the edge's own time window -
        the quote that lets a card say "the evidence exists, but not in the
        version you asked about".
        """
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "第一跳：a 关联 b", BEFORE, None),
            ("Disease:b", "Drug:c", "treats", "第二跳：b 关联 c", BEFORE, T_V),
        ])
        svc = DecisionService(store=store, tenant_id=TENANT)
        result = await svc.multi_hop("zzz", seeds=["Drug:a"],
                                     version="v1.0.0", as_of=T_V, depth=2)
        assert [p.claims for p in result.paths] == [["第一跳：a 关联 b"]], (
            "exactly the in-version prefix survives the walk")
        reported = [f for f in result.failed
                    if any("第二跳" in c for c in (f.path.claims or []))]
        assert reported, (
            "an edge the cutoff removed must be reported, not silently "
            "swallowed by the store-side filter")
        reason = reported[0].invalid_edge_reason
        assert "v1.0.0" in reason
        assert "2024" in reason, (
            "the reason must carry the edge's own window (valid 2024), "
            "not just the version label and the cutoff")
        # Anti-hallucination, service side: a returned multi-entity path
        # always carries the edge evidence it was verified against.
        assert result.edges_by_path[id(result.paths[0])]


class TestHopPlanning:
    """plan_hops is what makes the walk follow the question, and every
    degradation path matters: a bad plan must widen the walk, never narrow
    it to nothing (an empty plan looks exactly like "no knowledge")."""

    ONTOLOGY: ClassVar[dict] = {
        "classes": [], "rel_types": ["treats", "contraindicates",
                                     "monitored_by"]}

    @pytest.mark.asyncio
    async def test_plan_comes_from_the_llm(self):
        llm = FakeLLM(replies={"rel_types": '{"hops": ['
                              '{"rel_types": ["treats"]},'
                              '{"rel_types": ["monitored_by"], '
                              '"reverse": true}'
                              '], "stop_when": "the lab is named"}'})
        svc = DecisionService(llm=llm, ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("a 治疗什么？", ["Drug:a"], depth=2)
        assert plans[0].rel_types == ["treats"]
        assert plans[1].rel_types == ["monitored_by"]
        assert llm.calls[0]["kind"] == "hop_plan"

    @pytest.mark.asyncio
    async def test_relation_types_outside_the_vocabulary_are_dropped(self):
        llm = FakeLLM(replies={"rel_types": '{"hops": ['
                              '{"rel_types": ["invented_rel", "treats"]}]}'})
        svc = DecisionService(llm=llm, ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("q", ["Drug:a"], depth=1)
        assert plans[0].rel_types == ["treats"], (
            "a hallucinated relation type must be dropped rather than "
            "silently returning an empty graph")

    @pytest.mark.asyncio
    async def test_all_invalid_types_degrade_to_unfiltered(self):
        llm = FakeLLM(replies={"rel_types": '{"hops": ['
                              '{"rel_types": ["nope"]}]}'})
        svc = DecisionService(llm=llm, ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("q", ["Drug:a"], depth=1)
        assert plans[0].rel_types is None

    @pytest.mark.asyncio
    async def test_no_llm_means_unfiltered_plan(self):
        svc = DecisionService(llm=None, ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("q", ["Drug:a"], depth=3)
        assert len(plans) == 3 and all(p.rel_types is None for p in plans)

    @pytest.mark.asyncio
    async def test_explicit_rel_types_short_circuits_the_llm(self):
        llm = FakeLLM(replies={"plan_hop":
                               '{"hops": [{"rel_types": ["treats"]}]}'})
        svc = DecisionService(llm=llm, ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("q", ["Drug:a"], rel_types=["treats"],
                                    depth=2)
        assert all(p.rel_types == ["treats"] for p in plans)
        assert llm.calls == [], "caller-supplied types win; no LLM call"

    @pytest.mark.asyncio
    async def test_malformed_reply_degrades(self):
        svc = DecisionService(llm=FakeLLM(replies={"rel_types": "no json"}),
                              ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("q", ["Drug:a"], depth=2)
        assert len(plans) == 2 and all(p.rel_types is None for p in plans)

    @pytest.mark.asyncio
    async def test_llm_failure_degrades(self):
        class Boom(FakeLLM):
            async def __call__(self, *a, **k):
                raise RuntimeError("down")

        svc = DecisionService(llm=Boom(), ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("q", ["Drug:a"], depth=2)
        # The claim is "degrades to unfiltered", not merely "returns two
        # things": an exception path that silently narrowed the walk would
        # look identical if only the length were asserted.
        assert len(plans) == 2 and all(p.rel_types is None for p in plans)

    @pytest.mark.asyncio
    async def test_short_plan_is_padded_to_depth(self):
        llm = FakeLLM(replies={"plan_hop":
                               '{"hops": [{"rel_types": ["treats"]}]}'})
        svc = DecisionService(llm=llm, ontology=self.ONTOLOGY)
        plans = await svc.plan_hops("q", ["Drug:a"], depth=3)
        assert len(plans) == 3 and plans[1].rel_types is None

    @pytest.mark.asyncio
    async def test_walk_follows_the_plan(self):
        """The plan must actually reach the store, not just be computed."""
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "a 治疗 b", BEFORE, None),
            ("Drug:a", "Drug:z", "replaced_by", "a 被 z 取代", BEFORE, None),
        ])
        svc = DecisionService(
            store=store, tenant_id=TENANT, ontology=self.ONTOLOGY,
            llm=FakeLLM(replies={"rel_types": '{"hops": ['
                                 '{"rel_types": ["treats"]}]}'}))
        seen: list[list[str] | None] = []
        original = store.neighbors

        async def spy(tenant_id, entity_ids, rel_types=None, hop=1,
                      valid_view=True, as_of=None):
            seen.append(rel_types)
            return await original(tenant_id, entity_ids, rel_types=rel_types,
                                  hop=hop, valid_view=valid_view, as_of=as_of)

        store.neighbors = spy
        await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"], depth=1)
        assert seen and seen[0] == ["treats"]


class TestPathScoring:
    def test_relevant_path_outranks_irrelevant_one(self):
        from services.knowevo.graph_store import Path
        svc = DecisionService()
        relevant = Path(entities=["a", "b"],
                        claims=["eGFR 45 时 SGLT2i 仍可起始"])
        irrelevant = Path(entities=["a", "b"], claims=["天气不错"])
        hi = svc.score_path(relevant, "eGFR 45 时 SGLT2i 是否可以起始？")
        lo = svc.score_path(irrelevant, "eGFR 45 时 SGLT2i 是否可以起始？")
        assert hi.score.total > lo.score.total

    def test_missing_claim_is_flagged_and_penalised(self):
        from services.knowevo.graph_store import Path
        svc = DecisionService()
        partial = Path(entities=["a", "b", "c"], claims=["only one"])
        scored = svc.score_path(partial, "anything")
        assert scored.score.evidence_missing is True
        assert scored.score.evidence_richness < 1.0

    def test_contested_edges_lower_the_score(self):
        from services.knowevo.graph_store import Path
        svc = DecisionService()
        path = Path(entities=["a", "b"], claims=["claim"], edges=["e"])
        clean = svc.score_path(path, "claim", [
            EdgeCard(id="e", src="a", dst="b", rel_type="r", claim="claim")])
        dirty = svc.score_path(path, "claim", [
            EdgeCard(id="e", src="a", dst="b", rel_type="r", claim="claim",
                     contested=True)])
        assert dirty.score.total < clean.score.total

    def test_edges_without_evidence_refs_halve_richness(self):
        from services.knowevo.graph_store import Path
        svc = DecisionService()
        path = Path(entities=["a", "b"], claims=["c"], edges=["e"])
        bare = svc.score_path(path, "c", [
            EdgeCard(id="e", src="a", dst="b", rel_type="r", claim="c")])
        cited = svc.score_path(path, "c", [
            EdgeCard(id="e", src="a", dst="b", rel_type="r", claim="c",
                     props={"evidence_id": "ev1"})])
        assert bare.score.evidence_richness < cited.score.evidence_richness

    def test_answerable_requires_a_strong_match(self):
        from services.knowevo.graph_store import Path
        svc = DecisionService()
        strong = [Path(entities=["a", "b"],
                       claims=["eGFR 45 时 SGLT2i 仍可起始"])]
        weak = [Path(entities=["a", "b"], claims=["起始"])]
        q = "eGFR 45 时 SGLT2i 仍可起始？"
        assert svc._answerable(strong, q) is True
        assert svc._answerable(weak, q) is False


# ---------------------------------------------------------------------------
# Evidence assembly and fusion (02-tech-plan 3.4)
# ---------------------------------------------------------------------------

class TestEvidenceAssembly:
    @pytest.mark.asyncio
    async def test_graph_paths_become_kg_items(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        # Pinned just after the a->b edge became valid: at that cutoff the
        # walk has exactly one in-version edge to collect.
        result = await svc.multi_hop("a 治疗什么？", seeds=["Drug:a"],
                                     version="v1.0.0",
                                     as_of=BEFORE + timedelta(days=1),
                                     depth=1)
        chain = await svc.assemble_evidence(result)
        assert chain.has_evidence()
        assert all(i.source_channel == CHANNEL_KG for i in chain.items)
        assert all(i.provenance.version_pinned for i in chain.items)

    @pytest.mark.asyncio
    async def test_doc_hits_are_added_as_doc_channel(self):
        svc = DecisionService()
        chain = await svc.assemble_evidence(
            [], [DocHit(doc_id="d1", doc_title="指南2024版",
                        span_text="SGLT2i 可用于 eGFR≥30 的患者")])
        assert chain.items[0].source_channel == CHANNEL_DOC
        assert chain.items[0].tag == "EXTRACTED"

    @pytest.mark.asyncio
    async def test_agreeing_channels_upgrade_to_kg_plus_doc(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("b 与 c 有什么禁忌？", seeds=["Disease:b"],
                                     version="v1.0.0", as_of=T_V, depth=1)
        chain = await svc.assemble_evidence(
            result, [DocHit(doc_id="d1", doc_title="指南",
                            span_text="b 与 c 有禁忌")])
        channels = [i.source_channel for i in chain.items]
        assert CHANNEL_KG_DOC in channels, (
            "a proposition both channels assert should be upgraded, not "
            "duplicated")
        assert chain.contested is False

    @pytest.mark.asyncio
    async def test_contradicting_channels_mark_contested(self):
        """A real cross-channel contradiction: KG says one thing, the
        guideline passage says the opposite about the same proposition.

        The earlier version of this test put two *doc* hits in and asserted
        `contested is False`, which cannot fail in a way that matters: the
        fusion only cross-checks kg against doc, so it was asserting that
        an unchecked pair is unchecked. Both items must now survive AND the
        chain must carry the flag, because "nothing is dropped silently" is
        the actual promise.
        """
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("b 与 c 有什么禁忌？", seeds=["Disease:b"],
                                     version="v1.0.0", as_of=T_V, depth=1)
        # Same proposition wording, opposite polarity: the kg side asserts
        # the combination, the doc side forbids it.
        doc_claim = "b 与 c 不有禁忌"
        chain = await svc.assemble_evidence(
            result, [DocHit(doc_id="d1", doc_title="指南2024版",
                            span_text=doc_claim)])
        kg_items = [i for i in chain.items if i.source_channel == CHANNEL_KG]
        doc_items = [i for i in chain.items
                     if i.source_channel == CHANNEL_DOC]
        assert kg_items and doc_items, (
            "a contradiction must not be resolved by dropping a side")
        assert chain.contested is True
        assert all(i.contested for i in kg_items + doc_items), (
            "every item in the conflicting pair is flagged, so a reader "
            "cannot tell which side was privileged")
        assert not any(i.source_channel == CHANNEL_KG_DOC
                       for i in chain.items), (
            "a contradicting pair must not be presented as agreement")

    @pytest.mark.asyncio
    async def test_failed_paths_ride_along_for_counterfactuals(self):
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "旧版：a 治疗 b", BEFORE, T_V)])
        svc = DecisionService(store=store, tenant_id=TENANT)
        result = await svc.multi_hop("a", seeds=["Drug:a"], version="v1",
                                     as_of=T_V, depth=1)
        chain = await svc.assemble_evidence(result)
        assert chain.failed_paths

    @pytest.mark.asyncio
    async def test_unpinned_chain_reports_not_pinned(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        result = await svc.multi_hop("a", seeds=["Drug:a"],
                                     pin_version=False, depth=1)
        chain = await svc.assemble_evidence(result)
        assert all(i.provenance.version_pinned is False for i in chain.items)

    @pytest.mark.asyncio
    async def test_kg_rows_resolve_their_source_from_the_evidence_row(self):
        """The kg row's source line comes from real data, never from str().

        The edge's ``evidence_id`` resolves to its kg_evidence_t/doc_asset_t
        row (doc title + span locator); props that state a source explicitly
        win (fixtures, imports). What is forbidden is a str(None) "None" in
        either field - the panel used to print exactly that.
        """
        from services.knowevo.graph_store import Path
        from services.knowevo.schemas import PathSet

        svc = DecisionService(tenant_id=TENANT)
        svc._evidence_sources = lambda ids: {  # DB-free stand-in for the
            "ev-1": ("指南2024版", "chunk 3, p.5")}  # kg_evidence_t lookup
        linked = Path(entities=["a", "b"], claims=["c"])
        stated = Path(entities=["b", "c"], claims=["d"])
        bare = Path(entities=["c", "d"], claims=["e"])
        pset = PathSet(
            paths=[linked, stated, bare], version_pinned=True,
            claims_by_path={id(linked): ["c"], id(stated): ["d"],
                            id(bare): ["e"]},
            edges_by_path={
                id(linked): [EdgeCard(
                    id="e1", src="a", dst="b", rel_type="r", claim="c",
                    props={"evidence_id": "ev-1"})],
                id(stated): [EdgeCard(
                    id="e2", src="b", dst="c", rel_type="r", claim="d",
                    props={"doc_title": "自带来源", "span": "§1"})],
                id(bare): [EdgeCard(
                    id="e3", src="c", dst="d", rel_type="r", claim="e",
                    props={"evidence_id": "ev-missing", "doc_title": None,
                           "span": None})],
            })
        chain = await svc.assemble_evidence(pset)
        by_claim = {i.claim: i.provenance for i in chain.items}
        assert (by_claim["c"].doc, by_claim["c"].span) == ("指南2024版",
                                                           "chunk 3, p.5")
        assert (by_claim["d"].doc, by_claim["d"].span) == ("自带来源", "§1")
        assert (by_claim["e"].doc, by_claim["e"].span) == ("", ""), (
            "an unresolvable source stays empty so the panel can fall back "
            "to its label; a guessed or stringified-none source is worse")


# ---------------------------------------------------------------------------
# Decision card (02-tech-plan 3.3)
# ---------------------------------------------------------------------------

class TestDecisionCard:
    @pytest.mark.asyncio
    async def test_card_schema_and_calibration_flag(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card(
            "eGFR 45 的患者如何起始 SGLT2i？", _chain_with_claim(),
            clock=VersionClock("v1.3.0", T_V, source="explicit"))
        assert card.decision == DECISION_RECOMMEND
        assert len(card.candidates) == 2
        assert card.candidates[0].option.startswith("首选 SGLT2i")
        assert card.uncertainty_notes
        assert any("未找到校准表" in n for n in card.uncertainty_notes)
        assert card.calibration_applied is False
        assert card.conflict_adjudications[0].type == "guideline_vs_label"

    @pytest.mark.asyncio
    async def test_counterfactual_only_on_top1(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert card.candidates[0].counterfactual is not None
        assert card.candidates[1].counterfactual is None, (
            "counterfactual is a top-1 concern (one extra call budget)")

    @pytest.mark.asyncio
    async def test_healthcare_disclaimer_appended(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        card = await DecisionService(llm=llm, domain="healthcare").render_card(
            "q", _chain_with_claim())
        assert card.disclaimer == HEALTHCARE_DISCLAIMER

    @pytest.mark.asyncio
    async def test_non_healthcare_domain_gets_no_disclaimer(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        card = await DecisionService(llm=llm, domain="policy").render_card(
            "q", _chain_with_claim())
        assert card.disclaimer == ""

    @pytest.mark.asyncio
    async def test_empty_evidence_refuses_without_calling_llm(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("猫糖尿病酮症酸中毒用什么胰岛素？",
                                     EvidenceChain())
        assert card.decision == DECISION_INSUFFICIENT
        assert card.candidates == []
        assert llm.calls == [], (
            "refusal must be deterministic: an LLM asked to conclude "
            "'insufficient evidence' is free to invent a candidate instead")

    @pytest.mark.asyncio
    async def test_refusal_still_carries_disclaimer(self):
        card = await DecisionService(domain="healthcare").render_card(
            "q", EvidenceChain())
        assert card.disclaimer == HEALTHCARE_DISCLAIMER

    @pytest.mark.asyncio
    async def test_llm_insufficient_decision_clears_candidates(self):
        llm = FakeLLM(replies={
            "决策卡": '{"decision": "INSUFFICIENT_EVIDENCE", "candidates": '
                     '[{"option": "猜一个"}]}'})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert card.decision == DECISION_INSUFFICIENT
        assert card.candidates == []

    @pytest.mark.asyncio
    async def test_unknown_decision_value_falls_back_to_recommend(self):
        llm = FakeLLM(replies={"决策卡": '{"decision": "MAYBE", "candidates": '
                                       '[{"option": "首选 SGLT2i"}]}'})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert card.decision == DECISION_RECOMMEND
        assert card.candidates[0].option == "首选 SGLT2i"

    @pytest.mark.asyncio
    async def test_lite_mode_drops_risks_and_counterfactual(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim(), mode="lite")
        assert any("lite" in n for n in card.uncertainty_notes)
        for cand in card.candidates:
            assert cand.risks == [] and cand.counterfactual is None

    @pytest.mark.asyncio
    async def test_pinned_flag_comes_from_the_chain(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        pinned = await svc.render_card("q", _chain_with_claim(pinned=True))
        unpinned = await svc.render_card(
            "q", _chain_with_claim(pinned=False),
            clock=VersionClock(None, T_V))
        assert pinned.knowledge_version_pinned is True
        assert unpinned.knowledge_version_pinned is False

    @pytest.mark.asyncio
    async def test_pin_cannot_be_claimed_without_a_version(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim(pinned=True),
                                     clock=VersionClock(None, T_V))
        # The LLM echoed version_pinned=true; with no version on the clock
        # that provenance claim is unbacked and must be dropped.
        assert all(not i.provenance.version_pinned
                   for i in card.candidates[0].evidence_chain)

    @pytest.mark.asyncio
    async def test_llm_null_provenance_backfills_from_the_chain(self):
        """doc/span are assembly facts, not model output.

        The card prompt never carried them, so the model echoes null - and a
        plain str() used to publish that as the literal "None" (the panel
        printed "None · None" as a source line). A claim the chain knows
        must inherit the chain's provenance instead.
        """
        llm = FakeLLM(replies={"决策卡": """{
          "candidates": [{"option": "首选 SGLT2i（恩格列净）",
            "evidence_chain": [{
              "claim": "eGFR 45 时 SGLT2i 仍可起始",
              "provenance": {"doc": null, "span": null,
                             "kg_path": [], "version_pinned": true},
              "tag": "EXTRACTED", "source_channel": "kg"}],
            "risks": []}],
          "decision": "RECOMMEND"}"""})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        prov = card.candidates[0].evidence_chain[0].provenance
        assert (prov.doc, prov.span) == ("指南2024版", "§9.2 用药"), (
            "a matched claim takes its provenance from the assembled chain")
        assert prov.kg_path == ["Drug:sglt2i", "CKD_G3a"]

    @pytest.mark.asyncio
    async def test_unmatched_null_provenance_stays_empty_never_none(self):
        """No chain match means no source - empty, so the panel degrades to
        its fallback label. The literal string "None" must not survive the
        parse anywhere in the payload (the exact rendering defect)."""
        llm = FakeLLM(replies={"决策卡": """{
          "candidates": [{"option": "猜一个",
            "evidence_chain": [{
              "claim": "链上没有的命题",
              "provenance": {"doc": "None", "span": null,
                             "kg_path": [null], "version_pinned": false},
              "tag": "EXTRACTED", "source_channel": "kg"}],
            "risks": []}],
          "decision": "RECOMMEND"}"""})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        prov = card.candidates[0].evidence_chain[0].provenance
        assert (prov.doc, prov.span, prov.kg_path) == ("", "", [])
        assert "None" not in json.dumps(card.to_payload(), ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_markdown_fenced_json_is_accepted(self):
        llm = FakeLLM(replies={"决策卡": f"```json\n{CARD_JSON}\n```"})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert len(card.candidates) == 2
        assert card.candidates[0].option.startswith("首选 SGLT2i"), (
            "a mangled parse that still yielded two entries would pass a "
            "count-only assertion")

    @pytest.mark.asyncio
    async def test_recommend_with_no_candidates_becomes_refusal(self):
        """Zero candidates cannot be a recommendation.

        The wire contract rejects `RECOMMEND` with an empty candidate list,
        so a card that reached that state used to be an invalid payload
        waiting for whichever reader touched it first.
        """
        llm = FakeLLM(replies={"决策卡": '{"decision": "RECOMMEND", '
                                      '"candidates": []}'})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert card.decision == DECISION_INSUFFICIENT
        assert card.candidates == []
        # And the payload it would have written is a valid one.
        svc.validate_card(card)

    @pytest.mark.asyncio
    async def test_non_json_reply_raises_typeerror(self):
        llm = FakeLLM(replies={"决策卡": "抱歉，我无法回答。"})
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        with pytest.raises(TypeError):
            await svc.render_card("q", _chain_with_claim())

    @pytest.mark.asyncio
    async def test_contested_chain_adds_inconsistency_note(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        chain = _chain_with_claim()
        chain.contested = True
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", chain)
        assert any("知识不一致" in n for n in card.uncertainty_notes)

    @pytest.mark.asyncio
    async def test_failed_probe_adds_an_uncertainty_note(self):
        """A failed version-boundary probe must not read as "nothing was
        excluded" - that is the one claim version pinning may not fake."""
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        chain = _chain_with_claim()
        chain.probe_failed = True
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", chain)
        assert any("版本边界探测" in n for n in card.uncertainty_notes)

    @pytest.mark.asyncio
    async def test_walk_surfaces_probe_failure_instead_of_empty_failed(self):
        class BrokenProbeStore(FakeStore):
            async def neighbors(self, tenant_id, entity_ids, rel_types=None,
                                hop=1, valid_view=True, as_of=None):
                if not valid_view:  # the probe's all-time query
                    raise RuntimeError("store down")
                return await super().neighbors(
                    tenant_id, entity_ids, rel_types=rel_types, hop=hop,
                    valid_view=valid_view, as_of=as_of)

        svc = DecisionService(store=BrokenProbeStore([
            ("Drug:a", "Disease:b", "treats", "a 治疗 b", BEFORE, None)]),
            tenant_id=TENANT)
        result = await svc.multi_hop("a", seeds=["Drug:a"], version="v1",
                                     as_of=T_V, depth=1)
        assert result.probe_failed is True
        assert all(getattr(f, "invalid_edge_reason", "") != PROBE_FAILED
                   for f in result.failed), (
            "the synthetic probe marker must not masquerade as a real "
            "excluded edge")
        chain = await svc.assemble_evidence(result)
        assert chain.probe_failed is True

    def test_card_contract_rejects_a_refusal_with_candidates(self):
        from services.knowevo.schemas import Candidate, DecisionCardContract
        with pytest.raises(ValidationError):
            DecisionCardContract(decision=DECISION_INSUFFICIENT,
                                 candidates=[Candidate(option="x")])

    def test_card_contract_rejects_a_recommendation_without_candidates(self):
        from services.knowevo.schemas import DecisionCardContract
        with pytest.raises(ValidationError):
            DecisionCardContract(decision=DECISION_RECOMMEND)

    def test_validate_card_round_trips_a_real_payload(self):
        svc = DecisionService()
        payload = DecisionCard(
            question_id="Q-1", question="q",
            knowledge_stamp=KnowledgeStamp(ontology_version="v1.0.0",
                                           kg_cutoff=T_V.isoformat(),
                                           clock_source="version_created_at"),
            candidates=[], decision=DECISION_INSUFFICIENT,
            uncertainty_notes=["no evidence"]).to_payload()
        validated = svc.validate_card_payload(payload)
        assert validated.question_id == "Q-1"
        assert validated.knowledge_stamp.ontology_version == "v1.0.0"
        assert validated.decision == DECISION_INSUFFICIENT


class TestCalibration:
    def test_no_table_returns_raw_value(self):
        svc = DecisionService(calibration=None)
        assert svc.calibrate(0.83) == 0.83

    def test_bucket_lookup_maps_to_empirical_value(self):
        table = [{"lo": 0.8, "hi": 0.9, "empirical": 0.72},
                 {"lo": 0.9, "hi": 1.01, "empirical": 0.88}]
        svc = DecisionService(calibration=table)
        assert svc.calibrate(0.85) == 0.72
        assert svc.calibrate(0.95) == 0.88

    def test_k4_ten_bucket_table_is_what_the_lookup_expects(self):
        """The table shape K4 4.1 specifies: ten tenth-width buckets.

        Pinned as a positive case so the bucket contract is exercised by a
        real curve rather than only by two hand-written rows.
        """
        table = [{"lo": i / 10,
                  "hi": (i + 1) / 10 if i < 9 else 1.0,
                  "empirical": round((i + 0.5) / 10, 2)}
                 for i in range(10)]
        svc = DecisionService(calibration=table)
        assert svc.calibrate(0.05) == 0.05
        assert svc.calibrate(0.95) == 0.95
        assert svc.calibrate(0.55) == pytest.approx(0.55)

    def test_calibration_dict_wrapper_is_unwrapped(self):
        svc = DecisionService(calibration={"buckets": [
            {"lo": 0.5, "hi": 0.6, "empirical": 0.4}]})
        assert svc.calibrate(0.55) == 0.4

    def test_out_of_range_falls_through_to_nearest_bucket(self):
        svc = DecisionService(calibration=[
            {"lo": 0.0, "hi": 1.01, "empirical": 0.5}])
        assert svc.calibrate(1.0) == 0.5
        assert svc.calibrate(-0.5) == 0.5

    def test_non_numeric_confidence_passes_through(self):
        svc = DecisionService(calibration=[
            {"lo": 0.0, "hi": 1.01, "empirical": 0.5}])
        assert svc.calibrate("n/a") == "n/a"

    def test_confidence_outside_every_bucket_is_reported_not_claimed(self):
        """A passthrough must not be reported as a calibration.

        The table below covers only [0.9,1]; a 0.55 confidence used to pass
        through while the card still said calibration_applied=True, which is
        the card claiming a measurement it never made.
        """
        notes: list[str] = []
        svc = DecisionService(calibration=[
            {"lo": 0.9, "hi": 1.0, "empirical": 0.88}])
        value = svc._calibrate_in_table(0.55, svc._calibration_table(),
                                        notes.append)
        assert value == 0.55
        assert notes and "未落入任何校准桶" in notes[0]

    def test_malformed_bucket_is_skipped_and_reported(self):
        notes: list[str] = []
        table = [{"lo": "bad", "hi": "worse", "empirical": 0.5},
                 {"lo": 0.5, "hi": 0.6, "empirical": 0.42}]
        value = DecisionService(calibration=table)._calibrate_in_table(
            0.55, table, notes.append)
        assert value == 0.42
        assert any("结构不合法" in n for n in notes)

    @pytest.mark.asyncio
    async def test_card_records_that_calibration_ran(self):
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        # A ten-bucket curve. Bucket 9 (0.9-1.0) is the one the fixture's
        # top candidate (raw 0.9) lands in; bucket 4 covers 0.4 by identity.
        table = [{"lo": i / 10, "hi": (i + 1) / 10 if i < 9 else 1.0,
                  "empirical": 0.88 if i == 9 else (i + 0.5) / 10}
                 for i in range(10)]
        svc = DecisionService(llm=llm, tenant_id=TENANT, calibration=table)
        card = await svc.render_card("q", _chain_with_claim())
        assert card.calibration_applied is True
        assert card.candidates[0].confidence_calibrated == 0.88, (
            "0.9 must hit the 0.9-1.0 bucket and come out empirically "
            "corrected")

    @pytest.mark.asyncio
    async def test_card_reports_uncalibrated_confidence_as_uncalibrated(self):
        """A partial table that misses a candidate must not claim coverage."""
        llm = FakeLLM(replies={"决策卡": CARD_JSON})
        svc = DecisionService(llm=llm, tenant_id=TENANT, calibration=[
            {"lo": 0.0, "hi": 0.1, "empirical": 0.05}])
        card = await svc.render_card("q", _chain_with_claim())
        assert card.calibration_applied is False, (
            "no candidate fell in a bucket, so no calibration was applied")
        assert any("未落入任何校准桶" in n for n in card.uncertainty_notes)


class TestCalibrateHops:
    @pytest.mark.asyncio
    async def test_curve_rows_and_recommendation(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        curve = await svc.calibrate_hops(
            ["a 治疗什么？"] * 3, seeds_for=lambda q: ["Drug:a"],
            judge=lambda q, r: len(r.paths) > 0, depths=(1, 2),
            testset_hash="abc")
        assert [r["depth"] for r in curve.rows] == [1, 2]
        assert all(r["n_questions"] == 3 for r in curve.rows)
        assert curve.testset_hash == "abc"
        assert curve.recommended_depth in (1, 2)

    @pytest.mark.asyncio
    async def test_recommendation_filters_by_latency_budget_first(self):
        """Depth 2 wins on accuracy, depth 3 blows the p95 budget; the curve
        must recommend depth 2.

        The docstring's whole claim is "the most accurate depth that cannot
        answer inside p95 is a regression, not a recommendation". The
        previous assertion (`recommended_depth in (1, 2)`) was satisfied by
        either answer and never exercised the filter.
        """
        # A current-view three-edge chain whose claims do not answer the
        # question, so the walk is governed by depth alone: depth 1 sees one
        # edge, depth 2 reaches the end of the chain, depth 3 adds nothing.
        store = FakeStore([
            ("Drug:a", "Disease:b", "treats", "P1", BEFORE, None),
            ("Disease:b", "Drug:c", "contraindicates", "P2", BEFORE, None),
            ("Drug:c", "Drug:e", "replaced_by", "P3", BEFORE, None)])
        svc = DecisionService(store=store, tenant_id=TENANT)
        real_hop = svc.multi_hop
        delays = {1: 0.0, 2: 0.001, 3: 40.0}

        async def slow(question, depth=None, **kw):
            # Blocking sleep on purpose: this stands in for a slow store and
            # must be measured by the wall clock, exactly as a real slow
            # query would be. ASYNC251 is wrong for that intent.
            time.sleep(delays[depth])  # noqa: ASYNC251 - simulated latency
            return await real_hop(question, depth=depth, **kw)

        svc.multi_hop = slow
        curve = await svc.calibrate_hops(
            ["zzz"], seeds_for=lambda q: ["Drug:a"],
            judge=lambda q, r: max((len(p.entities) for p in r.paths),
                                   default=0) >= 3,
            depths=(1, 2, 3))
        by_depth = {r["depth"]: r for r in curve.rows}
        assert by_depth[3]["latency_ms"] > REASONING_LATENCY_BUDGET_MS
        assert by_depth[2]["accuracy"] > by_depth[1]["accuracy"], (
            "the fixture must make depth 2 the most accurate in-budget "
            "depth, or this test proves nothing")
        assert curve.recommended_depth == 2, (
            "an over-budget depth must not be recommended no matter how "
            "accurate it looks")

    @pytest.mark.asyncio
    async def test_empty_questions_still_returns_rows(self):
        svc = DecisionService(store=_pinned_store(), tenant_id=TENANT)
        curve = await svc.calibrate_hops([], depths=(1,))
        assert curve.rows[0]["accuracy"] == 0.0

    def test_best_by_prefers_shallower_depth_on_ties(self):
        from services.knowevo.schemas import HopCurve
        curve = HopCurve(rows=[{"depth": 1, "accuracy": 0.9},
                               {"depth": 3, "accuracy": 0.9}])
        assert curve.best_by("accuracy") == 1

    def test_best_by_empty_returns_default(self):
        from services.knowevo.schemas import HopCurve
        assert HopCurve().best_by() == 3


# ---------------------------------------------------------------------------
# help/persistence
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_parse_json_plain(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_parse_json_fenced(self):
        assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_parse_json_embedded_in_prose(self):
        assert _parse_json('好的：{"a": 1} 以上') == {"a": 1}

    def test_parse_json_garbage_returns_none(self):
        assert _parse_json("no json here") is None
        assert _parse_json("") is None

    def test_signature_ignores_digits_and_spaces(self):
        assert _signature("HbA1c 目标 7.0% 吗") == _signature(
            "HbA1c 目标 8.0% 吗")

    def test_question_id_is_stable_and_short(self):
        assert _question_id("abc") == _question_id("abc")
        assert len(_question_id("abc")) == 16

    def test_overlap_is_zero_for_empty(self):
        assert _overlap("", "abc") == 0.0

    def test_parse_json_passthrough_for_objects(self):
        assert _parse_json({"a": 1}) == {"a": 1}


class TestPayloadSerialization:
    def test_uuid_and_datetime_become_strings(self):
        card = DecisionCard(
            question_id="q1",
            knowledge_stamp=KnowledgeStamp(ontology_version="v1",
                                           kg_cutoff=T_V.isoformat()))
        payload = card.to_payload()
        assert isinstance(payload["knowledge_stamp"]["kg_cutoff"], str)
        assert payload["question_id"] == "q1"

    def test_nested_candidates_serialize_fully(self):
        from services.knowevo.schemas import Candidate
        card = DecisionCard(candidates=[Candidate(
            option="x", evidence_chain=[EvidenceItem(
                claim="c", provenance=Provenance(doc="d"))])])
        payload = card.to_payload()
        assert payload["candidates"][0]["option"] == "x"
        assert payload["candidates"][0]["evidence_chain"][0]["claim"] == "c"
        assert payload["candidates"][0]["evidence_chain"][0]["provenance"][
            "doc"] == "d"

    def test_uuid_value_is_stringified(self):
        card = DecisionCard(candidates=[])
        card_dict = card.to_payload()
        assert isinstance(card_dict, dict)


# ---------------------------------------------------------------------------
# Layer 2: real Postgres (RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

pg_gate = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests")


def _read_card(card_id, tenant):
    """Read back the columns the card assertions need.

    knowevo_db.get_by_id returns only the primary key (it is a existence
    helper, not a reader), and the ORM rows must be projected inside the
    session block (pitfall #26).
    """
    from database.knowevo_db import DecisionCard as Row
    from database.knowevo_db import _get_db_session
    with _get_db_session() as session:
        row = session.query(Row).filter(Row.id == card_id,
                                        Row.tenant_id == tenant).first()
        return {"payload": dict(row.payload or {}),
                "knowledge_stamp": dict(row.knowledge_stamp or {}),
                "needs_rerun": row.needs_rerun,
                "rerun_of": row.rerun_of}


@pg_gate
@pytest.mark.asyncio
class TestCardPersistence:
    async def test_persist_writes_payload_and_stamp(self):
        svc = DecisionService(llm=FakeLLM(replies={"决策卡": CARD_JSON}),
                              tenant_id=str(uuid_mod.uuid4()))
        clock = VersionClock("v1.3.0", T_V, source="explicit")
        card = await svc.render_card("eGFR 45 如何起始？",
                                     _chain_with_claim(), clock=clock)
        card_id = await svc.persist(card)
        assert card_id is not None

        row = _read_card(card_id, svc.tenant_id)
        assert row["payload"]["candidates"][0]["option"].startswith("首选")
        assert row["knowledge_stamp"]["ontology_version"] == "v1.3.0"

    async def test_refusal_card_is_marked_needs_rerun(self):
        svc = DecisionService(llm=FakeLLM(), tenant_id=str(uuid_mod.uuid4()))
        card = await svc.render_card("库外问题", EvidenceChain())
        card_id = await svc.persist(card)
        row = _read_card(card_id, svc.tenant_id)
        assert row["needs_rerun"] is True

    async def test_rerun_records_old_vs_new_diff(self):
        tenant = str(uuid_mod.uuid4())
        svc = DecisionService(llm=FakeLLM(replies={"决策卡": CARD_JSON}),
                              tenant_id=tenant)
        card = await svc.render_card("库外问题", EvidenceChain())
        await svc.persist(card)

        async def handler(row):
            fresh = DecisionCard(question_id=row["question_id"],
                                 question="库外问题")
            fresh.decision = DECISION_RECOMMEND
            from services.knowevo.schemas import Candidate
            fresh.candidates = [Candidate(option="新版依据给出的方案")]
            return fresh

        new_ids = await svc.rerun_marked(handler=handler)
        assert new_ids, "the needs_rerun card should have been re-rendered"
        new_row = _read_card(new_ids[0], tenant)
        note = new_row["payload"]["uncertainty_notes"][-1]
        assert "重算差异" in note and "changed" in note
        assert new_row["rerun_of"] is not None
        assert new_row["needs_rerun"] is False

    async def test_rerun_requires_a_handler(self):
        svc = DecisionService(tenant_id=str(uuid_mod.uuid4()))
        with pytest.raises(ValueError):
            await svc.rerun_marked()


@pg_gate
@pytest.mark.asyncio
class TestEvolutionTraceInPostgres:
    async def test_relation_events_include_superseded_edges(self):
        from services.knowevo.graph_store import PgJsonbGraphStore
        from services.knowevo.kg_service import KGService, PgStore
        tenant = str(uuid_mod.uuid4())
        store = PgJsonbGraphStore()
        await store.upsert_entities(tenant, [
            {"stable_id": "Drug:a", "name": "a", "class_ref": "Drug"},
            {"stable_id": "Disease:b", "name": "b", "class_ref": "Disease"}])
        await store.upsert_relations(tenant, [
            {"src": "Drug:a", "dst": "Disease:b", "rel_type": "treats",
             "claim": "a 治疗 b"}])
        svc = KGService(store=PgStore(), tenant_id=tenant)
        trace = await svc.evolution_trace(entity_id="Drug:a")
        assert trace.events
        assert trace.events[0]["type"] == "relation"
        assert trace.events[0]["rel_type"] == "treats"
        assert trace.truncated is False