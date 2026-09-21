"""E2 ablation runner (T-22): A1-A4 four-level x question-type matrix plus
the E8 version-pin on/off arms.

02-technical-plan 3.5: the same testset, the same generation model and
prompt, four escalating configurations -

    A1_pure_rag   document retrieval only (the T-10a-2 baseline, reused
                  unchanged via ``eval_e1.run_question``)
    A2_graph      + kg_search (1-hop neighbourhood): the graph channel is
                  fused into the retrieval context through
                  ``DecisionService.assemble_evidence`` (doc-level + graph
                  two-channel fusion, disagreements marked contested) -
                  no ad-hoc fusion is written here
    A3_multihop   + multi-hop beam walk, direct
                  ``DecisionService.multi_hop`` call (B3: no MCP round
                  trip; the MCP kg_search handler is a current-view tool
                  without version semantics, so the pinned arms cannot go
                  through it)
    A4_full       + router (L1/L2/L3) + dual-channel fusion + decision
                  card rendering (counterfactual included) + version
                  pinning through the ``version_pin`` single entry

Each (level, pin-arm) pair writes one ``eval_run_t`` row (INSERT-only,
``config.ablation_level`` in the frozen enum) and one cost-ledger row.
Aggregation reuses ``eval_e1.summarize`` unchanged (honest denominators:
acc always alongside n_judged, zero-judged types reported as
``insufficient_data``) and adds the by_type x by_level cross table and
Wilson 95% CIs (eval_v1).

E8 version pin on/off (the "evolvable" hard evidence): A4 runs twice,
``pin on`` walks the graph as of an explicit cutoff (resolved through
``version_pin.resolve_version_clock`` - the single pin entry point) and
``pin off`` runs the identical pipeline with the predicate off. In an
explicit E8 invocation (``--pin on,off``) the judge gold follows the arm
(readiness section IV): the pin-on arm is graded against ``answer_old``
and the pin-off arm against ``answer_new`` for V questions (the rubric is
narrowed to the key facts carried by the arm's gold); F/M/X golds are
edition-free and pass through unchanged. Headline A4 runs (single pin)
use the standard rubric so the cross table stays comparable with A1-A3.

Honesty contract (iron rule 6, inherited from T-18c):
  * acc / n_judged are always reported together; platform faults stay out
    of the denominator, runner errors (count_fail) stay IN it;
  * a question-type cell with zero judged runs is reported
    ``insufficient_data``, never dropped;
  * the graph channel records its own diagnostics (n_seeds / n_edges /
    n_paths / errors) so an empty graph is visible, not silent - whatever
    the build-tenant graph happens to contain is part of the result, not a
    footnote to hide. That content is volatile (initial ingestion, then
    2020/2024 guideline windows), so the report measures it every run into
    ``data_reality`` instead of freezing a snapshot in prose; the
    historical "graph was empty" observation now lives in ``E8_CAVEAT``
    explicitly dated, never as an unqualified claim.

CLI (from backend/):
    python -m services.knowevo.pipeline.ablation \
        --levels A1,A2,A3,A4 --runs 3 --top-k 5 --pace 8
    python -m services.knowevo.pipeline.ablation --levels A4 \
        --pin on,off --types V,F --runs 3

The report file is written incrementally after every (level, arm) pair
and is resumable: re-running the same command skips pairs already
recorded (unless --force), which is what makes the 45-minute wall-clock
budget checkpoint/resume clean.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from services.knowevo.decision_service import DecisionService
from services.knowevo.e1_retrieval import (
    Retriever,
    parse_corpus,
    retrieve_context,
)
from services.knowevo.pipeline import eval_e1
from services.knowevo.pipeline.eval_e1 import (
    DEFAULT_TENANT,
    GENERATE_TIER,
    JUDGE_TIER,
    MIN_CALL_INTERVAL_SECONDS,
    _resolved_model_id,
    persist_eval_run,
    run_question,
    run_question_with_context,
    summarize,
    trace_answer_cite,
)
from services.knowevo.pipeline.eval_v1 import (
    _testset_hash,
    cross_table,
    wilson_interval,
)
from services.knowevo.version_pin import resolve_version_clock

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
CORPUS_ROOT = REPO_ROOT / "competition" / "corpus"
REPORT_PATH = (REPO_ROOT / "competition" / "deliverables"
               / "e2-ablation-report.json")
COST_LEDGER_PATH = REPO_ROOT / "competition" / "docs" / "cost-ledger.md"

# Frozen level enum for eval_run_t.config.ablation_level.
ABLATION_LEVELS = ("A1_pure_rag", "A2_graph", "A3_multihop", "A4_full")
LEVEL_ALIASES = {"A1": "A1_pure_rag", "A2": "A2_graph",
                 "A3": "A3_multihop", "A4": "A4_full"}

# Multi-hop shape (readiness section VI step 4: depth=2, beam=3).
MULTIHOP_DEPTH = 2
MULTIHOP_BEAM = 3
SEED_TOP_K = 3
MAX_SEED_LOOKUPS = 12

# E8 default pin cutoff. The corpus carries two guideline editions
# (guide-2020 published_at=2021-04-01, guide-2024 published_at=2025-01-01,
# both T-18b-backfilled and traceable to registry license_note); a named
# snapshot "as of 2024-06-01" sits between them, so pinning cuts exactly
# the 2024-edition graph facts. Overridable via --pin-as-of; the resolved
# clock (source, instant) is recorded in every report row.
DEFAULT_PIN_AS_OF = "2024-06-01"

# D1 gate instants (T-18b discriminative check, run through the version_pin
# predicate itself before any ablation arm fires).
D1_CLOCKS = (("t_v_2022_01_01", datetime(2022, 1, 1, tzinfo=UTC)),
             ("t_v_2024_06_01", datetime(2024, 6, 1, tzinfo=UTC)),
             ("t_v_2025_06_01", datetime(2025, 6, 1, tzinfo=UTC)))

TASK_REF = "T-22"

# Question-type axis of the cross table (mirrors eval_e1.summarize).
QTYPES = ("F", "M", "V", "X")

E8_CAVEAT = (
    "数据现实一律以 report['data_reality'] 为准——它每次运行实测（D1 判别性"
    "计数 + 构建租户图谱规模），本字段因此**不复述任何会过期的快照数字**"
    "（r21 修正：此前这里硬编码了 2026-09-19 的\u201c图谱为空\u201d快照，"
    "与同一份 JSON 里 data_reality 的实测值自相矛盾）。\n"
    "历史观察（2026-09-19 真库勘察，当时构建租户 6756b0ab 图谱为空、"
    "0 实体 / 0 关系）：此时两臂收到完全相同的证据（仅文档通道），E8 的 Δ "
    "衡量的是\u201c钉住机制在真实语料图摄取前不可观测\u201d——机制证明由"
    "判别性单测 TestDiscriminativeVersionPin 与 PG 集成测试 "
    "test_discriminative_version_pin_on_real_db 承载，不在此粉饰。\n"
    "恒久约束：两臂可能来自不同的 invocation（配额窗口分段续跑，见 "
    "report['resume']），因此 Δ 只有在 "
    "e8['arm_vintage']['same_invocation'] 为 true 时才是同一次受控对比；为 "
    "false 时它是两个时间点、可能两个图谱状态下的并置，不得当作配对实验读。"
)


# ---------------------------------------------------------------------------
# Seed extraction + graph channel (B1/B2: wire the existing capabilities)
# ---------------------------------------------------------------------------

_ASCII_TERM = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")


def extract_seed_terms(question: str, max_terms: int = MAX_SEED_LOOKUPS
                       ) -> list[str]:
    """Deterministic seed terms for the kg entity-lookup channel.

    ASCII words plus CJK runs; a CJK run longer than 6 chars also yields
    3-char sliding windows (step 2) - the store's ILIKE lookup matches
    names *containing* the query, so a query must be a substring of an
    entity name for the name to be found. Order-preserving, deduped,
    capped - the same few terms for the same question on every run, which
    is what keeps the ablation reproducible.
    """
    terms: list[str] = list(_ASCII_TERM.findall(question or ""))
    for run in _CJK_RUN.findall(question or ""):
        terms.append(run)
        if len(run) > 6:
            terms.extend(run[i:i + 3] for i in range(0, len(run) - 2, 2))
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out[:max_terms]


async def seed_entities(question: str, tenant_id: str, store: Any
                        ) -> tuple[list[str], list[str]]:
    """Entity lookup per seed term -> distinct stable_ids (kg_search path).

    Mirrors the MCP kg_search handler's lookup step (``store.entity_lookup``
    then ``neighbors``) without the MCP round trip. Returns (ids, errors):
    lookup failures are recorded, never swallowed - an empty graph must be
    distinguishable from a broken one.
    """
    errors: list[str] = []
    ids: list[str] = []
    seen: set[str] = set()
    for term in extract_seed_terms(question):
        try:
            cards = await store.entity_lookup(tenant_id, term,
                                              top_k=SEED_TOP_K)
        except Exception as exc:  # noqa: BLE001 - channel errors are data
            errors.append(f"lookup({term}): {exc}"[:120])
            continue
        for card in cards:
            if card.stable_id not in seen:
                seen.add(card.stable_id)
                ids.append(card.stable_id)
    return ids, errors


class _CountingLLM:
    """llm-contract wrapper that feeds the token counters.

    The decision layer's frozen contract returns str only, so the card
    render / hop-plan / route calls would otherwise vanish from the cost
    ledger. This wrapper routes through ``call_with_usage`` and accumulates
    - the ledger keeps its measured-only rule (never estimated).

    Empty content is retried here exactly as the E1 contract retries
    platform faults (E0 limitation 3: the free-tier gateway intermittently
    returns empty completions under load): every attempt is metered and
    logged, and exhaustion raises the same ValueError the decision layer
    expects, so the caller's count_fail accounting is unchanged.
    """

    EMPTY_RETRIES = 4
    EMPTY_BACKOFF_SECONDS = 5.0

    def __init__(self, router: Any):
        self.router = router
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    async def __call__(self, prompt: str, *, kind: str, tier: str,
                       temperature: float = 0.0) -> str:
        content: str | None = None
        usage: dict[str, int] | None = None
        for attempt in range(self.EMPTY_RETRIES):
            self.calls += 1
            content, usage = await self.router.call_with_usage(
                prompt, kind=f"ablation_{kind}", tier=tier,
                temperature=temperature)
            if usage:
                self.tokens_in += int(usage.get("input_tokens", 0) or 0)
                self.tokens_out += int(usage.get("output_tokens", 0) or 0)
            if content is not None and str(content).strip():
                return str(content)
            logger.warning("empty llm content for kind=%s (attempt %d/%d)",
                           kind, attempt + 1, self.EMPTY_RETRIES)
            if attempt < self.EMPTY_RETRIES - 1:
                await asyncio.sleep(self.EMPTY_BACKOFF_SECONDS)
        raise ValueError(f"llm returned no content for kind={kind} "
                         f"after {self.EMPTY_RETRIES} attempts")


def _edge_path(edge: Any) -> Any:
    """One graph edge as a single-hop Path (the A2 neighbourhood shape)."""
    from services.knowevo.graph_store import Path as GraphPath

    return GraphPath(entities=[edge.src, edge.dst], edges=[edge.id],
                     claims=[edge.claim])


def _doc_hits(retriever: Retriever, question: str, top_k: int,
              evidence: list[dict[str, Any]]) -> list[Any]:
    """DocHit list aligned with ``retrieve_context``'s evidence records.

    ``retrieve_context`` (reused verbatim for the doc channel so A2/A3/A4
    read the exact A1 context) does not expose chunk text, but its search
    is deterministic - re-running it yields the same hits in the same
    order, so evidence[i] aligns with hits[i].
    """
    from services.knowevo.schemas import DocHit

    hits = retriever.search(question, top_k=top_k)
    out: list[Any] = []
    for i, rec in enumerate(evidence):
        if i >= len(hits):
            break
        chunk = hits[i].chunk
        out.append(DocHit(doc_id=rec.get("doc_id"),
                          doc_title=chunk.title,
                          span_text=chunk.text,
                          score=float(rec.get("score") or 0.0)))
    return out


def _fuse(context: str, evidence: list[dict[str, Any]],
          chain: Any) -> tuple[str, list[dict[str, Any]], str]:
    """Append the graph channel to the doc context and evidence records.

    The doc blocks stay exactly as A1 rendered them (same generator prompt,
    comparable [n] citations); graph claims continue the numbering. KG
    records honestly carry ``chunk_idx=None / span_hash=None`` - they are
    graph facts, not chunk hits, so they count as incomplete in
    ``trace_machine`` instead of being dressed up as chunk locators.
    Returns ``(fused_context, combined_records, judge_chain_text)``.
    """
    from services.knowevo.schemas import CHANNEL_DOC, CHANNEL_KG, CHANNEL_KG_DOC

    records: list[dict[str, Any]] = [dict(r, channel="doc") for r in evidence]
    blocks: list[str] = []
    next_rank = len(evidence) + 1
    for item in chain.items:
        if item.source_channel == CHANNEL_DOC:
            continue  # doc claims are already in the context verbatim
        claim = (item.claim or "").strip()
        if not claim:
            continue
        is_kg = item.source_channel in (CHANNEL_KG, CHANNEL_KG_DOC)
        path_txt = " -> ".join(str(p) for p in item.provenance.kg_path[:4])
        records.append({
            "rank": next_rank,
            "channel": "kg" if is_kg else "doc",
            "title": f"kg: {path_txt}" if is_kg else item.provenance.doc,
            "doc_id": item.provenance.doc or "",
            "chunk_idx": None,
            "span_hash": None,
            "score": None,
            "split": "kg" if is_kg else "doc",
            "claim": claim,
            "contested": bool(item.contested),
            "version_pinned": bool(item.provenance.version_pinned),
        })
        if is_kg:
            suffix = "；标注: 知识不一致(contested)" if item.contested else ""
            blocks.append(f"[{next_rank}] （图谱）{claim}{suffix}")
            next_rank += 1
    fused = context
    if blocks:
        fused = (context + "\n\n【图谱路径（kg_search/多跳检索，供交叉核对；"
                 "与文献片段冲突时以下列 contested 标注为准）】\n"
                 + "\n".join(blocks))
    return fused, records, _judge_chain_text(records)


def _judge_chain_text(records: list[dict[str, Any]]) -> str:
    """One numbered evidence list for the judge (doc locators + kg paths)."""
    if not records:
        return "(no passages retrieved)"
    lines: list[str] = []
    for rec in records:
        if rec.get("channel") == "kg":
            lines.append(f"[{rec['rank']}] (图谱) {rec.get('claim', '')} "
                         f"path={rec.get('title', '')}"
                         + (" [contested]" if rec.get("contested") else ""))
        else:
            lines.append(
                f"[{rec['rank']}] {rec.get('title', '')} "
                f"(doc={rec.get('doc_id')}, chunk={rec.get('chunk_idx')}, "
                f"score={rec.get('score')}, split={rec.get('split')})")
    return "; ".join(lines)


def arm_item(item: dict[str, Any], pin_on: bool) -> dict[str, Any]:
    """E8 executor extension: judge gold follows the arm (readiness IV).

    The pin-on arm walks the graph as of the old cutoff, so it is graded
    against ``answer_old``; the pin-off arm sees the current view and is
    graded against ``answer_new``. The rubric is narrowed to the key facts
    that appear in the arm's gold (V-004's two facts each belong to one
    edition); when old == new (4 of 5 seed V questions) or narrowing would
    empty the list, the rubric passes through unchanged - the n=1
    limitation is reported, never engineered around.
    F/M/X golds are edition-free and pass through unchanged.
    """
    ans = item.get("answer")
    if item.get("type") != "V" or not isinstance(ans, dict):
        return item
    gold = ans.get("answer_old" if pin_on else "answer_new")
    rubric = dict(item.get("rubric") or {})
    key_facts = [str(k) for k in (rubric.get("key_facts") or [])]
    matched = [k for k in key_facts if k in str(gold or "")]
    if matched and len(matched) < len(key_facts):
        rubric["key_facts"] = matched
    out = dict(item)
    out["rubric"] = rubric
    out["arm_gold"] = gold
    return out


# ---------------------------------------------------------------------------
# Channel construction + per-level runners
# ---------------------------------------------------------------------------

async def build_channel(level: str, item: dict[str, Any], *, tenant_id: str,
                        store: Any, svc: Any, retriever: Retriever,
                        top_k: int, pin_on: bool,
                        dt_pin_as_of: datetime | None,
                        ) -> tuple[str, list[dict[str, Any]], Any, str, dict]:
    """Build (context, records, chain, chain_text, kg_info) for one question.

    Doc channel: ``retrieve_context`` verbatim (A1-identical). Graph
    channel (A2/A3/A4): seeds -> 1-hop neighbourhood (A2) or multi-hop
    walk (A3/A4) -> ``DecisionService.assemble_evidence`` fusion. The
    version pin goes through the ``version_pin`` single entry:
    ``resolve_version_clock(None, as_of=...)`` for the pin-on arm,
    ``pin_version=False`` for the off arm.
    """
    question = item["question"]
    context, evidence = retrieve_context(retriever, question, top_k=top_k)
    kg_info: dict[str, Any] = {"seeds": [], "n_seeds": 0, "n_edges": 0,
                               "n_paths": 0, "pinned": False,
                               "clock_source": None, "kg_cutoff": None,
                               "errors": []}
    doc_hits = _doc_hits(retriever, question, top_k, evidence)
    if level == "A1_pure_rag":
        return context, evidence, None, None, kg_info

    from services.knowevo.schemas import EvidenceChain, PathSet

    chain = None
    try:
        seeds, errors = await seed_entities(question, tenant_id, store)
        kg_info["seeds"] = seeds
        kg_info["n_seeds"] = len(seeds)
        kg_info["errors"].extend(errors)
        if level == "A2_graph":
            sub = (await store.neighbors(tenant_id, seeds, hop=1,
                                         valid_view=True)
                   if seeds else None)
            edges = list(sub.edges) if sub is not None else []
            kg_info["n_edges"] = len(edges)
            # Same structure a multi_hop PathSet carries, so the fusion (and
            # the contested flag on the EdgeCard) flows through
            # assemble_evidence exactly as it does for A3/A4.
            paths = [_edge_path(e) for e in edges]
            pathset = PathSet(
                paths=paths,
                claims_by_path={id(p): [c for c in p.claims if c]
                                for p in paths},
                edges_by_path={id(p): [edges[i]] for i, p in
                               enumerate(paths)})
            chain = await svc.assemble_evidence(pathset, doc_hits=doc_hits)
        else:  # A3_multihop / A4_full: direct DecisionService.multi_hop (B3)
            clock = None
            if pin_on:
                # version_pin single entry (B4): an explicit as_of resolves
                # deterministically (source="explicit"); multi_hop re-resolves
                # the same clock internally from the same as_of.
                clock = resolve_version_clock(None, as_of=dt_pin_as_of)
            pathset = await svc.multi_hop(
                question, seeds=seeds, depth=MULTIHOP_DEPTH,
                beam=MULTIHOP_BEAM,
                as_of=clock.as_of if clock is not None else None,
                pin_version=pin_on)
            kg_info["n_paths"] = len(pathset.paths)
            kg_info["pinned"] = bool(pathset.version_pinned)
            if pathset.clock is not None:
                kg_info["clock_source"] = pathset.clock.source
                kg_info["kg_cutoff"] = pathset.clock.as_of.isoformat()
            chain = await svc.assemble_evidence(pathset, doc_hits=doc_hits)
    except Exception as exc:  # noqa: BLE001 - channel failure is a datum
        kg_info["errors"].append(f"channel: {exc}"[:200])
        logger.warning("graph channel failed for %s: %s", item.get("id"), exc)
        chain = EvidenceChain()

    fused, records, chain_text = _fuse(context, evidence, chain)
    return fused, records, chain, chain_text, kg_info


def _card_answer(card: Any) -> tuple[str, dict[str, Any]]:
    """The judged answer surface of a decision card (honest refusal too)."""
    if card.candidates:
        top = card.candidates[0]
        claims = [i.claim for i in top.evidence_chain if i.claim]
        text = top.option
        if claims:
            text += "（依据：" + "；".join(claims[:3]) + "）"
        meta = {"decision": card.decision,
                "n_candidates": len(card.candidates)}
    else:
        notes = "; ".join(card.uncertainty_notes[:2])
        text = f"证据不足，无法给出推荐。{notes}"
        meta = {"decision": card.decision, "n_candidates": 0}
    return text, meta


async def _run_graph_question(
        level: str, item: dict[str, Any], *, router, svc: Any,
        retriever: Retriever, tenant_id: str, runs: int, top_k: int,
        lang: str, on_unexpected: str, pin_on: bool, arm_gold: bool,
        dt_pin_as_of: datetime | None,
        counting_llm: _CountingLLM | None = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One question through A2/A3/A4 with the shared scoring contract.

    A2/A3: the fused context feeds the same E1 answer prompt (same
    generation model + prompt across levels) through
    ``run_question_with_context``. A4: the card render IS the generation
    step - the loop mirrors the shared contract but replaces generation
    with ``svc.render_card``, judging through the same ``_judge_once`` so
    the scoring semantics cannot drift.
    """
    qid = item["id"]
    question = item["question"]
    judge_item = arm_item(item, pin_on) if arm_gold else item
    pre_in = pre_out = 0
    if counting_llm is not None:
        pre_in, pre_out = counting_llm.tokens_in, counting_llm.tokens_out
    fused, records, chain, chain_text, kg_info = await build_channel(
        level, item, tenant_id=tenant_id, store=svc.store, svc=svc,
        retriever=retriever, top_k=top_k, pin_on=pin_on,
        dt_pin_as_of=dt_pin_as_of)
    channel_tokens_in = channel_tokens_out = 0
    if counting_llm is not None:
        channel_tokens_in = counting_llm.tokens_in - pre_in
        channel_tokens_out = counting_llm.tokens_out - pre_out

    if level == "A4_full":
        per_run: list[dict[str, Any]] = []
        latencies: list[float] = []
        citation_traces: list[dict[str, Any]] = []
        tokens_in = channel_tokens_in
        tokens_out = channel_tokens_out
        route_txt = None
        route_in = route_out = 0
        if counting_llm is not None:
            route_in, route_out = counting_llm.tokens_in, counting_llm.tokens_out
        try:
            route = await svc.route_async(question)
            route_txt = f"{route.route}/{route.level}:{route.reason}"[:120]
        except Exception as exc:  # noqa: BLE001 - degrade, record honestly
            route_txt = f"route_error:{exc}"[:120]
        if counting_llm is not None:
            tokens_in += counting_llm.tokens_in - route_in
            tokens_out += counting_llm.tokens_out - route_out
        for run_idx in range(runs):
            t0 = time.monotonic()
            card_in = counting_llm.tokens_in if counting_llm else 0
            card_out = counting_llm.tokens_out if counting_llm else 0
            card = None
            card_exc: Exception | None = None
            for attempt in range(2):
                try:
                    card = await svc.render_card(
                        question, chain, mode="full", question_id=qid,
                        clock=(resolve_version_clock(None,
                                                     as_of=dt_pin_as_of)
                               if pin_on else None))
                    break
                except TypeError as exc:
                    # non-JSON LLM output is a model flake, not a code bug:
                    # one retry, then the same accounting as any failure
                    card_exc = exc
                    logger.warning("card output unparseable for %s run %d "
                                   "(attempt %d): %s", qid, run_idx,
                                   attempt + 1, str(exc)[:120])
                except Exception as exc:  # noqa: BLE001 - recorded verbatim
                    card_exc = exc
                    break
            if card is None:
                assert card_exc is not None
                if on_unexpected == "count_fail":
                    per_run.append(
                        {"question_id": qid, "pass": 0, "run": run_idx,
                         "type": item.get("type"),
                         "error": "runner_error", "platform_fault": False,
                         "reason": f"card render runner error: "
                                   f"{card_exc}"[:200]})
                    continue
                raise card_exc
            answer, _card_meta = _card_answer(card)
            if counting_llm:
                tokens_in += counting_llm.tokens_in - card_in
                tokens_out += counting_llm.tokens_out - card_out
            citation_traces.append(
                trace_answer_cite(question, records, answer))
            entry, judge_usage = await eval_e1._judge_once(
                router, judge_item, answer, chain_text, qid=qid,
                run_idx=run_idx, lang=lang, on_unexpected=on_unexpected,
                judge_kind="ablation_judge", t0=t0,
                citation_trace=citation_traces[-1])
            per_run.append(entry)
            if judge_usage is not None:
                tokens_in += judge_usage.get("input_tokens", 0)
                tokens_out += judge_usage.get("output_tokens", 0)
            if entry.get("latency_s"):
                latencies.append(entry["latency_s"])
        detail: dict[str, Any] = {
            "question_id": qid,
            "type": item.get("type"),
            "question": question,
            "trace_completeness": eval_e1.trace_completeness(records),
            "trace_answer_cite": eval_e1._aggregate_citation_traces(
                citation_traces),
            "evidence": records,
            "context_chars": len(fused),
            "p50_latency_s": (round(statistics.median(latencies), 2)
                              if latencies else 0.0),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "kg_channel": kg_info,
            "route": route_txt,
        }
        return per_run, detail

    # A2/A3: shared generate->judge loop over the fused context.
    per_run, detail = await run_question_with_context(
        router, judge_item, fused, records, runs=runs, lang=lang,
        on_unexpected=on_unexpected, gen_kind="ablation_answer",
        judge_kind="ablation_judge", chain_text=chain_text)
    detail["kg_channel"] = kg_info
    detail["tokens_in"] += channel_tokens_in
    detail["tokens_out"] += channel_tokens_out
    return per_run, detail


async def run_level(level: str, questions: list[dict[str, Any]], *,
                    router, retriever: Retriever, store: Any = None,
                    llm: Any = None, runs: int = 3, top_k: int = 5,
                    lang: str = "zh", on_unexpected: str = "count_fail",
                    pin_on: bool = False, arm_gold: bool = False,
                    dt_pin_as_of: datetime | None = None,
                    ontology: dict[str, Any] | None = None,
                    tenant_id: str = DEFAULT_TENANT,
                    budget_deadline: float | None = None,
                    prev_runs: list[dict[str, Any]] | None = None,
                    prev_details: list[dict[str, Any]] | None = None,
                    ) -> tuple[dict[str, Any], bool]:
    """Run one ablation level over ``questions`` -> (metrics, complete).

    ``complete`` is False when the wall-clock budget ran out mid-level: the
    caller records the partial level, marks the report ``partial`` and
    writes a resume command (the honest checkpoint contract). ``pin_on``
    / ``arm_gold`` only matter for the A4/E8 arms. ``prev_runs`` /
    ``prev_details`` are a previous partial record of the SAME level+arm:
    its questions are skipped and the observations merged, so a resumed
    level extends instead of restarting (the merged metrics are recomputed
    over the union).
    """
    if level not in ABLATION_LEVELS:
        raise ValueError(f"unknown ablation level: {level!r}")

    counting_llm = _CountingLLM(router) if level != "A1_pure_rag" else None
    svc = None
    if level != "A1_pure_rag":
        if ontology is None and store is not None:
            # The snapshot loader lives on the kg-service store (the graph
            # store here is the version-pinned adapter); loading it is one
            # query, and None only degrades hop plans to unfiltered.
            try:
                from services.knowevo.kg_service import PgStore
                ontology = await PgStore().load_ontology_snapshot(tenant_id)
            except Exception as exc:  # noqa: BLE001 - unfiltered hop plans
                logger.warning("ontology snapshot unavailable, hop plans "
                               "degrade to unfiltered: %s", exc)
        svc = DecisionService(store=store,
                              llm=counting_llm if llm is None else llm,
                              ontology=ontology, tenant_id=tenant_id)

    config: dict[str, Any] = {
        "ablation_level": level,
        "pin": ("on" if pin_on else "off") if level == "A4_full" else "n/a",
        "pin_as_of": dt_pin_as_of.isoformat() if dt_pin_as_of else None,
        "arm_gold_judging": bool(arm_gold),
        "multihop": {"depth": MULTIHOP_DEPTH, "beam": MULTIHOP_BEAM},
        "model_plan": {
            "generator": f"{GENERATE_TIER}:"
                         f"{_resolved_model_id(router, GENERATE_TIER)}",
            "judge": f"{JUDGE_TIER}:"
                     f"{_resolved_model_id(router, JUDGE_TIER)}",
        },
        "knowledge_stamp": {
            "retrieval": "bm25_local_corpus",
            "corpus_chunks": len(retriever.chunks),
            "top_k": top_k,
            "graph": "pg_jsonb_store" if level != "A1_pure_rag" else None,
        },
        "pace_seconds": MIN_CALL_INTERVAL_SECONDS,
        "runs_per_question": runs,
        "on_unexpected": on_unexpected,
        "tenant_id": tenant_id,
    }

    all_runs: list[dict[str, Any]] = list(prev_runs or [])
    details: list[dict[str, Any]] = list(prev_details or [])
    done_ids = {d.get("question_id") for d in details}
    ran = 0
    complete = True
    for i, item in enumerate(questions, start=1):
        if item.get("id") in done_ids:
            continue  # already measured in the resumed-from partial record
        if budget_deadline is not None and time.monotonic() >= budget_deadline:
            logger.warning("budget exhausted before %s [%d/%d]; checkpointing",
                           item.get("id"), i, len(questions))
            complete = False
            break
        logger.info("[%s%s] [%d/%d] %s", level,
                    f"/pin={'on' if pin_on else 'off'}"
                    if level == "A4_full" else "",
                    i, len(questions), item.get("id"))
        if level == "A1_pure_rag":
            per_run, detail = await run_question(
                router, item, retriever, runs=runs, top_k=top_k, lang=lang,
                on_unexpected=on_unexpected)
        else:
            per_run, detail = await _run_graph_question(
                level, item, router=router, svc=svc, retriever=retriever,
                tenant_id=tenant_id, runs=runs, top_k=top_k, lang=lang,
                on_unexpected=on_unexpected, pin_on=pin_on,
                arm_gold=arm_gold, dt_pin_as_of=dt_pin_as_of,
                counting_llm=counting_llm)
        all_runs.extend(per_run)
        details.append(detail)
        ran += 1

    # n_expected covers the questions actually measured so far: a budget
    # checkpoint is reported via level_complete/partial, while any judged
    # shortfall WITHIN the measured set stays an integrity warning.
    metrics = summarize(all_runs, details, config,
                        n_expected=len(details) * runs)
    metrics["details"] = details
    metrics["runs"] = all_runs
    metrics["level_complete"] = complete
    metrics["n_questions_run"] = len(details)
    if counting_llm is not None:
        # Decision-layer calls (route / hop plan / card render) are metered
        # through the wrapper; their tokens were already attributed into the
        # per-question details, so only the call count is recorded here.
        metrics["decision_llm_calls"] = counting_llm.calls
    if metrics["n_questions"]:
        k2 = round(metrics["pass2"] * metrics["n_questions"])
        metrics["pass2_ci95"] = wilson_interval(k2, metrics["n_questions"])
    if metrics["n_judged"]:
        ka = round(metrics["acc"] * metrics["n_judged"])
        metrics["acc_ci95"] = wilson_interval(ka, metrics["n_judged"])
    return metrics, complete


# ---------------------------------------------------------------------------
# D1 gate + data-reality checks (run through the version_pin predicate)
# ---------------------------------------------------------------------------

def d1_check(tenant_id: str | None = None) -> dict[str, Any]:
    """T-18b discriminative counts through the version_pin predicate.

    The acceptance gate: the pinned-predicate fact count must differ
    between the two t_v instants (0 != 1 today). Equal counts mean D1 is
    broken and the caller must stop before running E8.
    """
    from database.knowevo_db import KgRelation, _get_db_session
    from services.knowevo.version_pin import pin_predicate

    out: dict[str, Any] = {}
    with _get_db_session() as session:
        q = session.query(KgRelation)
        if tenant_id:
            q = q.filter(KgRelation.tenant_id == tenant_id)
        out["all"] = q.count()
        for label, instant in D1_CLOCKS:
            out[label] = (q.filter(pin_predicate(KgRelation, instant))
                          .count())
    out["discriminative"] = (out["t_v_2022_01_01"] != out["t_v_2025_06_01"])
    return out


def graph_presence(tenant_id: str) -> dict[str, int]:
    """Active entity / relation counts for the ablation tenant (data reality)."""
    from database.knowevo_db import KgEntity, KgRelation, _get_db_session

    with _get_db_session() as session:
        ents = (session.query(KgEntity)
                .filter(KgEntity.tenant_id == tenant_id,
                        KgEntity.status == "active")
                .count())
        rels = (session.query(KgRelation)
                .filter(KgRelation.tenant_id == tenant_id)
                .count())
    return {"entities": ents, "relations": rels}


# ---------------------------------------------------------------------------
# Persistence + report
# ---------------------------------------------------------------------------

def _append_cost_ledger_row(metrics: dict[str, Any], run_id: str | None,
                            note: str) -> None:
    """One markdown table row per (level, arm) run (ledger discipline)."""
    plan = (metrics.get("config") or {}).get("model_plan") or {}
    plan_text = (f"{plan.get('generator', 'mid=?')} / "
                 f"{plan.get('judge', 'judge=?')}")
    line = (
        f"| e2-{run_id or 'nopersist'} | {time.strftime('%Y-%m-%d %H:%M')} "
        f"| 评测(E2 消融) | {plan_text} "
        f"| {metrics.get('tokens_in', 0)} | {metrics.get('tokens_out', 0)} "
        f"| 0 | p95={metrics.get('p95_latency_s', 0)}s | {note} |\n"
    )
    try:
        COST_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COST_LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        logger.warning("cost-ledger append failed: %s", exc)


def _result_key(level: str, pin: str | None, arm_gold: bool = False) -> str:
    """(level, pin, gold-mode) - an E8 gold-swap run is a different run."""
    return f"{level}|{pin or 'n/a'}|{int(arm_gold)}"


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _parse_pin_as_of(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _e8_section(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """E8 pin on/off contrast (per type), only when both arms exist.

    The on/off accs come from the two arms' own ``by_type`` cells, so the
    arms may cover different question subsets - each acc is honest about
    its own n_judged, and the delta is reported with those n's attached.
    """
    def _arm(pin: str) -> dict[str, Any] | None:
        # the dedicated gold-swap arm first; a headline run is the fallback
        return (next((r for r in results if r["level"] == "A4_full"
                      and r["pin"] == pin and r.get("arm_gold")), None)
                or next((r for r in results if r["level"] == "A4_full"
                         and r["pin"] == pin), None))

    on = _arm("on")
    off = _arm("off")
    if on is None or off is None:
        return None
    def _run_id(row: dict[str, Any]) -> Any:
        # The producer writes ``eval_run_id`` (see the results.append site).
        # The earlier ``run_id`` read therefore returned null for every real
        # report, silently dropping which run each arm came from - the
        # fixtures used ``run_id``, which is why the mismatch went unseen.
        # Older reports and fixtures may still carry ``run_id``: fall back.
        return row.get("eval_run_id") or row.get("run_id")

    on_vintage = on.get("invocation_id")
    off_vintage = off.get("invocation_id")
    out: dict[str, Any] = {
        "pin_on_run": _run_id(on),
        "pin_off_run": _run_id(off),
        "arm_gold_judging": {"pin_on": on.get("arm_gold"),
                             "pin_off": off.get("arm_gold")},
        "arm_vintage": {
            "pin_on": {"eval_run_id": _run_id(on),
                       "invocation_id": on_vintage,
                       "recorded_at": on.get("recorded_at")},
            "pin_off": {"eval_run_id": _run_id(off),
                        "invocation_id": off_vintage,
                        "recorded_at": off.get("recorded_at")},
            # ``results`` accumulates across invocations by design (the
            # checkpoint/resume across quota windows), so the two arms of a
            # reported delta may come from different days and different
            # graph states. This flag is the reader's only way to tell a
            # paired contrast from a juxtaposition; None when neither row
            # carries a vintage stamp.
            "same_invocation": (
                None if not (on_vintage and off_vintage)
                else on_vintage == off_vintage),
        },
        "per_type": {},
        "caveat": E8_CAVEAT,
    }
    for qtype in ("F", "V"):
        cells: dict[str, Any] = {}
        for name, res in (("pin_on", on), ("pin_off", off)):
            cell = ((res["metrics"].get("by_type") or {}).get(qtype)) or {}
            n = int(cell.get("n_judged") or 0)
            acc = cell.get("acc")
            cells[name] = {
                "acc": acc,
                "n_judged": n,
                "ci95": (wilson_interval(round((acc or 0) * n), n)
                         if n else {"lo": None, "hi": None}),
            }
        delta = None
        if (cells["pin_on"]["acc"] is not None
                and cells["pin_off"]["acc"] is not None):
            delta = round(cells["pin_on"]["acc"]
                          - cells["pin_off"]["acc"], 4)
        out["per_type"][qtype] = {
            **cells, "delta_acc_on_minus_off": delta}
    return out


def _ran_no_new_questions(metrics: dict[str, Any],
                          prev_detail_count: int) -> bool:
    """True when a resumed level produced zero new question records.

    A budget checkpoint that lands before the level's first question must
    neither clobber the previous partial record (in a multi-arm E8
    invocation the second arm would otherwise wipe the first arm's data)
    nor emit an empty eval_run_t row - the level simply stays pending and
    the resume command points at it.
    """
    return int(metrics.get("n_questions_run") or 0) <= prev_detail_count


def _resume_command(args: argparse.Namespace, levels: list[str],
                    pins: list[str]) -> str:
    types = args.types or "all"
    return (
        "cd backend && POSTGRES_HOST=localhost POSTGRES_PORT=5434 "
        "POSTGRES_USER=root POSTGRES_DB=nexent "
        "NEXENT_POSTGRES_PASSWORD=$(grep -m1 '^NEXENT_POSTGRES_PASSWORD=' "
        "../deploy/env/.env | cut -d= -f2) "
        "KW_LLM_SMALL_MODEL_ID=7 KW_LLM_MID_MODEL_ID=7 KW_LLM_LARGE_MODEL_ID=8 "
        f"uv run python -m services.knowevo.pipeline.ablation "
        f"--levels {','.join(levels)} --pin {','.join(pins)} "
        f"--types {types} --runs {args.runs} --top-k {args.top_k} "
        f"--pace {args.pace} --pin-as-of {args.pin_as_of} "
        f"--budget-minutes {args.budget_minutes} "
        f"--tenant {args.tenant} --out {args.out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_levels(raw: str) -> list[str]:
    levels: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        level = LEVEL_ALIASES.get(token.upper(), token)
        if level not in ABLATION_LEVELS:
            raise ValueError(f"unknown ablation level: {token!r} "
                             f"(use one of {ABLATION_LEVELS} or A1..A4)")
        if level not in levels:
            levels.append(level)
    if not levels:
        raise ValueError("--levels resolved to nothing")
    return levels


def _parse_pins(raw: str) -> list[str]:
    pins: list[str] = []
    for token in raw.split(","):
        token = token.strip().lower()
        if token not in ("on", "off"):
            raise ValueError(f"--pin accepts on/off only, got {token!r}")
        if token not in pins:
            pins.append(token)
    return pins


def _parse_types(raw: str) -> list[str]:
    types: list[str] = []
    for token in raw.split(","):
        token = token.strip().upper()
        if not token:
            continue
        if token not in QTYPES:
            raise ValueError(f"--types accepts F/M/V/X, got {token!r}")
        if token not in types:
            types.append(token)
    return types


def build_plan(levels: list[str], pins: list[str]) -> list[tuple[str, str | None]]:
    """(level, pin-arm) pairs: A4 fans out over pins, other levels once."""
    plan: list[tuple[str, str | None]] = []
    for level in levels:
        if level == "A4_full":
            plan.extend((level, pin) for pin in pins)
        else:
            plan.append((level, None))
    return plan


async def _dry_run(plan, questions, *, retriever, store, tenant_id,
                   dt_pin_as_of) -> int:
    """Build each level's channel for the first question (no LLM, no DB write)."""
    from services.knowevo.schemas import EvidenceChain

    for level, pin, _prev in plan:
        item = questions[0]
        svc = DecisionService(store=store, llm=None, tenant_id=tenant_id)
        fused, records, _chain, _txt, kg_info = await build_channel(
            level, item, tenant_id=tenant_id, store=store, svc=svc,
            retriever=retriever, top_k=5, pin_on=(pin == "on"),
            dt_pin_as_of=dt_pin_as_of)
        print(json.dumps({
            "level": level, "pin": pin or "n/a", "question": item["id"],
            "context_chars": len(fused), "n_records": len(records),
            "kg_channel": kg_info,
            "chain": isinstance(_chain, EvidenceChain),
        }, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="E2 ablation runner (T-22): A1-A4 + E8 pin on/off")
    parser.add_argument("--testset", default=str(
        CORPUS_ROOT / "testset-v1-seed.json"))
    parser.add_argument("--levels", default="A1,A2,A3,A4",
                        help="comma list of A1..A4 or full level names")
    parser.add_argument("--pin", default="on",
                        help="comma list of on/off pin arms for A4 "
                             "(two+ arms => E8 run: judge gold follows arm)")
    parser.add_argument("--types", default="",
                        help="comma list of F/M/V/X to include (default all)")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--lang", default="zh", choices=["zh", "en"])
    parser.add_argument("--pace", type=float, default=8.0,
                        help="min seconds between LLM calls (default 8; 0 off)")
    parser.add_argument("--pin-as-of", default=DEFAULT_PIN_AS_OF,
                        help="explicit t_v for the pin-on arm (ISO date; "
                             "default between the two guideline editions)")
    parser.add_argument("--on-unexpected", default="count_fail",
                        choices=["count_fail", "raise"])
    parser.add_argument("--no-persist", action="store_true",
                        help="skip the eval_run_t insert and cost-ledger row")
    parser.add_argument("--out", default=str(REPORT_PATH))
    parser.add_argument("--budget-minutes", type=float, default=0.0,
                        help="wall-clock budget; the run checkpoints at the "
                             "question boundary when it expires (0 = no limit)")
    parser.add_argument("--force", action="store_true",
                        help="rerun pairs already recorded in the report")
    parser.add_argument("--dry-run", action="store_true",
                        help="build channels for the first question per "
                             "level, no LLM call, no DB write")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    levels = _parse_levels(args.levels)
    pins = _parse_pins(args.pin)
    types = _parse_types(args.types)
    dt_pin_as_of = _parse_pin_as_of(args.pin_as_of)
    plan = build_plan(levels, pins)

    data = json.loads(Path(args.testset).read_text(encoding="utf-8"))
    questions = data.get("questions") or []
    if types:
        questions = [q for q in questions if q.get("type") in types]
    if args.limit:
        questions = questions[:args.limit]
    if not questions:
        logger.error("no questions after filtering")
        return 1
    testset_hash = _testset_hash(data)

    out_path = Path(args.out)
    report: dict[str, Any] = {
        "task": TASK_REF,
        "partial": True,
        "generated_at": datetime.now(UTC).isoformat(),
        # Identity of THIS process run. The report accumulates results across
        # invocations (checkpoint/resume over quota windows), so every result
        # row records which invocation produced it; the E8 block then
        # discloses whether a delta pairs two arms from one controlled run
        # or from two different runs/dates.
        "invocation_id": uuid4().hex,
        "testset": Path(args.testset).name,
        "testset_hash": testset_hash,
        "n_questions": len(questions),
        "runs_per_question": args.runs,
        "top_k": args.top_k,
        "pace_seconds": args.pace,
        "pin_as_of": args.pin_as_of,
        "types_filter": types or list(QTYPES),
        # E8 multi-arm invocations swap the judge gold: those runs are a
        # different (level, pin, gold-mode) triple than headline runs.
        "levels_requested": [_result_key(level, pin,
                                         len(pins) > 1
                                         and level == "A4_full")
                             for level, pin in plan],
        "data_reality": {},
        "results": [],
        "cross_table": {},
        "e8": None,
        "notes": [
            ("A2/A3/A4 的文档通道与 A1 逐字节相同（同一生成模型与 prompt）；"
             "图谱通道经 DecisionService.assemble_evidence 融合，冲突标 contested。"),
            ("A3 多跳为 DecisionService.multi_hop 服务直调（depth=2, beam=3, "
             "current view）；A4/E8 的版本钉住走 version_pin 单一入口。"),
            ("E8 判分按臂选金标：pin-on 臂对 answer_old、pin-off 臂对 answer_new"
             "（V 题按臂收窄 key_facts）；F/M/X 金标不变。"),
        ],
    }
    done: set[str] = set()
    if not args.force and out_path.exists():
        try:
            previous = json.loads(out_path.read_text(encoding="utf-8"))
            for res in previous.get("results") or []:
                report["results"].append(res)
                done.add(_result_key(res["level"], res["pin"],
                                     bool(res.get("arm_gold"))))
            report["data_reality"] = previous.get("data_reality") or {}
            # the overall plan spans invocations (checkpoint/resume): a run
            # only stops being partial when every requested pair is done
            for key in previous.get("levels_requested") or []:
                if key not in report["levels_requested"]:
                    report["levels_requested"].append(key)
            # the header keeps the WIDEST scope any invocation planned, so
            # a narrow E8 (--types V,F) resume does not shrink the report
            # header of a four-level run
            report["n_questions"] = max(previous.get("n_questions") or 0,
                                        len(questions))
            merged_types = {t for t in (previous.get("types_filter") or [])}
            merged_types.update(types or QTYPES)
            report["types_filter"] = [t for t in QTYPES if t in merged_types]
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("existing report unreadable, starting fresh: %s",
                           exc)
    arm_gold = len(pins) > 1  # explicit multi-arm run => E8 gold swap
    by_key = {_result_key(r["level"], r["pin"], bool(r.get("arm_gold"))): r
              for r in report["results"]}
    # a recorded-and-complete pair is skipped; a recorded-but-partial pair
    # is CONTINUED (its observations are merged, see run_level prev_*)
    pending: list[tuple[str, str | None, dict[str, Any] | None]] = []
    for level, pin in plan:
        key = _result_key(level, pin, arm_gold and level == "A4_full")
        prev = by_key.get(key)
        if prev is not None and prev.get("complete"):
            continue
        pending.append((level, pin, prev))

    # D1 gate + data reality (runs even on resume; cheap counts).
    try:
        d1 = d1_check()
        presence = graph_presence(args.tenant)
        report["data_reality"] = {
            "d1_discriminative_counts": d1,
            "d1_checked_at": datetime.now(UTC).isoformat(),
            "ablation_tenant_graph": presence,
            "ablation_tenant": args.tenant,
        }
        if not d1["discriminative"]:
            report["partial"] = True
            report["resume"] = _resume_command(args, levels, pins)
            report["notes"].append(
                "D1 前置校验失败：两个 t_v 下纳入事实数相同，E8 无效，已停止。")
            _write_report(out_path, report)
            logger.error("D1 gate FAILED: t_v counts identical (%s) - "
                         "fix T-18b before running E8", d1)
            return 3
    except Exception as exc:  # noqa: BLE001 - DB down is reportable, not fatal
        report["data_reality"] = {"error": str(exc)[:300]}

    if not pending:
        _finalize_report(report, out_path, args, levels, pins)
        print(json.dumps({"status": "nothing_to_do",
                          "results": len(report["results"])},
                         ensure_ascii=False))
        return 0

    if args.dry_run:
        from services.knowevo.graph_store import PgJsonbGraphStore
        retriever = Retriever.from_documents(parse_corpus(CORPUS_ROOT))
        store = PgJsonbGraphStore()
        return asyncio.run(_dry_run(
            pending, questions, retriever=retriever, store=store,
            tenant_id=args.tenant, dt_pin_as_of=dt_pin_as_of))

    retriever = Retriever.from_documents(parse_corpus(CORPUS_ROOT))
    logger.info("index built: %d chunks", len(retriever.chunks))
    from services.knowevo.graph_store import PgJsonbGraphStore
    from services.knowevo.llm_client import build_llm_callable
    store = PgJsonbGraphStore()
    router = build_llm_callable(args.tenant)
    if args.pace is not None:
        global MIN_CALL_INTERVAL_SECONDS
        MIN_CALL_INTERVAL_SECONDS = args.pace

    # The hop planner's relation vocabulary is loaded inside run_level
    # (async store call); None degrades to an unfiltered walk.
    ontology = None
    deadline = (time.monotonic() + args.budget_minutes * 60.0
                if args.budget_minutes and args.budget_minutes > 0 else None)
    # llm stays None: run_level wraps the router in the token-counting
    # adapter (the decision layer's str-only contract would otherwise lose
    # the card/hop-plan calls from the ledger).
    run_kwargs = {
        "retriever": retriever, "store": store,
        "runs": args.runs, "top_k": args.top_k, "lang": args.lang,
        "on_unexpected": args.on_unexpected,
        "dt_pin_as_of": dt_pin_as_of, "tenant_id": args.tenant,
        "budget_deadline": deadline,
    }

    for level, pin, prev in pending:
        prev_runs = list((prev or {}).get("metrics", {}).get("runs") or [])
        prev_details = list((prev or {}).get("metrics", {})
                            .get("details") or [])
        result_arm_gold = bool(arm_gold and level == "A4_full")
        if prev is not None:
            logger.info("resuming %s from %d measured question(s)",
                        _result_key(level, pin, result_arm_gold),
                        len(prev_details))
        metrics, complete = asyncio.run(run_level(
            level, questions, router=router,
            pin_on=(pin == "on"), arm_gold=result_arm_gold,
            ontology=ontology, prev_runs=prev_runs,
            prev_details=prev_details, **run_kwargs))
        if _ran_no_new_questions(metrics, len(prev_details)):
            # Budget expired before this level ran a single new question:
            # record nothing. Writing here would replace the previous
            # partial entry with an empty one (E8's second arm) or insert
            # a zero-question eval_run_t row; the level stays pending and
            # the resume command points at it.
            logger.warning("%s: no new questions in this invocation "
                           "(budget checkpoint); existing record untouched",
                           _result_key(level, pin, result_arm_gold))
            _finalize_report(report, out_path, args, levels, pins)
            continue
        run_id = None
        if not args.no_persist:
            run_id = persist_eval_run(metrics, testset_hash, args.tenant,
                                      task_ref=TASK_REF)
            _append_cost_ledger_row(
                metrics, run_id,
                f"T-22 {level} pin={pin or 'n/a'} "
                f"{metrics['n_questions_run']}题×{args.runs}runs"
                f"{'(部分)' if not complete else ''}; "
                f"acc={metrics['acc']} pass2={metrics['pass2']} "
                f"n_judged={metrics['n_judged']}/{metrics['n_expected']} "
                f"trace={metrics['trace_machine']}")
        # a continued pair REPLACES its partial predecessor (same key)
        new_key = _result_key(level, pin, result_arm_gold)
        report["results"] = [
            r for r in report["results"]
            if _result_key(r["level"], r["pin"], bool(r.get("arm_gold")))
            != new_key]
        report["results"].append({
            "level": level,
            "pin": pin or "n/a",
            "arm_gold": result_arm_gold,
            "complete": complete,
            "eval_run_id": run_id,
            # Vintage stamp: ``results`` spans invocations, so the E8 block
            # needs to know which run produced each arm (see _e8_section).
            "invocation_id": report["invocation_id"],
            "recorded_at": datetime.now(UTC).isoformat(),
            "metrics": metrics,
        })
        _finalize_report(report, out_path, args, levels, pins)
        print(json.dumps({
            "level": level, "pin": pin, "complete": complete,
            "eval_run_id": run_id,
            "acc": metrics["acc"], "pass2": metrics["pass2"],
            "n_judged": metrics["n_judged"],
            "n_expected": metrics["n_expected"],
            "pass2_ci95": metrics.get("pass2_ci95"),
        }, ensure_ascii=False))

    _finalize_report(report, out_path, args, levels, pins)
    return 0


def _finalize_report(report: dict[str, Any], out_path: Path,
                     args: argparse.Namespace, levels: list[str],
                     pins: list[str]) -> None:
    """Refresh cross table / E8 / partial flags and write the report."""
    results = report["results"]
    done = {_result_key(r["level"], r["pin"], bool(r.get("arm_gold")))
            for r in results}
    report["partial"] = (
        any(key not in done for key in report["levels_requested"])
        or any(not r.get("complete") for r in results))
    # Cross table over the first result per level (the headline run; E8
    # partial-type runs do not displace it).
    headline: dict[str, dict[str, Any]] = {}
    for res in results:
        headline.setdefault(res["level"], res["metrics"])
    report["cross_table"] = cross_table(headline)
    report["e8"] = _e8_section(results)
    pending_keys = [key for key in report["levels_requested"] if key not in done]
    report["pending"] = pending_keys
    report["resume"] = _resume_command(args, levels, pins) if pending_keys \
        else None
    report["generated_at"] = datetime.now(UTC).isoformat()
    _write_report(out_path, report)
    logger.info("report written to %s (partial=%s)", out_path,
                report["partial"])


if __name__ == "__main__":
    sys.exit(main())
