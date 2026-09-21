"""
KnowEvo K3 decision layer (T-09): routing, version-pinned multi-hop beam
search, evidence-chain assembly and decision-card rendering.

Two things make this file the centre of the project's originality claim:

1. **Version pinning (B2).** Every expansion step of the beam walk runs
   inside G_v - the subgraph of facts valid at a named knowledge version's
   cutoff t_v (the predicate itself delegated to version_pin.py, which
   transcribes the definition from 02-tech-plan 3.2). Paths that leave G_v
   are kept but flagged, which is what makes the pinned on/off ablation
   observable instead of merely asserted.
2. **Honest degradation everywhere else.** No calibration table means
   confidence passes through with ``calibration_applied=False``; no
   evidence means ``INSUFFICIENT_EVIDENCE`` with no candidates; two
   contradictory channels are marked contested rather than silently
   resolved. A card that overstates its own grounding is worse than a card
   that admits its limits.

The LLM is injected as the async callable contract frozen by kg_service and
implemented by llm_client (``await llm(prompt, *, kind, tier,
temperature)``), so tests drive the whole layer with a fake and T-08's
LlmRouter plugs in unchanged. Persistence goes to decision_card_t (T-03
schema - no migration, no ALTER).

Interface contract: knowevo/backend/services/knowevo/decision_service.py.md;
algorithm source: memo 04-K3 (02-technical-plan 3.1-3.4).
Design inspired by: Adaptive-RAG's rule-then-classify routing (2403.14403)
and HippoRAG's graph-informed path ranking (2409.14866), both reimplemented
against this repo's own graph seam - attribution per 03-development-plan 4.2.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from consts.const import KW_MULTIHOP_BEAM, KW_MULTIHOP_MAX_DEPTH, LANGUAGE
from services.knowevo.graph_store import EdgeCard, HopPlan, Path, Subgraph
from services.knowevo.kg_service import _render_prompt
from services.knowevo.schemas import (
    CHANNEL_DOC,
    CHANNEL_KG,
    CHANNEL_KG_DOC,
    DECISION_INSUFFICIENT,
    DECISION_RECOMMEND,
    ROUTE_BOTH,
    ROUTE_REASONING,
    ROUTE_RETRIEVAL,
    TAG_EXTRACTED,
    TAG_INFERRED,
    CalibrationTableModel,
    Candidate,
    ConflictAdjudication,
    Counterfactual,
    DecisionCard,
    DecisionCardContract,
    DocHit,
    EvidenceChain,
    EvidenceItem,
    HopCurve,
    KnowledgeStamp,
    PathScore,
    PathSet,
    Provenance,
    Route,
    ScoredPath,
)
from services.knowevo.version_pin import (
    VersionClock,
    edge_in_version,
    path_version_valid,
    resolve_version_clock,
)

logger = logging.getLogger(__name__)

TIER_SMALL = "small"
TIER_MID = "mid"

# K3 1.1 latency budget (p95 <= 30s hard ceiling): the reasoning path gets
# 25s of it; calibrate_hops refuses to recommend a depth that breaks this.
REASONING_LATENCY_BUDGET_MS = 25000

# 02-tech-plan 3.1: LOOKUP_RULES - a question is a single-point lookup only
# when it asks for one attribute and carries no comparison, mechanism or
# causal chain. Explicit patterns rather than a trained classifier, so the
# low-resource narrative holds and every decision is auditable.
LOOKUP_RULES: list[dict[str, str]] = [
    {"name": "class_or_type",
     "pattern": r"(哪一类|什么类|属于哪|是什么类|which (class|type|category))"},
    {"name": "normal_range",
     "pattern": r"(正常(参考)?(范围|值)|参考范围|normal (range|value))"},
    {"name": "definition",
     "pattern": r"(是什么|的定义|指什么|what is the definition)"},
    {"name": "quantity",
     "pattern": r"(多长时间|多少(周|天|小时|个月)|how (long|many))"},
    {"name": "single_attribute",
     "pattern": r"(通过哪个(器官|部位)|抑制的?是?什么酶"
                r"|which (organ|enzyme|site))"},
    {"name": "single_fact",
     "pattern": r"(首选是什么|一线(药物)?(是)?什么"
                r"|first[- ]line (drug|therapy) is)"},
]

# Any of these means the answer turns on a version difference: never a
# plain lookup (02-tech-plan 3.1 rule 2).
VERSION_COMPARE_WORDS = [
    "最新版", "新版", "旧版", "以前", "现在", "变化", "有何不同", "差异",
    "vs", "相比", "更新后", "改版", "修订",
    "latest", "new version", "old version", "compared", "what changed",
    "revised", "updated guideline", "difference between",
]

# A lookup rule match is vetoed when the question also carries one of
# these: a question matching the "first-line drug is" rule but also asking
# "and why" is plainly multi-hop, not a lookup.
MULTI_HOP_MARKERS = [
    "以及", "并且", "同时", "机制", "为什么", "为何", "风险", "警惕",
    "需注意", "如何调整", "怎么选", "先后", "导致",
    "and", "why", "mechanism", "risk", "how should", "adjust",
]

# Polarity markers for the narrow contradiction heuristic below. These are
# matched as substrings, so an entry must be a genuine polarity flip and
# not a domain word that merely contains one: "禁" was in this list and
# made every claim mentioning 禁忌 (contraindication) look negated, which
# silently disabled contradiction detection for exactly the drug-safety
# pairs the check exists for (pitfall #34).
NEGATION_MARKERS = ["不", "无", "禁用", "停用", "不可", "避免", "不再", "取消",
                    "not", "no longer", "avoid", "contraindicated", "never"]

# Router few-shot, one example per route. Kept short on purpose: the L2
# call is budgeted at ~200 tokens (02-tech-plan 3.1).
ROUTER_EXAMPLES: list[dict[str, str]] = [
    {"question": "二甲双胍是哪一类降糖药？", "route": ROUTE_RETRIEVAL,
     "reason": "单个实体、单个属性，一次查询即可"},
    {"question": "SGLT2抑制剂通过哪个器官排泄葡萄糖，长期使用需警惕哪类感染风险？",
     "route": ROUTE_REASONING,
     "reason": "两个事实串联：作用部位与由此产生的感染风险"},
    {"question": "《中国2型糖尿病防治指南》2024版对HbA1c目标的建议与2020版"
                 "相比有何变化？",
     "route": ROUTE_BOTH,
     "reason": "问题取决于两个知识版本之间的差异，需版本对比"},
]

# Healthcare cards always carry this (02-tech-plan 3.3 medical boundary).
HEALTHCARE_DISCLAIMER = (
    "本系统提供认知辅助，不构成处方建议；临床决策请结合患者具体情况"
    "并由执业医师作出。"
)

# Content-word overlap above which two claims are treated as the same
# proposition (used for cross-channel fusion and contradiction checks).
PROPOSITION_MATCH = 0.5

# K4 4.1: empirical calibration is a ten-bucket curve, so every card's
# confidence means the same thing across evaluation runs. The edges are
# fixed here rather than inferred from the table so a short or ragged
# table is detectable instead of silently producing a different curve.
CALIBRATION_BUCKETS = 10
CALIBRATION_EDGES = [i / CALIBRATION_BUCKETS
                     for i in range(CALIBRATION_BUCKETS + 1)]

# Sentinel reason on the synthetic entry _probe_excluded emits when the
# boundary probe itself failed. Never surfaces as a decision reason: the
# walk lifts it into PathSet.probe_failed and the card turns that into an
# uncertainty note (an unbacked "nothing was excluded" is the one outcome
# version pinning must not produce silently).
PROBE_FAILED = "probe_failed"

_DIGITS = re.compile(r"[0-9０-９%．.]")
_WORD = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")
_FENCE_OPEN = re.compile(r"^```[a-zA-Z]*\s*")
_FENCE_CLOSE = re.compile(r"\s*```$")


def _tokens(text: str) -> set[str]:
    """Cheap lexical token set: CJK unigrams plus latin/digit runs.

    Deliberately not an embedding call: path scoring runs per beam
    candidate inside the latency budget, and the PPR/embedding ranker is a
    listed alternative rather than a v1 requirement (02-tech-plan 3.2).
    """
    return {t.lower() for t in _WORD.findall(text or "")}


def _overlap(a: str, b: str) -> float:
    """Jaccard overlap in [0,1]; 0.0 when either side is empty."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _has_any(text: str, needles: Iterable[str]) -> bool:
    low = (text or "").lower()
    return any(n.lower() in low for n in needles)


def _signature(question: str) -> str:
    """Question signature for the route-hit ledger: entity-free skeleton.

    Digits are stripped so the same question shape with a different value
    shares a signature: the E6 route hit rate is about question *shape*,
    not about the particular number in it.
    """
    return re.sub(r"\s+", "", _DIGITS.sub("", question or ""))[:60]


def _contradicts(left: str, right: str) -> bool:
    """Do two claims assert the same thing with opposite polarity?

    Intentionally narrow: high lexical overlap plus exactly one side
    carrying a negation marker. It will miss subtle conflicts - which is why
    cross-document disagreement is also caught at the merge layer (T-06
    CONTENDED) - but it never invents one, and everything it catches is
    surfaced rather than resolved.
    """
    if _overlap(left, right) < PROPOSITION_MATCH:
        return False
    return _has_any(left, NEGATION_MARKERS) != _has_any(right, NEGATION_MARKERS)


class DecisionService:
    """K3 orchestration: route -> dual-path execution -> chain -> card.

    ``store`` is a GraphStore (PgJsonbGraphStore in production, a fake in
    tests); ``llm`` is the injected async callable, or None when the caller
    only needs the deterministic parts (routing rules, the version-pinned
    walk, refusal handling, scoring). ``ontology`` is the active snapshot
    dict and supplies the hop planner's relation vocabulary.
    """

    def __init__(self, store: Any = None, llm: Any = None,
                 ontology: dict[str, Any] | None = None,
                 tenant_id: str = "",
                 domain: str = "healthcare",
                 lang: str = LANGUAGE["ZH"],
                 calibration: dict[str, Any] | list | None = None,
                 version_rows: list[dict[str, Any]] | None = None,
                 max_depth: int = KW_MULTIHOP_MAX_DEPTH,
                 beam: int = KW_MULTIHOP_BEAM):
        self.store = store
        self.llm = llm
        self.ontology = ontology or {"classes": [], "rel_types": []}
        self.tenant_id = tenant_id
        self.domain = domain
        self.lang = lang
        self.max_depth = max(1, int(max_depth))
        self.beam = max(1, int(beam))
        self._calibration = calibration
        self._calibration_loaded = calibration is not None
        # Ontology version rows ([{version, created_at}]), the source of a
        # version's cutoff t_v. Injected when the caller already has them
        # (tests, batch runs); otherwise resolved from the DB on demand.
        self._version_rows = version_rows
        # E6 route-hit ledger: signature -> {"hits": int, "misses": int}.
        self._route_ledger: dict[str, dict[str, int]] = {}
        # Whether the store honours the as_of seam extension, resolved once
        # (see _expand).
        self._store_pins: bool | None = None

    # ── routing (02-tech-plan 3.1) ─────────────────────────────────────

    def route(self, question: str, ctx: str = "") -> Route:
        """Deterministic routing: L1 rules, then the safe default.

        L1 is free and settles the two ends of the distribution - obvious
        single-point lookups as R, any version-comparison wording as RM.
        Everything in between falls to L3 (RM): slow is acceptable, wrong
        is not. This entry point never calls the LLM, which is what lets it
        run on the synchronous request path; ``route_async`` adds the L2
        few-shot classification for callers already in an event loop.

        A signature with >= 2 recorded failures escalates to RM before any
        rule is consulted - a question shape that already fooled the router
        twice should not be handed back to the same rules.
        """
        ledger = self._route_ledger.get(_signature(question))
        if ledger and ledger.get("misses", 0) >= 2:
            return Route(route=ROUTE_BOTH, confidence=0.0, level="L3",
                         reason="signature-failure >= 2 -> escalate")

        # Version comparison beats every lookup rule: a question that asks
        # what changed is not a single-point lookup, even when its wording
        # also matches a lookup pattern.
        if _has_any(question, VERSION_COMPARE_WORDS):
            return Route(route=ROUTE_BOTH, confidence=1.0, level="L1",
                         reason="version-comparison wording -> RM")

        matched = self._match_lookup_rule(question)
        if matched:
            return Route(route=ROUTE_RETRIEVAL, confidence=1.0, level="L1",
                         reason=f"lookup rule: {matched}")

        return Route(route=ROUTE_BOTH, confidence=0.0, level="L3",
                     reason="L1 undecided -> default RM")

    async def route_async(self, question: str, ctx: str = "") -> Route:
        """Full three-layer routing: L1 rules, L2 few-shot, L3 default.

        Identical to ``route()`` except that an L1-undecided question is
        handed to the small-tier classifier (~200 tokens) before falling
        back to RM, and unlike the sync variant it can distinguish "the
        classifier said R with confidence 0.9" from "the classifier was
        unsure". The layer order is deliberately duplicated rather than
        shared through a callback: the two entry points differ in exactly
        one step, and a shared helper would hide that the sync one silently
        skips classification.
        """
        ledger = self._route_ledger.get(_signature(question))
        if ledger and ledger.get("misses", 0) >= 2:
            return Route(route=ROUTE_BOTH, confidence=0.0, level="L3",
                         reason="signature-failure >= 2 -> escalate")
        if _has_any(question, VERSION_COMPARE_WORDS):
            return Route(route=ROUTE_BOTH, confidence=1.0, level="L1",
                         reason="version-comparison wording -> RM")
        matched = self._match_lookup_rule(question)
        if matched:
            return Route(route=ROUTE_RETRIEVAL, confidence=1.0, level="L1",
                         reason=f"lookup rule: {matched}")
        if self.llm is None:
            return Route(route=ROUTE_BOTH, confidence=0.0, level="L3",
                         reason="no llm available -> default RM")
        classified = await self._classify_route(question, ctx)
        if classified is None:
            return Route(route=ROUTE_BOTH, confidence=0.0, level="L3",
                         reason="router call failed or unparseable -> "
                                "default RM")
        if classified.confidence < 0.7:
            return Route(route=ROUTE_BOTH, confidence=classified.confidence,
                         level="L3",
                         reason=f"confidence {classified.confidence:.2f} "
                                "< 0.7 -> default RM")
        return classified

    def _match_lookup_rule(self, question: str) -> str | None:
        """First matching lookup rule, or None if vetoed/absent."""
        if _has_any(question, MULTI_HOP_MARKERS):
            return None
        for rule in LOOKUP_RULES:
            if re.search(rule["pattern"], question or "", re.IGNORECASE):
                return rule["name"]
        return None

    async def _classify_route(self, question: str,
                              ctx: str) -> Route | None:
        """L2 few-shot classification with the small tier.

        Returns None (never raises) on any failure: a router that cannot
        answer must degrade to the safe default, not take the request down
        with it.
        """
        fewshot = "\n".join(
            f"- {ex['question']} -> {ex['route']} ({ex['reason']})"
            for ex in ROUTER_EXAMPLES)
        system, user = _render_prompt(
            "knowevo_route", lang=self.lang,
            question=question, context=ctx or "(none)", fewshot=fewshot)
        try:
            raw = await self._call_llm(system, user, kind="route_llm",
                                       tier=TIER_SMALL)
        except Exception as exc:  # noqa: BLE001 - degrade, never raise
            logger.warning("route classification failed: %s", exc)
            return None
        data = _parse_json(raw)
        if not isinstance(data, dict):
            return None
        route = str(data.get("route", "")).upper()
        if route not in (ROUTE_RETRIEVAL, ROUTE_REASONING, ROUTE_BOTH):
            return None
        return Route(route=route,
                     confidence=_as_float(data.get("confidence"), 0.0),
                     level="L2", reason=str(data.get("reason", ""))[:200])

    def route_hit_feedback(self, question: str, route: str,
                           correct: bool) -> None:
        """Record whether the chosen route produced a correct answer (E6).

        Session-level signal only: it exists to escalate a question shape
        that keeps failing, not to be a durable statistic (durable route
        statistics belong in eval_run_t, T-10b).
        """
        entry = self._route_ledger.setdefault(
            _signature(question), {"hits": 0, "misses": 0})
        entry["hits" if correct else "misses"] += 1

    def route_hit_rate(self) -> float:
        """Share of feedback events where the chosen route was correct."""
        hits = sum(e["hits"] for e in self._route_ledger.values())
        total = hits + sum(e["misses"] for e in self._route_ledger.values())
        return hits / total if total else 0.0

    # ── version-pinned beam search (B2 core) ───────────────────────────

    async def multi_hop(self, question: str, seeds: list[str] | None = None,
                        depth: int | None = None, beam: int | None = None,
                        version: str | None = None,
                        as_of: datetime | None = None,
                        pin_version: bool = True,
                        versions: list[dict[str, Any]] | None = None,
                        rel_types: list[str] | None = None) -> PathSet:
        """Beam walk constrained to a knowledge version (02-tech-plan 3.2).

        The expansion loop lives here rather than in the store because the
        service is what scores and prunes between hops - exactly as the
        frozen spec puts it: "iteratively expand (GraphStore.multi_hop
        1-hop) -> score_path -> top-k". Each expansion calls
        ``store.neighbors(..., as_of=t_v)`` so the version predicate is
        applied by the store's own SQL, and every surviving path is then
        re-verified edge by edge against the same cutoff. Two checks on
        purpose: the SQL one is an optimisation, the pure one is the
        evidence that ends up on the card.

        ``pin_version=False`` runs the identical walk with the predicate
        off - the ablation switch for T-10b and for the demo's "what the
        old system would have answered" comparison.

        Excluded evidence is reported, not swallowed. A store-side cutoff
        simply returns fewer edges, so a path that *would* have existed
        leaves no trace in the walk; the boundary probe below asks the same
        frontier for its all-time neighborhood and records whatever the
        cutoff removed. Without that, ``failed`` would always be empty and
        the card could not tell the difference between "the knowledge does
        not exist" and "the knowledge exists in a version you did not ask
        about" - which is exactly the distinction B2 is about.
        """
        depth = max(1, min(int(depth if depth is not None else self.max_depth),
                           self.max_depth))
        beam = max(1, int(beam if beam is not None else self.beam))
        seeds = [s for s in (seeds or []) if s]
        clock = await self._resolve_clock(
            version if pin_version else None,
            as_of if pin_version else None, versions)
        # A requested pin is only real when the cutoff was actually
        # resolved. Without a version row (or an explicit as_of) the clock
        # falls back to now(), and pinning against now() constrains
        # nothing - reporting that as "pinned" would be exactly the kind of
        # unbacked provenance claim this layer exists to prevent.
        pinned = bool(pin_version) and clock.source != "now"
        t_v = clock.as_of if pinned else None

        result = PathSet(version_pinned=pinned, clock=clock)
        if not seeds:
            return result

        # Hop plan: an explicit rel_types argument wins (the caller knows
        # the domain); otherwise the LLM decomposes the question once, and
        # every hop of the walk follows that plan. A failed plan degrades
        # to "no relation-type filter", which widens the walk rather than
        # breaking it.
        plans = await self.plan_hops(question, seeds, rel_types=rel_types)
        paths: list[Path] = [Path(entities=[s]) for s in seeds]
        # Keyed by id(path): Path carries edge *ids* (for storage lookup)
        # and claim texts, so the EdgeCards collected during expansion are
        # tracked alongside - they are what the version re-verification
        # reads and what the chain renderer quotes.
        edges_by_path: dict[int, list[EdgeCard]] = {id(p): [] for p in paths}
        claims_by_path: dict[int, list[str]] = {id(p): [] for p in paths}

        for level in range(depth):
            frontier = [p.entities[-1] for p in paths]
            hop_plan = plans[min(level, len(plans) - 1)]
            sub = await self._expand(frontier, hop_plan, t_v)
            if t_v is not None:
                probe = await self._probe_excluded(frontier, hop_plan, clock)
                real = [e for e in probe
                        if e.invalid_edge_reason != PROBE_FAILED]
                if len(real) != len(probe):
                    result.probe_failed = True
                result.failed.extend(real)
            if not sub.edges:
                break
            by_entity: dict[str, list[EdgeCard]] = {}
            for edge in sub.edges:
                by_entity.setdefault(edge.src, []).append(edge)
                by_entity.setdefault(edge.dst, []).append(edge)

            candidates: list[Path] = []
            for path in paths:
                tail = path.entities[-1]
                for edge in by_entity.get(tail, []):
                    nxt = edge.dst if edge.src == tail else edge.src
                    if nxt in path.entities:
                        continue  # no cycles
                    child = Path(
                        entities=path.entities + [nxt],
                        edges=path.edges + [edge.id],
                        claims=path.claims + [edge.claim])
                    edges_by_path[id(child)] = (
                        edges_by_path[id(path)] + [edge])
                    claims_by_path[id(child)] = (
                        claims_by_path[id(path)] + [edge.claim])
                    candidates.append(child)

            if not candidates:
                break
            scored = [self.score_path(c, question, edges_by_path[id(c)])
                      for c in candidates]
            scored.sort(key=lambda s: s.score.total, reverse=True)
            paths = [s.path for s in scored[:beam]]
            if self._answerable(paths, question):
                result.early_stopped = True
                break

        for path in paths:
            if len(path.entities) < 2:
                # A seed that never expanded asserts nothing; reporting it
                # as a path would claim evidence the walk does not have.
                continue
            edges = edges_by_path[id(path)]
            entry = self.score_path(path, question, edges)
            entry.version_valid = (True if not pinned
                                   else path_version_valid(edges, clock))
            if not entry.version_valid:
                expired = [e for e in edges
                           if not edge_in_version(e.valid_at, e.invalid_at,
                                                  clock)]
                entry.invalid_edge_reason = _expired_reason(expired, clock)
            result.scored.append(entry)
        result.paths = [s.path for s in result.scored if s.version_valid]
        result.failed.extend(s for s in result.scored
                             if not s.version_valid)
        result.claims_by_path = {id(s.path): claims_by_path[id(s.path)]
                                 for s in result.scored}
        result.edges_by_path = {id(s.path): edges_by_path[id(s.path)]
                                for s in result.scored}
        return result

    async def _expand(self, frontier: list[str], hop_plan: HopPlan,
                      t_v: datetime | None) -> Subgraph:
        """One expansion step, version-pinned when ``t_v`` is set.

        ``as_of`` is a T-09 extension on the GraphStore seam: a store that
        predates it would raise TypeError on every pinned query, so the
        support check happens once (never per hop) and degrades to the
        current view - the path-level re-verification still carries the
        pinning, it just costs an extra filter instead of pushing work into
        SQL.
        """
        if self.store is None or not frontier:
            return Subgraph()
        kwargs: dict[str, Any] = {
            "rel_types": hop_plan.rel_types, "hop": 1, "valid_view": True}
        if t_v is not None and self._store_supports_as_of():
            kwargs["as_of"] = t_v
        return await self.store.neighbors(self.tenant_id, frontier, **kwargs)

    async def plan_hops(self, question: str, seeds: list[str],
                        rel_types: list[str] | None = None,
                        depth: int | None = None) -> list[HopPlan]:
        """Decompose the question into one HopPlan per level (LLM, mid tier).

        Returns a list rather than a single plan because hop intent changes
        along the walk (drug -> indication -> monitoring lab), so each level
        gets its own relation-type whitelist instead of one whitelist
        stretched across the whole path.

        Every failure mode degrades to an unfiltered plan: no LLM, a
        malformed reply, or relation types outside the active ontology
        vocabulary all mean "walk everything" - which is slower and less
        precise but never wrong, whereas a hallucinated relation type would
        silently return an empty graph and look like "no knowledge exists".

        ``HopPlan.reverse`` is carried through but has no effect yet: the
        graph store traverses both edge directions in one hop, so a
        reversed hop is already covered. It is kept on the plan because the
        hop planner's answer is worth recording (and a future unidirectional
        adapter would need it), not because anything acts on it.
        """
        levels = max(1, int(depth if depth is not None else self.max_depth))
        # An explicit rel_types argument still wins for level 0 (callers
        # that know their domain keep full control); later levels are
        # unfiltered so the walk can turn a corner.
        base = HopPlan(rel_types=rel_types)
        if rel_types is not None or self.llm is None:
            return [base] * levels
        vocabulary = [str(r) for r in (self.ontology.get("rel_types") or [])]
        known = set(vocabulary)
        if not known:
            # No relation vocabulary: every type the planner could propose
            # is unverifiable, and an unverifiable type must never become a
            # hard filter. The filter travels into
            # ``store.neighbors(rel_types=[...])``, so one hallucinated name
            # matches zero edges, the walk breaks at the first level and the
            # card reports "no evidence" for knowledge that does exist -
            # which is what T-26 caught the production panel doing on
            # questions the evaluation chain could reach (the two chains
            # differed only in this: production passes no ontology, so this
            # guard was disabled). "Walk everything" is the documented
            # degradation for a plan that cannot be checked, so it is the
            # only honest plan here and the planner LLM is not called.
            logger.info("no relation vocabulary available; hop plan degrades "
                        "to unfiltered (question=%s)", (question or "")[:40])
            return [base] * levels
        system, user = _render_prompt(
            "knowevo_hops", lang=self.lang, question=question,
            seeds=", ".join(seeds[:10]),
            rel_vocabulary=json.dumps(vocabulary, ensure_ascii=False))
        try:
            raw = await self._call_llm(system, user, kind="hop_plan",
                                       tier=TIER_MID)
        except Exception as exc:  # noqa: BLE001 - plan failure degrades only
            logger.warning("hop planning failed: %s", exc)
            return [base] * levels
        data = _parse_json(raw)
        if not isinstance(data, dict):
            return [base] * levels
        plans: list[HopPlan] = []
        for hop in (data.get("hops") or [])[:levels]:
            if not isinstance(hop, dict):
                plans.append(HopPlan())
                continue
            types = [str(t) for t in (hop.get("rel_types") or [])
                     if str(t) in known]
            plans.append(HopPlan(rel_types=types or None,
                                 reverse=bool(hop.get("reverse", False))))
        while len(plans) < levels:
            plans.append(HopPlan())
        return plans

    def _store_supports_as_of(self) -> bool:
        """Does the injected store accept the ``as_of`` keyword?

        Inspected once and cached: introspection is cheap but not free, and
        the answer cannot change for a given store instance (each test case
        builds its own service around its own fake).
        """
        if self._store_pins is None:
            try:
                import inspect
                params: Any = inspect.signature(
                    self.store.neighbors).parameters
                self._store_pins = ("as_of" in params
                                    or any(p.kind == p.VAR_KEYWORD
                                           for p in params.values()))
            except (TypeError, ValueError):
                self._store_pins = False
        return self._store_pins

    async def _resolve_clock(self, version: str | None, as_of: datetime | None,
                             versions: list[dict[str, Any]] | None,
                             ) -> VersionClock:
        """Resolve t_v, loading the ontology version rows when not supplied.

        The version rows are where a version's creation instant lives, so
        the caller does not have to pass it: a request naming "v1.3.0" is
        enough. A DB failure degrades to the honest "now" clock (which the
        walk then treats as unpinned) rather than failing the request.
        """
        if as_of is not None or versions is not None or not version:
            return resolve_version_clock(version, as_of=as_of,
                                         versions=versions)
        rows: list[dict[str, Any]] | None = self._version_rows
        if rows is None:
            try:
                from database.knowevo_db import OntologyVersion, _get_db_session
                with _get_db_session() as session:
                    # T-18b D1: the version's fact_cutoff (business-time
                    # upper bound of its facts) travels alongside created_at
                    # so the pin resolves against the facts' own clock.
                    rows = [
                        {"version": r.version,
                         "created_at": r.created_at,
                         "fact_cutoff": (r.metrics or {}).get("fact_cutoff")}
                        for r in session.query(OntologyVersion).filter(
                            OntologyVersion.tenant_id
                            == self.tenant_id).all()]
            except Exception as exc:  # noqa: BLE001 - fallback clock is valid
                logger.info("version rows unavailable for pinning: %s", exc)
                rows = None
        return resolve_version_clock(version, as_of=as_of, versions=rows)

    async def _probe_excluded(self, frontier: list[str], hop_plan: HopPlan,
                              clock: VersionClock) -> list[ScoredPath]:
        """Find what the version cutoff removed from this frontier.

        A pinned query returns fewer edges with no record of the ones it
        dropped, so the failure list would otherwise be empty and the card
        could not distinguish "no such knowledge" from "that knowledge
        belongs to another version". One all-time query per frontier closes
        that gap; it costs nothing extra on the happy path because it only
        runs when pinning is on, and it is the reason the ablation is
        observable at all.

        The probe reads the same edges the store has, so it never invents
        a failure: an edge is reported only when it exists and is outside
        G_v, with the cutoff reason that excluded it.

        A probe failure is reported as one synthetic entry rather than an
        empty list. Returning ``[]`` would make "the version cutoff removed
        nothing" and "we could not check what it removed" produce the same
        card, and only the second of those is unbacked. The caller filters
        the synthetic entry out of ``failed`` and surfaces it as an
        uncertainty note instead (see ``multi_hop``).
        """
        if self.store is None or not frontier:
            return []
        kwargs: dict[str, Any] = {
            "rel_types": hop_plan.rel_types, "hop": 1, "valid_view": False}
        try:
            sub = await self.store.neighbors(self.tenant_id, frontier,
                                             **kwargs)
        except Exception as exc:  # noqa: BLE001 - probe is best-effort
            logger.warning("version boundary probe failed: %s", exc)
            return [ScoredPath(path=Path(entities=list(frontier)),
                               score=PathScore(),
                               version_valid=False,
                               invalid_edge_reason="probe_failed")]
        out: list[ScoredPath] = []
        for edge in sub.edges:
            if edge_in_version(edge.valid_at, edge.invalid_at, clock):
                continue
            nxt = edge.dst if edge.src in frontier else edge.src
            path = Path(entities=[edge.src if edge.src in frontier
                                  else edge.dst, nxt],
                        edges=[edge.id], claims=[edge.claim])
            entry = ScoredPath(path=path, score=PathScore(path=path),
                               version_valid=False,
                               invalid_edge_reason=_expired_reason(
                                   [edge], clock))
            out.append(entry)
        return out

    def score_path(self, path: Path, question: str,
                   edges: list[EdgeCard] | None = None) -> ScoredPath:
        """Score one path on relevance, evidence richness and conflict.

        A path missing a claim on some edge is flagged ``evidence_missing``:
        the reasoning-path skill drops evidence-free edges (hallucination
        guard), so the score must not reward a walk that is merely long.
        """
        edges = edges or []
        claims = [c for c in (path.claims or []) if c]
        hops = max(1, len(path.entities) - 1) if path.entities else 1
        relevance = _overlap(question, " ".join(claims)) if claims else 0.0
        richness = len(claims) / hops
        if edges and not any((e.props or {}).get("evidence_id")
                             for e in edges):
            richness *= 0.5
        conflict = (sum(1 for e in edges if e.contested) / len(edges)
                    if edges else 0.0)
        return ScoredPath(
            path=path,
            score=PathScore(path=path, relevance=relevance,
                            evidence_richness=richness,
                            conflict_signal=conflict,
                            evidence_missing=len(claims) < hops))

    def _answerable(self, paths: list[Path], question: str) -> bool:
        """Early-termination test: did the walk reach a claim that covers
        the question's content words?

        The threshold is deliberately high - stopping early on a weak match
        loses the second hop that would have made the answer correct, and
        that failure looks like a wrong answer rather than a slow one.
        """
        return any(_overlap(question, claim) >= PROPOSITION_MATCH
                   for path in paths for claim in (path.claims or []))

    async def calibrate_hops(self,
                             questions: list[str],
                             seeds_for: Callable[[str], list[str]] | None = None,
                             judge: Callable[[str, PathSet], bool] | None = None,
                             depths: tuple[int, ...] = (1, 2, 3, 4),
                             testset_hash: str = "") -> HopCurve:
        """Depth grid over multi-hop questions -> accuracy/token/latency curve.

        This is what fixes KW_MULTIHOP_MAX_DEPTH with evidence rather than
        intuition (02-tech-plan 3.2, recovering the L5 leftover). The
        recommendation filters by the latency budget first: the most
        accurate depth that cannot answer inside p95 is not a
        recommendation, it is a regression.
        """
        curve = HopCurve(testset_hash=testset_hash)
        for depth in depths:
            correct = 0
            tokens = 0
            latencies: list[int] = []
            for question in questions:
                seeds = seeds_for(question) if seeds_for else []
                t0 = time.monotonic()
                result = await self.multi_hop(question, seeds=seeds,
                                              depth=depth)
                latencies.append(int((time.monotonic() - t0) * 1000))
                tokens += int(getattr(result, "used_tokens", 0) or 0)
                if judge is not None and judge(question, result):
                    correct += 1
            n = len(questions) or 1
            curve.rows.append({
                "depth": depth,
                "accuracy": correct / n,
                "tokens": tokens,
                "latency_ms": _p95(latencies),
                "n_questions": len(questions),
            })
        in_budget = [r for r in curve.rows
                     if r["latency_ms"] <= REASONING_LATENCY_BUDGET_MS]
        if in_budget:
            curve.recommended_depth = HopCurve(
                rows=in_budget).best_by("accuracy")
        elif curve.rows:
            curve.recommended_depth = int(min(
                curve.rows, key=lambda r: r["latency_ms"])["depth"])
        return curve

    # ── evidence-chain assembly and fusion (02-tech-plan 3.4) ──────────

    async def assemble_evidence(self, paths: list[Path] | PathSet,
                                doc_hits: list[DocHit] | None = None,
                                ) -> EvidenceChain:
        """Fuse the graph channel and the document channel into one chain.

        Document passages are EXTRACTED by definition (they are the source
        text); graph paths supply connectivity plus their own claim text,
        and a claim that the two channels agree on is upgraded to kg+doc.
        Nothing is deduplicated away silently: when the channels disagree,
        both items stay and the chain is marked contested, which the card
        renders as "knowledge inconsistency".
        """
        doc_hits = doc_hits or []
        chain = EvidenceChain()
        if isinstance(paths, PathSet):
            path_list = list(paths.paths)
            claims_by_path = paths.claims_by_path
            edges_by_path = paths.edges_by_path
            chain.failed_paths = list(paths.failed)
            chain.probe_failed = bool(paths.probe_failed)
            pinned = bool(paths.version_pinned)
        else:
            path_list = list(paths)
            claims_by_path = {}
            edges_by_path = {}
            pinned = False

        for path in path_list:
            claims = claims_by_path.get(id(path)) or [
                c for c in (path.claims or []) if c]
            edges = edges_by_path.get(id(path)) or []
            kg_path = _describe_path(path)
            for idx, claim in enumerate(claims):
                if not claim:
                    continue
                edge = edges[idx] if idx < len(edges) else None
                contested = bool(edge is not None and edge.contested)
                chain.items.append(EvidenceItem(
                    claim=claim,
                    provenance=Provenance(
                        doc=str((edge.props or {}).get("doc_title", ""))
                        if edge is not None else "",
                        span=str((edge.props or {}).get("span", ""))
                        if edge is not None else "",
                        kg_path=kg_path,
                        version_pinned=pinned),
                    tag=TAG_EXTRACTED,
                    source_channel=CHANNEL_KG,
                    contested=contested))
                if contested:
                    chain.contested = True

        for hit in doc_hits:
            chain.items.append(EvidenceItem(
                claim=hit.span_text,
                provenance=Provenance(doc=hit.doc_title, span=hit.span_text,
                                      kg_path=[], version_pinned=pinned),
                tag=TAG_EXTRACTED,
                source_channel=CHANNEL_DOC))

        kg_items = [i for i in chain.items if i.source_channel == CHANNEL_KG]
        doc_items = [i for i in chain.items if i.source_channel == CHANNEL_DOC]
        for kg_item in kg_items:
            for doc_item in doc_items:
                if _overlap(kg_item.claim, doc_item.claim) < PROPOSITION_MATCH:
                    continue
                if _contradicts(kg_item.claim, doc_item.claim):
                    kg_item.contested = True
                    doc_item.contested = True
                    chain.contested = True
                else:
                    kg_item.source_channel = CHANNEL_KG_DOC
        return chain

    # ── decision card (02-tech-plan 3.3) ──────────────────────────────

    async def render_card(self, question: str, chain: EvidenceChain,
                          mode: str = "full",
                          question_id: str = "",
                          clock: VersionClock | None = None,
                          ) -> DecisionCard:
        """Render the card; refuse deterministically when evidence is empty.

        The refusal branch deliberately does not call the LLM. Asking a
        model to conclude "insufficient evidence" leaves it free to invent a
        candidate instead, and the K4 X-type questions exist precisely to
        check that the system knows its own boundary: no evidence in means
        no candidates out, full stop.
        """
        clock = clock or resolve_version_clock(None)
        card = DecisionCard(
            question_id=question_id or _question_id(question),
            question=question,
            route=ROUTE_REASONING,
            knowledge_version_pinned=_chain_pinned(chain),
            knowledge_stamp=KnowledgeStamp(
                ontology_version=clock.ontology_version,
                kg_cutoff=clock.as_of.isoformat(),
                clock_source=clock.source))
        if mode == "lite":
            card.uncertainty_notes.append("lite 模式：跳过风险与反事实区")

        if not chain.has_evidence():
            card.decision = DECISION_INSUFFICIENT
            card.uncertainty_notes.append(
                "关键事实项证据全缺：检索路与推理路均未取到支撑证据，"
                "按纪律不生成候选。")
            self._apply_domain_rules(card)
            return card

        if self.llm is None:
            raise RuntimeError("render_card requires an injected llm")

        system, user = _render_prompt(
            "knowevo_card", lang=self.lang,
            question=question, domain=self.domain,
            knowledge_stamp=json.dumps(asdict(card.knowledge_stamp),
                                       ensure_ascii=False),
            paths=_render_chain(chain),
            doc_hits=_render_doc_hits(chain),
            failed_paths=_render_failed(chain))
        raw = await self._call_llm(system, user, kind="decision_card",
                                   tier=TIER_MID)
        data = _parse_json(raw)
        if not isinstance(data, dict):
            raise TypeError("decision-card LLM output was not a JSON "
                            f"object; got: {str(raw)[:200]!r}")

        for idx, cand in enumerate(data.get("candidates") or []):
            if not isinstance(cand, dict):
                continue
            for built in _build_candidates(cand, clock):
                # Counterfactual is a top-1 concern by design (one extra
                # call's worth of content, ~600 tokens); enforce it here
                # rather than trusting the prompt to have obeyed.
                if idx > 0:
                    built.counterfactual = None
                card.candidates.append(built)

        card.decision = str(
            data.get("decision") or DECISION_RECOMMEND).upper()
        if card.decision not in (DECISION_RECOMMEND, DECISION_INSUFFICIENT):
            logger.warning("unknown decision %r from llm; treating as %s",
                           card.decision, DECISION_RECOMMEND)
            card.decision = DECISION_RECOMMEND
        if card.decision == DECISION_INSUFFICIENT:
            card.candidates = []
        elif not card.candidates:
            # An unparseable decision plus zero candidates leaves nothing to
            # recommend. Keeping RECOMMEND here would produce a card that
            # asserts a recommendation it cannot name, which the wire
            # contract rejects and a reader would rightly distrust.
            logger.warning("no candidates produced; refusing instead of "
                           "recommending nothing")
            card.decision = DECISION_INSUFFICIENT
        card.uncertainty_notes.extend(
            str(n) for n in (data.get("uncertainty_notes") or []))
        for adj in data.get("conflict_adjudications") or []:
            if isinstance(adj, dict):
                card.conflict_adjudications.append(ConflictAdjudication(
                    conflict_id=str(adj.get("conflict_id", "")),
                    type=str(adj.get("type", "")),
                    resolution=str(adj.get("resolution", ""))))

        table = self._load_calibration()
        card.calibration_applied = table is not None
        if table is not None:
            for cand in card.candidates:
                cand.confidence_calibrated = self._calibrate_in_table(
                    cand.confidence_calibrated, table,
                    _card_notes_appender(card))
        else:
            card.uncertainty_notes.append(
                "未找到校准表（eval_run_t.calibration 为空）：置信度为模型"
                "自报值，未做经验校准。")

        if chain.contested:
            card.uncertainty_notes.append(
                "知识不一致：双通道或跨文档存在相互矛盾的表述，已标注"
                "而非静默取舍。")
        if chain.probe_failed:
            card.uncertainty_notes.append(
                "版本边界探测未能执行：无法确认当前版本之外是否还有"
                "被排除的旧事实，\"无差异\"在此处未经验证。")
        if mode == "lite":
            for cand in card.candidates:
                cand.risks = []
                cand.counterfactual = None
        self._apply_domain_rules(card)
        # Validate before returning: the producer-side check that catches a
        # card violating its own refusal/recommend invariants here, rather
        # than in whichever downstream consumer reads the payload first.
        try:
            self.validate_card(card)
        except ValidationError as exc:
            logger.warning("rendered card failed contract validation: %s",
                           exc)
        return card

    def _apply_domain_rules(self, card: DecisionCard) -> None:
        """Healthcare cards always carry the disclaimer (medical boundary)."""
        if self.domain == "healthcare":
            card.disclaimer = HEALTHCARE_DISCLAIMER

    def calibrate(self, raw_conf: float) -> float:
        """Ten-bucket empirical calibration lookup (ECE target <= 0.10).

        Returns the raw value unchanged when no table is loaded, and the
        card records that fact, so no card ever implies a calibration that
        never ran. Non-numeric input passes through untouched: it is a
        producer bug to be surfaced, not a number to invent.
        """
        table = self._load_calibration()
        if not table:
            return raw_conf
        try:
            conf = max(0.0, min(1.0, float(raw_conf)))
        except (TypeError, ValueError):
            return raw_conf
        return self._calibrate_in_table(conf, table)

    def _calibrate_in_table(self, conf: float, table: list[dict[str, Any]],
                            note: Callable[[str], None] | None = None,
                            ) -> float:
        """Bucket lookup that admits when it did not find a bucket.

        A confidence that matches no bucket is returned uncalibrated and
        reported, because the alternative - passing it through while the
        card says ``calibration_applied=True`` - is a card claiming a
        calibration it did not perform. A malformed bucket is skipped and
        also reported: same claim, same obligation.
        """
        for bucket in table:
            try:
                lo = float(bucket.get("lo", 0.0))
                hi = float(bucket.get("hi", 1.0))
            except (TypeError, ValueError, AttributeError):
                if note is not None:
                    note(f"校准表单桶结构不合法，已跳过：{bucket!r}")
                continue
            if lo <= conf < hi or (hi >= 1.0 and conf >= lo):
                try:
                    return float(bucket.get("empirical", conf))
                except (TypeError, ValueError):
                    if note is not None:
                        note("校准表命中的桶缺少可用 empirical 值，"
                             "保持模型自报置信度。")
                    return conf
        if note is not None:
            note(f"置信度 {conf:.2f} 未落入任何校准桶：该项保持未校准值。")
        return conf

    def validate_card(self, card: DecisionCard) -> DecisionCardContract:
        """Validate a rendered card against the wire contract.

        Raises ``pydantic.ValidationError`` when the card violates its own
        guarantees, so a producer bug surfaces at render time (or in CI)
        instead of inside a downstream consumer that just reads the table.
        """
        return DecisionCardContract.model_validate(card.to_payload())

    def validate_card_payload(self, payload: dict[str, Any],
                              ) -> DecisionCardContract:
        """Validate a serialized card read back out of decision_card_t."""
        return DecisionCardContract.model_validate(payload)

    def _load_calibration(self) -> list[dict[str, Any]] | None:
        """Load the bucket table once, from eval_run_t or the injected dict.

        A DB failure is not an error here: "no calibration yet" is a
        legitimate state before T-10b has run, and failing an entire card
        render because a metrics table is missing would be the wrong trade.

        Bucket count is checked, not assumed: the table is a ten-bucket
        curve (K4 4.1) and a table of any other length would make this
        card's confidences incomparable with every other card's. A short
        table is logged and still used - the buckets that are present are
        real measurements - but the caller's per-value "matched no bucket"
        reporting is what ultimately keeps the card honest.
        """
        if self._calibration_loaded:
            return self._calibration_table()
        self._calibration_loaded = True
        try:
            from database.knowevo_db import EvalRun, _get_db_session
            with _get_db_session() as session:
                row = (session.query(EvalRun)
                       .filter(EvalRun.tenant_id == self.tenant_id,
                               EvalRun.calibration.isnot(None))
                       .order_by(EvalRun.created_at.desc())
                       .first())
                if row is not None and row.calibration:
                    self._calibration = dict(row.calibration)
        except Exception as exc:  # noqa: BLE001 - absent table is valid state
            logger.info("calibration table unavailable: %s", exc)
            self._calibration = None
        table = self._calibration_table()
        if table is not None:
            try:
                model = CalibrationTableModel(buckets=table)
            except Exception as exc:  # noqa: BLE001 - malformed is reportable
                logger.warning("calibration table failed contract "
                               "validation: %s", exc)
            else:
                if len(model.buckets) != CALIBRATION_BUCKETS:
                    logger.warning(
                        "calibration table has %d buckets, expected %d "
                        "(K4 4.1); confidences outside the covered range "
                        "will be reported as uncalibrated",
                        len(model.buckets), CALIBRATION_BUCKETS)
        return table

    def _calibration_table(self) -> list[dict[str, Any]] | None:
        if not self._calibration:
            return None
        if isinstance(self._calibration, list):
            return self._calibration
        buckets = self._calibration.get("buckets")
        return buckets if isinstance(buckets, list) else None

    # ── persistence and rerun (Q2 ledger material) ─────────────────────

    async def persist(self, card: DecisionCard,
                      session_id: Any | None = None) -> Any:
        """Write the card to decision_card_t; returns the new row id."""
        from database.knowevo_db import DecisionCard as DecisionCardRow
        from database.knowevo_db import create_row
        payload = card.to_payload()
        row = create_row(
            DecisionCardRow,
            tenant_id=self.tenant_id,
            question_id=card.question_id,
            session_id=session_id,
            payload=payload,
            knowledge_stamp=payload["knowledge_stamp"],
            needs_rerun=bool(card.decision == DECISION_INSUFFICIENT),
        )
        return row["id"]

    async def rerun_marked(self, tenant_id: str | None = None,
                           handler: Callable[[dict[str, Any]], Any] | None = None,
                           ) -> list[Any]:
        """Re-render needs_rerun cards under the current knowledge stamp.

        ``handler`` receives the stored card row and returns a fresh
        DecisionCard. There is no sensible default, because re-rendering
        needs the question's evidence pipeline; refusing loudly beats
        "re-running" a card by copying it.

        The old-vs-new conclusion diff is recorded on the new card, which is
        the Q2 ledger's raw material: the entire point of an evolving system
        is showing that the same question now gets a different answer, and
        by how much.
        """
        tenant = tenant_id or self.tenant_id
        if handler is None:
            raise ValueError(
                "rerun_marked requires a handler that re-renders a card; "
                "there is no default re-render (the evidence pipeline is "
                "the caller's)")
        from database.knowevo_db import DecisionCard as DecisionCardRow
        from database.knowevo_db import _get_db_session, create_row
        with _get_db_session() as session:
            rows = [{"id": r.id, "payload": dict(r.payload or {}),
                     "question_id": r.question_id}
                    for r in session.query(DecisionCardRow).filter(
                        DecisionCardRow.tenant_id == tenant,
                        DecisionCardRow.needs_rerun.is_(True)).all()]
        new_ids: list[Any] = []
        for row in rows:
            fresh = await handler(row)
            if fresh is None:
                continue
            diff = _card_diff(row["payload"], fresh.to_payload())
            fresh.uncertainty_notes.append(
                f"重算差异：{json.dumps(diff, ensure_ascii=False)}")
            payload = fresh.to_payload()
            new_row = create_row(
                DecisionCardRow, tenant_id=tenant,
                question_id=fresh.question_id,
                payload=payload,
                knowledge_stamp=payload["knowledge_stamp"],
                needs_rerun=False, rerun_of=row["id"])
            new_ids.append(new_row["id"])
        return new_ids

    # ── llm plumbing ───────────────────────────────────────────────────

    async def _call_llm(self, system: str, user: str, *, kind: str,
                        tier: str, temperature: float = 0.0) -> str:
        """Await the injected callable and return its raw text.

        The contract is ``await llm(prompt, *, kind, tier, temperature)``
        (frozen by kg_service, implemented by llm_client.LlmRouter); the
        prompt carries system+user already, matching what that contract
        expects.
        """
        if self.llm is None:
            raise RuntimeError("no llm injected")
        prompt = f"{system}\n\n{user}" if system else user
        result = await self.llm(prompt, kind=kind, tier=tier,
                                temperature=temperature)
        if result is None:
            raise ValueError(f"llm returned no content for kind={kind}")
        return str(result)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _parse_json(raw: Any) -> Any:
    """Parse an LLM JSON reply, tolerating markdown fences.

    Models still wrap JSON in ``` fences despite the prompt asking them not
    to; stripping the fence is cheaper and more reliable than a retry.
    """
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", text)).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _p95(values: list[int]) -> int:
    """Nearest-rank p95; 0 for an empty sample."""
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return ordered[idx]


def _question_id(question: str) -> str:
    """Stable short id for a question (card lookup / rerun key)."""
    import hashlib
    return hashlib.sha256((question or "").encode()).hexdigest()[:16]


def _chain_pinned(chain: EvidenceChain) -> bool:
    """Did any evidence item come from a version-pinned walk?

    Card-level flag derived from the items rather than passed in: a caller
    that assembles a chain by hand cannot then assert a pinning the items
    do not carry.
    """
    return any(i.provenance.version_pinned for i in chain.items)


def _expired_reason(expired: Iterable[EdgeCard],
                    clock: VersionClock) -> str:
    """Human-readable reason a path left G_v (goes on the card verbatim)."""
    expired = list(expired)
    return (f"{len(expired)} edge(s) outside version "
            f"{clock.ontology_version or 'unpinned'} at "
            f"{clock.as_of.isoformat()}: "
            + "; ".join(f"{e.rel_type}(valid {e.valid_at} -> "
                        f"{e.invalid_at or 'open'})"
                        for e in expired[:3]))


def _describe_path(path: Path) -> list[str]:
    """Readable alternating walk: entity, claim, entity, claim, ...

    The relation *type* is not on Path (only the edge id is), so the walk
    renders as entity/claim pairs - which is what a human reviewer reads
    anyway. The structured edge ids stay in the path for machine consumers.
    """
    out: list[str] = []
    for idx, sid in enumerate(path.entities or []):
        out.append(str(sid))
        if idx < len(path.claims or []) and path.claims[idx]:
            out.append(str(path.claims[idx]))
    return out


def _render_chain(chain: EvidenceChain) -> str:
    """Rendering of the KG channel for the card prompt."""
    lines = []
    for item in chain.items:
        if item.source_channel == CHANNEL_DOC:
            continue
        lines.append(
            f"- claim: {item.claim}\n"
            f"  tag: {item.tag}; channel: {item.source_channel}; "
            f"contested: {item.contested}\n"
            f"  kg_path: {' | '.join(item.provenance.kg_path)}\n"
            f"  version_pinned: {str(item.provenance.version_pinned).lower()}")
    return "\n".join(lines) if lines else "(no graph path evidence)"


def _render_doc_hits(chain: EvidenceChain) -> str:
    lines = [f"- [{i.provenance.doc}] {i.claim}"
             for i in chain.items if i.source_channel == CHANNEL_DOC]
    return "\n".join(lines) if lines else "(no document hits)"


def _render_failed(chain: EvidenceChain) -> str:
    lines = []
    for entry in chain.failed_paths[:3]:
        reason = getattr(entry, "invalid_edge_reason", "") or "dead end"
        path = getattr(entry, "path", None)
        entities = " -> ".join(path.entities) if path is not None else ""
        lines.append(f"- {entities}: {reason}")
    return "\n".join(lines) if lines else "(none)"


def _build_candidates(cand: dict[str, Any],
                      clock: VersionClock) -> list[Candidate]:
    """Build Candidates from one LLM candidate object.

    Returns a list (usually of one) so a malformed entry yields nothing
    rather than a partially-populated candidate: a card showing an option
    with no evidence chain is worse than one omitting it.
    """
    option = str(cand.get("option", "")).strip()
    if not option:
        return []
    items: list[EvidenceItem] = []
    for item in cand.get("evidence_chain") or []:
        if not isinstance(item, dict):
            continue
        prov = item.get("provenance")
        if not isinstance(prov, dict):
            prov = {}
        pinned = bool(prov.get("version_pinned", False))
        # A claim cannot be pinned more tightly than the run's own clock:
        # an LLM echoing true while the walk was unpinned would be claiming
        # provenance the run never had.
        if pinned and clock.ontology_version is None:
            pinned = False
        items.append(EvidenceItem(
            claim=str(item.get("claim", "")),
            provenance=Provenance(
                doc=str(prov.get("doc", "")),
                span=str(prov.get("span", "")),
                kg_path=[str(p) for p in (prov.get("kg_path") or [])],
                version_pinned=pinned),
            tag=str(item.get("tag") or TAG_EXTRACTED).upper(),
            source_channel=str(item.get("source_channel") or CHANNEL_KG)))
    cf = cand.get("counterfactual")
    counterfactual = None
    if isinstance(cf, dict) and cf.get("not_choose"):
        counterfactual = Counterfactual(
            not_choose=str(cf.get("not_choose")),
            tag=str(cf.get("tag") or TAG_INFERRED).upper())
    return [Candidate(
        option=option,
        score=_as_float(cand.get("score"), 0.0),
        confidence_calibrated=_as_float(
            cand.get("confidence_calibrated"), 0.0),
        evidence_chain=items,
        risks=[str(r) for r in (cand.get("risks") or [])],
        counterfactual=counterfactual)]


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _card_notes_appender(card: DecisionCard) -> Callable[[str], None]:
    """Collect calibration degradations onto the card, deduplicated.

    A per-candidate note would repeat the same sentence once per candidate
    (the failure mode is a property of the table, not of a candidate), so
    identical notes collapse to one line.
    """
    def note(text: str) -> None:
        if text not in card.uncertainty_notes:
            card.uncertainty_notes.append(text)
        if "校准" in text:
            card.calibration_applied = False
    return note


def _card_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Old-vs-new conclusion diff for a rerun (Q2 ledger material)."""
    old_opts = [c.get("option") for c in (old.get("candidates") or [])]
    new_opts = [c.get("option") for c in (new.get("candidates") or [])]
    return {
        "decision": [old.get("decision"), new.get("decision")],
        "candidates": [old_opts, new_opts],
        "changed": (old.get("decision") != new.get("decision")
                    or old_opts != new_opts),
        "knowledge_stamp": [old.get("knowledge_stamp"),
                            new.get("knowledge_stamp")],
    }
