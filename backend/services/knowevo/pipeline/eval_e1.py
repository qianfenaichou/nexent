"""E1 pure-RAG baseline runner (T-10a-2).

The A1 ablation arm of 02-technical-plan 3.5: answer each testset question
from retrieved knowledge-base passages only - no graph, no multi-hop, no
version pinning - then grade with an LLM-as-judge five-tuple rubric and
aggregate pass^k, writing one ``eval_run_t`` row.

Pipeline per question:
    retrieve (BM25 over the corpus) -> generate (mid tier) -> judge (large
    tier, different model family) -> per-run pass 0/1 -> pass^k aggregate

K4 protocol (02-technical-plan 6): each question runs 3x, pass^2 (share of
questions with >=2 passing runs) is the headline metric, p95 latency and
tokens are reported alongside. The judge prompt is the frozen
``knowevo_judge_*`` pair; the scorer logic is rewritten from tau-bench
(arXiv:2406.12045) "Design inspired by tau-bench / tau2-bench (arXiv:2506.07982)".

CLI (from backend/):
    python -m services.knowevo.pipeline.eval_e1 \
        --testset ../competition/corpus/testset-v1-seed.json \
        --runs 3 --top-k 5 [--limit 5] [--dry-run] [--no-persist]
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
from pathlib import Path
from typing import Any

from services.knowevo.e1_retrieval import (
    Retriever,
    parse_corpus,
    retrieve_context,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
CORPUS_ROOT = REPO_ROOT / "competition" / "corpus"
PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts"
COST_LEDGER_PATH = REPO_ROOT / "competition" / "docs" / "cost-ledger.md"

# Default tenant: the one the corpus (58 docs), the graph and the registered
# models all live under (verified against model_record_t / doc_asset_t).
DEFAULT_TENANT = "6756b0ab-39c0-462a-9745-aa12e1511fcd"

GENERATE_TIER = "mid"
JUDGE_TIER = "large"
PASS_LINE = 0.8

# Platform fault markers (E0 limitation 3: the free-tier gateway 429/500s
# and returns empty content under load; a platform fault is NOT a reasoned
# answer and must never be averaged into acc as a failed run).
# T-18c D2: the marker list is a *substring* heuristic - a code bug whose
# message happens to contain e.g. "502" (a line number, a port) must not be
# classified as a platform fault. The authoritative classifier is
# `_is_platform_fault(exc)` which checks exception types first and only
# falls back to these markers for transport-shaped messages.
_PLATFORM_FAULT_MARKERS = (
    "no active accounts available",
    "rate limit",
    "insufficient_quota",
    "402",
    "timed out",
    "connection reset",
    "connection aborted",
    "temporarily unavailable",
    "bad gateway",
    "502",
    "503",
    "504",
)
# Exception types that are always platform faults regardless of message
# content (they cannot be produced by a code bug in the pipeline itself).
_PLATFORM_FAULT_TYPES = (TimeoutError, ConnectionError,
                         ConnectionResetError, ConnectionAbortedError)
JUDGE_RETRIES = 6
RETRY_BACKOFF_SECONDS = 5.0
# Hard per-call ceiling enforced by the runner itself, independent of the
# SDK/provider timeout: a hung upstream connection (observed as 50+ minutes
# of zero IO before timeout_seconds was set) must become a retryable failure,
# not an indefinitely blocked batch.
CALL_HARD_TIMEOUT_SECONDS = 180.0
# Pacing: commercial endpoints bill against a tokens-per-minute budget, and
# E1's calls are large (retrieved context + rubric). Sleeping between calls
# keeps the run under the TPM ceiling instead of collecting 429s whose
# Retry-After (147s observed on sensenova) is far longer than any sane
# backoff. 0 disables pacing (used by tests and unmetered endpoints).
MIN_CALL_INTERVAL_SECONDS = 8.0
_LAST_CALL_AT = 0.0


def _retry_after_seconds(message: str) -> float | None:
    """Extract a server-advised wait from an error message, if present.

    Handles both ``Retry in 147.9 seconds`` and ``retry_after: 30`` shapes.
    Capped so a hostile/garbled value cannot park the batch for hours.
    """
    match = re.search(r"retry(?:_after)?[^0-9]{0,20}(\d+(?:\.\d+)?)",
                      message or "", re.IGNORECASE)
    if not match:
        return None
    try:
        return min(float(match.group(1)), 300.0)
    except ValueError:
        return None


async def _pace(min_interval: float | None = None) -> None:
    """Sleep so consecutive LLM calls are at least ``min_interval`` apart."""
    global _LAST_CALL_AT
    interval = MIN_CALL_INTERVAL_SECONDS if min_interval is None else min_interval
    if interval <= 0:
        _LAST_CALL_AT = time.monotonic()
        return
    now = time.monotonic()
    wait = interval - (now - _LAST_CALL_AT)
    if wait > 0:
        await asyncio.sleep(wait)
    _LAST_CALL_AT = time.monotonic()


def _is_platform_fault(exc: Exception | str) -> bool:
    """Classify an error as a platform fault (T-18c D2).

    Platform faults are infra/transport conditions: type-first (timeouts,
    connection resets are always platform), then a *message* match only for
    gateway/quota phrases. A code bug (KeyError, Pydantic validation,
    ValueError from our own parsing) is never a platform fault even if its
    message accidentally contains a marker substring like "502".
    """
    if isinstance(exc, Exception) and isinstance(exc, _PLATFORM_FAULT_TYPES):
        return True
    message = exc if isinstance(exc, str) else str(exc)
    if not message:
        return False
    lowered = message.lower()
    # 429 must appear in a transport-shaped frame ("HTTP 429", "status 429",
    # "error code: 429"); a bare "429" (a line number, a lab value echoed in
    # an error message) must not classify a code bug as a platform fault.
    if "too many requests" in lowered or re.search(
            r"(?:http|status|error\s*code|response)\D{0,8}429", lowered):
        return True
    if "no active accounts available" in lowered or "insufficient_quota" in lowered:
        return True
    # Remaining markers are unambiguous transport phrases; "402"/"502" etc.
    # keep the frame requirement for the same reason as 429.
    for marker in (_PLATFORM_FAULT_MARKERS):
        if marker.isdigit():
            if re.search(rf"(?:http|status|error\s*code|response)\D{{0,8}}{marker}",
                         lowered):
                return True
            continue
        if marker in lowered:
            return True
    return False


def _render_prompt(name: str, lang: str, **variables: Any) -> tuple[str, str]:
    """Load ``{name}_{lang}.yaml`` and substitute ``{{ var }}`` placeholders
    (same convention as eval_v1 and kg_service)."""
    import yaml

    with open(PROMPT_DIR / f"{name}_{lang}.yaml", "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    system = str(data.get("system_prompt", ""))
    user = str(data.get("user_prompt", ""))

    def fill(template: str) -> str:
        for key, value in variables.items():
            for pattern in (f"{{{{ {key} }}}}", f"{{{{{key}}}}}",
                            f"{{{{ {key}}}}}", f"{{{{{key} }}}}"):
                template = template.replace(pattern, str(value))
        return template

    return fill(system), fill(user)


def render_answer_prompt(context: str, question: str,
                         lang: str = "zh") -> str:
    """E1 generation prompt: retrieved context + question, citation-mandated."""
    system, user = _render_prompt(
        "knowevo_e1_answer", lang, context=context, question=question)
    return f"{system}\n\n{user}"


def render_judge_prompt(item: dict[str, Any], answer: str,
                        evidence_chain: str, lang: str = "zh") -> str:
    """Five-tuple judge prompt from the frozen knowevo_judge template."""
    rubric = item.get("rubric") or {}
    system, user = _render_prompt(
        "knowevo_judge", lang,
        question=item.get("question", ""),
        key_facts=json.dumps(rubric.get("key_facts") or [], ensure_ascii=False),
        refusal_expected=bool(rubric.get("refusal_expected")),
        evidence_required=bool(rubric.get("evidence_required")),
        reasoning_steps=json.dumps(
            rubric.get("reasoning_steps") or [], ensure_ascii=False),
        safety_boundary=rubric.get("safety_boundary") or "",
        answer=answer,
        evidence_chain=evidence_chain,
    )
    return f"{system}\n\n{user}"


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_judge_output(text: str) -> dict[str, Any]:
    """Parse the judge JSON; unparseable output is an explicit failure.

    Returns ``{"pass": 0|1, "total": float, "items": {...}, "reason": str,
    "parse_error": bool}``. Unparseable output (empty, truncated, or prose)
    comes back with ``parse_error=True``: it is never silently read as a
    pass. ``run_question`` treats such a run as a judge failure and keeps it
    out of the accuracy denominator - the same honesty rule T-09 applied to
    malformed LLM output, extended to grading.
    """
    if not text or not text.strip():
        return {"pass": 0, "total": 0.0, "items": {},
                "reason": "empty judge output", "parse_error": True}
    match = _JSON_BLOCK.search(text)
    candidate = match.group(0) if match else text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return {"pass": 0, "total": 0.0, "items": {},
                "reason": f"judge output not JSON: {text[:120]}",
                "parse_error": True}
    items = data.get("items") or {}
    weights = {"key_facts": 0.6, "refusal": 0.1, "evidence": 0.1,
               "reasoning": 0.1, "safety": 0.1}
    # Recompute the weighted total from the items instead of trusting the
    # judge's own arithmetic; the judge reports item scores, we own the sum.
    total = 0.0
    for key, weight in weights.items():
        try:
            total += float(items.get(key, 0) or 0) * weight
        except (TypeError, ValueError):
            pass
    passed = int(total >= PASS_LINE - 1e-9)
    # Honor an explicit judge veto: a stated pass=0 with a veto reason wins
    # over the arithmetic (X-type fabrication must not be averaged away).
    if data.get("pass") in (0, 1) and int(data["pass"]) == 0 and passed:
        veto = str(data.get("reason", "")).lower()
        if any(w in veto for w in ("veto", "一票否决", "fabricat", "编造")):
            passed = 0
    return {
        "pass": passed,
        "total": round(total, 4),
        "items": {k: items.get(k) for k in weights},
        "reason": str(data.get("reason", ""))[:500],
        "parse_error": False,
    }


def passk_aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """pass^k over per-question runs (delegates to the eval_v1 scorer)."""
    from services.knowevo.pipeline.eval_v1 import passk_aggregate as _agg

    return _agg(runs)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return round(ordered[idx], 2)


def evidence_chain_text(evidence: list[dict[str, Any]]) -> str:
    """Compact evidence record handed to the judge (locators + scores)."""
    if not evidence:
        return "(no passages retrieved)"
    return "; ".join(
        f"[{e['rank']}] {e['title']} (doc={e['doc_id']}, chunk={e['chunk_idx']}, "
        f"score={e['score']}, split={e['split']})" for e in evidence)


def trace_completeness(evidence: list[dict[str, Any]]) -> float:
    """Machine trace completeness: share of hits carrying full locators.

    Renamed semantics under T-18c D3 (exported as ``trace_machine`` in the
    report): this measures whether the *retriever* kept the doc/chunk/score
    locators intact end to end - it says nothing about whether the answer
    actually cites them. See ``trace_answer_cite`` for the answer-level
    counterpart.
    """
    if not evidence:
        return 0.0
    required = ("doc_id", "chunk_idx", "span_hash", "title")
    complete = sum(1 for e in evidence if all(e.get(k) is not None
                                              for k in required))
    return round(complete / len(evidence), 4)


_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


def trace_answer_cite(question: str, evidence: list[dict[str, Any]],
                      answer: str | None = None) -> dict[str, Any]:
    """Answer-level citation trace (D3, deterministic layer - zero LLM).

    Checks that every ``[n]`` reference in the generated answer points at a
    chunk that was actually retrieved (``n`` within 1..len(evidence)). A
    fabricated or out-of-range citation - exactly the false-positive
    reference the judge prompt vetoes - is countable here without paying a
    judge call, so this layer runs on every question.

    Returns ``{"cited": <n of citations in range>, "out_of_range": <n>,
    "no_citation": bool, "n_evidence": len(evidence)}``; aggregated into
    ``trace_answer_cite`` = cited / (cited + out_of_range) with no-citation
    answers reported separately (never silently scored 1.0).
    """
    if answer is None:
        # Citation checks need a generated answer; the retrieve-only summary
        # records the zero-signal state instead of pretending a score.
        return {"citations": 0, "out_of_range": 0, "no_citation": True,
                "n_evidence": len(evidence)}
    nums = [int(m.group(1)) for m in _CITATION_RE.finditer(answer)]
    if not nums:
        return {"citations": 0, "out_of_range": 0, "no_citation": True,
                "n_evidence": len(evidence)}
    out_of_range = sum(1 for n in nums if n < 1 or n > len(evidence))
    return {"citations": len(nums) - out_of_range, "out_of_range": out_of_range,
            "no_citation": False, "n_evidence": len(evidence)}


async def run_question(router, item: dict[str, Any], retriever: Retriever,
                       runs: int, top_k: int, lang: str = "zh",
                       on_unexpected: str = "raise"
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One question x ``runs`` trials: retrieve, generate, judge.

    ``on_unexpected`` follows the D2 contract: "raise" (default) lets a
    non-platform error abort the batch loudly; "count_fail" records such an
    error as ``pass=0`` with ``error="runner_error"`` so it stays in the
    accuracy denominator instead of vanishing into platform_fault.
    """
    question = item["question"]
    context, evidence = retrieve_context(retriever, question, top_k=top_k)
    return await run_question_with_context(
        router, item, context, evidence, runs, lang=lang,
        on_unexpected=on_unexpected)


async def run_question_with_context(
        router, item: dict[str, Any], context: str,
        evidence: list[dict[str, Any]], runs: int, lang: str = "zh",
        on_unexpected: str = "raise", gen_kind: str = "e1_answer",
        judge_kind: str = "e1_judge", chain_text: str | None = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One question x ``runs`` trials over a pre-built (context, evidence).

    T-22 additive extraction: the generate->judge loop of ``run_question``,
    byte-for-byte the same semantics, factored out so the ablation arms
    (A2/A3/A4) can supply their own context/evidence (graph channels fused
    via ``assemble_evidence``) without forking the scoring contract. A1
    behaviour is unchanged: same prompt, same retry/fault classification,
    same per-run record shape.

    ``chain_text`` overrides the judge's evidence-chain rendering (the
    ablation arms render doc + kg records in one numbered list); A1 keeps
    ``evidence_chain_text(evidence)``.
    """
    qid = item["id"]
    question = item["question"]
    chain = chain_text if chain_text is not None \
        else evidence_chain_text(evidence)
    trace = trace_completeness(evidence)

    per_run: list[dict[str, Any]] = []
    latencies: list[float] = []
    citation_traces: list[dict[str, Any]] = []
    tokens_in = tokens_out = 0
    prompt = render_answer_prompt(context, question, lang=lang)

    for run_idx in range(runs):
        t0 = time.monotonic()
        # Generation retries on platform faults (free-tier gateway 500s are
        # transient and honest retry beats recording a fake fail). A code bug
        # is counted as a failed run (count_fail) so the denominator is
        # honest - it must never be swept under platform_fault.
        try:
            answer, gen_usage = await _call_with_retry(
                router, prompt, kind=gen_kind, tier=GENERATE_TIER,
                qid=qid, run_idx=run_idx, label="generation",
                on_unexpected=on_unexpected)
        except Exception as exc:
            if on_unexpected == "count_fail":
                per_run.append({"question_id": qid, "pass": 0, "run": run_idx,
                                "type": item.get("type"),
                                "error": "runner_error",
                                "platform_fault": False,
                                "reason": f"generation runner error: {exc}"[:200]})
                continue
            raise
        if answer is None:
            # pass=None keeps this run out of the acc denominator only for a
            # genuine platform outage; a runner error (count_fail) is marked
            # pass=0 above and stays in the denominator.
            per_run.append({"question_id": qid, "pass": None, "run": run_idx,
                            "type": item.get("type"),
                            "error": "generation_failed",
                            "platform_fault": True})
            continue
        gen_usage = gen_usage or {"input_tokens": 0, "output_tokens": 0}
        tokens_in += gen_usage.get("input_tokens", 0)
        tokens_out += gen_usage.get("output_tokens", 0)
        # D3 answer-level citation trace (deterministic layer, no LLM):
        # every [n] in the answer must point at a retrieved chunk.
        citation_traces.append(trace_answer_cite(question, evidence, answer))

        entry, judge_usage = await _judge_once(
            router, item, answer, chain, qid=qid, run_idx=run_idx,
            lang=lang, on_unexpected=on_unexpected, judge_kind=judge_kind,
            t0=t0, citation_trace=citation_traces[-1])
        per_run.append(entry)
        if judge_usage is not None:
            tokens_in += judge_usage.get("input_tokens", 0)
            tokens_out += judge_usage.get("output_tokens", 0)
        if entry.get("latency_s"):
            latencies.append(entry["latency_s"])

    detail = {
        "question_id": qid,
        "type": item.get("type"),
        "question": question,
        "trace_completeness": trace,
        "trace_answer_cite": _aggregate_citation_traces(citation_traces),
        "evidence": evidence,
        "context_chars": len(context),
        "p50_latency_s": round(statistics.median(latencies), 2) if latencies else 0.0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    return per_run, detail


async def _judge_once(router, item: dict[str, Any], answer: str,
                      chain_text: str, *, qid: str, run_idx: int,
                      lang: str, on_unexpected: str, judge_kind: str,
                      t0: float,
                      citation_trace: dict[str, Any],
                      ) -> tuple[dict[str, Any], dict[str, int] | None]:
    """Judge one generated answer (the judge half of the run loop, T-22).

    Returns ``(per_run_entry, judge_usage)``; the entry follows the exact
    record shape ``run_question`` always produced (verdict, platform-fault
    isolation, count_fail runner errors), so extraction cannot drift the
    scoring contract. ``judge_usage`` is None exactly when no judge call
    was metered (unavailable judge).
    """
    # Judge retries hard on platform faults: an unavailable judge is not
    # a verdict "failed", and we do not pay for a partial run's answer
    # without a verdict - the run is marked platform_fault and excluded
    # from the acc denominator instead of silently counted wrong.
    try:
        judge_prompt = render_judge_prompt(item, answer, chain_text, lang=lang)
        judge_raw, judge_usage = await _call_with_retry(
            router, judge_prompt, kind=judge_kind, tier=JUDGE_TIER,
            qid=qid, run_idx=run_idx, label="judge",
            on_unexpected=on_unexpected)
    except Exception as exc:
        # Non-platform judge error: re-raise by default (a code bug must
        # surface); count_fail records it as a failed judged observation.
        if on_unexpected == "count_fail":
            return ({"question_id": qid, "pass": 0, "run": run_idx,
                     "type": item.get("type"),
                     "error": "runner_error", "platform_fault": False,
                     "reason": f"judge runner error: {exc}"[:200]}, None)
        raise
    if judge_raw is None:
        return ({"question_id": qid, "pass": None, "run": run_idx,
                 "type": item.get("type"),
                 "error": "judge_unavailable", "platform_fault": True}, None)
    judge_usage = judge_usage or {"input_tokens": 0, "output_tokens": 0}

    verdict = parse_judge_output(judge_raw)
    if verdict.get("parse_error"):
        # The judge returned empty or truncated output: that is a judge
        # failure, not a wrong answer from the model under test. Keeping
        # it out of the denominator is the same honesty rule as the
        # platform-fault path (E0 limitation 3 generalised to grading).
        logger.warning("judge output unparseable for %s run %d: %s",
                       qid, run_idx, verdict.get("reason", "")[:120])
        return ({"question_id": qid, "pass": None, "run": run_idx,
                 "type": item.get("type"),
                 "error": "judge_unparseable", "platform_fault": True,
                 "reason": verdict.get("reason", "")[:200]}, judge_usage)
    latency = time.monotonic() - t0
    return ({
        "question_id": qid,
        "pass": verdict["pass"],
        "run": run_idx,
        "total": verdict["total"],
        "type": item.get("type"),
        "latency_s": round(latency, 2),
        "answer": answer[:2000],
        "trace_answer_cite": citation_trace,
        "reason": verdict.get("reason", ""),
        "parse_error": verdict.get("parse_error", False),
    }, judge_usage)


def _aggregate_citation_traces(
        traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold per-run citation traces into one answer-level record.

    ``cited``/``out_of_range`` are summed over runs that produced an answer;
    ``rate`` is the in-range share (undefined -> None when nothing was ever
    cited - reported as ``no_citation_run=True``, never as a fake 1.0).
    """
    if not traces:
        return {"citations": 0, "out_of_range": 0, "rate": None,
                "no_citation_run": True}
    cited = sum(t["citations"] for t in traces)
    out_of_range = sum(t["out_of_range"] for t in traces)
    total = cited + out_of_range
    return {
        "citations": cited,
        "out_of_range": out_of_range,
        "rate": round(cited / total, 4) if total else None,
        "no_citation_run": total == 0,
    }


async def _call_with_retry(router, prompt: str, *, kind: str, tier: str,
                           qid: str, run_idx: int, label: str,
                           on_unexpected: str = "raise") -> tuple[str | None, dict[str, int] | None]:
    """Call the router with backoff retries on platform faults.

    Returns ``(content, usage)``; on an unrecoverable platform fault returns
    ``(None, None)`` (caller marks the run platform_fault). Non-platform
    errors are handled by ``on_unexpected`` (T-18c D2):
      * "raise" (default): re-raise immediately - a code bug must surface,
        never masquerade as flaky infra;
      * "count_fail": return ``(None, None)`` with ``_last_message`` set to
        the exception text; the caller records pass=0 in the denominator.
    """
    last_message = ""
    last_is_fault = False
    for attempt in range(JUDGE_RETRIES):
        await _pace()
        try:
            content, usage = await asyncio.wait_for(
                router.call_with_usage(
                    prompt, kind=kind, tier=tier, temperature=0.0),
                timeout=CALL_HARD_TIMEOUT_SECONDS)
            return content, usage
        except TimeoutError:
            last_message = (
                f"runner hard timeout after {CALL_HARD_TIMEOUT_SECONDS:.0f}s")
            last_is_fault = True
            if attempt < JUDGE_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS * (attempt + 1)
                logger.warning("%s %s for %s run %d (attempt %d); retrying in %.0fs",
                               label, last_message, qid, run_idx, attempt + 1, wait)
                await asyncio.sleep(wait)
            continue
        except Exception as exc:
            last_message = str(exc)
            last_is_fault = _is_platform_fault(exc)
            if last_is_fault:
                if attempt < JUDGE_RETRIES - 1:
                    # Respect a server-advised wait when the provider states
                    # one; otherwise exponential-ish linear backoff.
                    wait = _retry_after_seconds(last_message) or (
                        RETRY_BACKOFF_SECONDS * (attempt + 1))
                    logger.warning(
                        "%s platform fault for %s run %d (attempt %d): %s; "
                        "retrying in %.0fs", label, qid, run_idx, attempt + 1,
                        last_message[:160], wait)
                    await asyncio.sleep(wait)
                continue
            # Non-platform error: a code bug, not flaky infra. Default is to
            # let it crash the run loudly; count_fail opts in to recording it
            # as a failed observation that stays in the denominator.
            if on_unexpected == "count_fail":
                return None, None
            raise
    logger.warning("%s unavailable for %s run %d after %d attempts: %s",
                   label, qid, run_idx, JUDGE_RETRIES, last_message[:200])
    return None, None


def summarize(runs: list[dict[str, Any]], details: list[dict[str, Any]],
              config: dict[str, Any],
              n_expected: int | None = None) -> dict[str, Any]:
    """Aggregate one evaluation run into the ``eval_run_t.metrics`` shape.

    Honesty contract (T-18c D2):
      * Platform faults (judge/generation unavailable) are excluded from the
        acc/pass^k denominators and reported separately - a thundering
        free-tier gateway must not masquerade as a wrong answer;
      * ``n_judged`` (runs with a real verdict) is always reported next to
        ``acc``/``n_expected`` so an inflated acc is visible at a glance;
      * a type with zero judged runs is reported with
        ``insufficient_data=true`` instead of silently disappearing (the old
        behaviour inflated by_type accuracy by omission);
      * ``integrity_warning=true`` whenever a non-platform runner error was
        recorded (it stays in the denominator as pass=0) or when judged
        runs fall short of expected ones for non-platform reasons.
    """
    judged = [r for r in runs if r.get("pass") in (0, 1)]
    faults = [r for r in runs if r.get("platform_fault")]
    runner_errors = [r for r in runs
                     if r.get("error") == "runner_error"]
    agg = passk_aggregate(judged) if judged else {
        "acc": 0.0, "pass2": 0.0, "pass3": 0.0,
        "n_questions": 0, "n_runs": 0}
    latencies = [d["p50_latency_s"] for d in details if d.get("p50_latency_s")]
    traces = [d["trace_completeness"] for d in details]
    answer_traces = [d["trace_answer_cite"] for d in details
                     if isinstance(d.get("trace_answer_cite"), dict)]
    cited = sum(t.get("citations", 0) for t in answer_traces)
    out_of_range = sum(t.get("out_of_range", 0) for t in answer_traces)
    citation_total = cited + out_of_range
    by_type: dict[str, dict[str, Any]] = {}
    for qtype in ("F", "M", "V", "X"):
        sub = [r for r in judged if r.get("type") == qtype]
        if sub:
            by_type[qtype] = {
                "n": len({r["question_id"] for r in sub}),
                "n_judged": len(sub),
                "acc": round(sum(r["pass"] for r in sub) / len(sub), 4),
            }
        else:
            by_type[qtype] = {
                "n": 0, "n_judged": 0, "acc": None,
                "insufficient_data": True,
            }
    if n_expected is None:
        n_expected = agg["n_runs"]
    # Integrity: runs that were expected but never received a verdict for a
    # non-platform reason (code bug swallowed elsewhere, count_fail path).
    # Platform faults that explain the whole shortfall keep this False; a
    # shortfall the faults cannot account for means the denominator shrank
    # for an unexplained reason and the report must say so.
    unaccounted = (n_expected - len(judged)) - len(faults)
    integrity_warning = bool(runner_errors) or unaccounted > 0
    metric = {
        "acc": agg["acc"],
        "pass2": agg["pass2"],
        "pass3": agg["pass3"],
        "n_questions": agg["n_questions"],
        "n_runs": agg["n_runs"],
        "n_judged": len(judged),
        "n_expected": n_expected,
        "trace_machine": round(sum(traces) / len(traces), 4) if traces else 0.0,
        "trace_answer_cite": {
            "citations": cited,
            "out_of_range": out_of_range,
            "rate": round(cited / citation_total, 4) if citation_total else None,
            "no_citation_run": citation_total == 0,
        },
        "p95_latency_s": _p95(latencies),
        "tokens_in": sum(d["tokens_in"] for d in details),
        "tokens_out": sum(d["tokens_out"] for d in details),
        "judge_parse_errors": sum(
            1 for r in runs if r.get("parse_error")),
        "runner_errors": len(runner_errors),
        "integrity_warning": integrity_warning,
        "platform_faults": {
            "n": len(faults),
            "generation": sum(1 for r in faults if r.get("error") == "generation_failed"),
            "judge": sum(1 for r in faults if r.get("error") == "judge_unavailable"),
            "judge_unparseable": sum(
                1 for r in faults if r.get("error") == "judge_unparseable"),
        },
        "by_type": by_type,
        "config": config,
    }
    return metric


def _resolved_model_id(router, tier: str) -> str:
    """Concrete model id the router will use for ``tier`` (never guessed).

    Falls back to a placeholder only when the router cannot report one, so a
    degraded run is visible in the recorded config instead of silently
    claiming a model it may not have used.
    """
    getter = getattr(router, "_get_model", None)
    if getter is None:
        return "unknown"
    try:
        return str(getattr(getter(tier, 0.0), "model_id", "unknown"))
    except Exception:  # noqa: BLE001 - config recording must not abort a run
        return "unknown"


def persist_eval_run(metrics: dict[str, Any], testset_hash: str,
                     tenant_id: str, task_ref: str = "T-10a-2") -> str | None:
    """Write one ``eval_run_t`` row; returns the row id (None when skipped).

    The table is INSERT-only from our side (T-03 owns its DDL), so this is a
    straight insert of the run's config + metrics payload.
    """
    from database.knowevo_db import EvalRun, create_row

    config = metrics.get("config") or {}
    # eval_run_t.metrics is documented as the summary payload
    # ({acc, pass2, pass3, trace_*, p95, tokens, cny}); the per-question
    # runs/details belong in the durable report file, not in this column.
    metrics_payload = {k: v for k, v in metrics.items()
                       if k not in ("config", "details", "runs")}
    try:
        row = create_row(
            EvalRun,
            tenant_id=tenant_id,
            testset_hash=testset_hash,
            config=config,
            metrics=metrics_payload,
            task_ref=task_ref,
        )
        return str(row.get("id"))
    except Exception as exc:  # noqa: BLE001 - persistence must not fake success
        logger.error("eval_run_t persist failed: %s", exc)
        return None


def append_cost_ledger(metrics: dict[str, Any], run_id: str | None,
                       note: str) -> None:
    """Append one cost-ledger row (the ledger is append-only by design)."""
    plan = (metrics.get("config") or {}).get("model_plan") or {}
    plan_text = (f"{plan.get('generator', 'mid=?')} / "
                 f"{plan.get('judge', 'judge=?')}")
    try:
        line = (
            f"| e1-{run_id or 'nopersist'} | {time.strftime('%Y-%m-%d %H:%M')} "
            f"| 评测(E1 纯RAG) | {plan_text} "
            f"| {metrics.get('tokens_in', 0)} | {metrics.get('tokens_out', 0)} "
            f"| 0 | p95={metrics.get('p95_latency_s', 0)}s | {note} |\n"
        )
        with open(COST_LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:  # noqa: BLE001
        logger.error("cost ledger append failed: %s", exc)


async def evaluate(testset_path: Path, runs: int = 3, top_k: int = 5,
                   limit: int | None = None, tenant_id: str = DEFAULT_TENANT,
                   lang: str = "zh", build_llm=None,
                   retriever: Retriever | None = None,
                   on_unexpected: str = "raise") -> dict[str, Any]:
    """Run the E1 baseline over a testset and return the metrics payload.

    ``on_unexpected`` (T-18c D2): "raise" aborts the batch on a non-platform
    runner error (a code bug must surface); "count_fail" records it as
    pass=0 in the denominator. The default for batch runs is count_fail -
    a long batch dying on one question's bug wastes the whole run, and the
    failed observation stays honestly in the denominator either way.
    """
    data = json.loads(Path(testset_path).read_text(encoding="utf-8"))
    questions = data.get("questions") or []
    if limit:
        questions = questions[:limit]

    if retriever is None:
        logger.info("parsing corpus from %s ...", CORPUS_ROOT)
        retriever = Retriever.from_documents(parse_corpus(CORPUS_ROOT))
        logger.info("index built: %d chunks", len(retriever.chunks))

    if build_llm is None:
        from services.knowevo.llm_client import build_llm_callable
        build_llm = build_llm_callable
    router = build_llm(tenant_id)

    config = {
        "ablation_level": "A1_pure_rag",
        # Resolve the concrete model names from the router so the recorded
        # plan is what actually ran (a hardcoded name here is how the earlier
        # tokenrouter fallback went unnoticed - see pitfall #38).
        "model_plan": {
            "generator": f"{GENERATE_TIER}:{_resolved_model_id(router, GENERATE_TIER)}",
            "judge": f"{JUDGE_TIER}:{_resolved_model_id(router, JUDGE_TIER)}",
        },
        "knowledge_stamp": {
            "retrieval": "bm25_local_corpus",
            "corpus_chunks": len(retriever.chunks),
            "top_k": top_k,
        },
        "pace_seconds": MIN_CALL_INTERVAL_SECONDS,
        "runs_per_question": runs,
        "on_unexpected": on_unexpected,
        "testset": Path(testset_path).name,
    }

    all_runs: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for i, item in enumerate(questions, start=1):
        logger.info("[%d/%d] %s", i, len(questions), item.get("id"))
        per_run, detail = await run_question(
            router, item, retriever, runs=runs, top_k=top_k, lang=lang,
            on_unexpected=on_unexpected)
        all_runs.extend(per_run)
        details.append(detail)

    metrics = summarize(all_runs, details, config,
                        n_expected=len(questions) * runs)
    metrics["details"] = details
    metrics["runs"] = all_runs
    return metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E1 pure-RAG baseline runner")
    parser.add_argument("--testset", default=str(
        CORPUS_ROOT / "testset-v1-seed.json"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--lang", default="zh", choices=["zh", "en"])
    parser.add_argument("--no-persist", action="store_true",
                        help="skip the eval_run_t insert and cost-ledger row")
    parser.add_argument("--pace", type=float, default=None,
                        help="min seconds between LLM calls (default 8; 0 off)")
    parser.add_argument("--on-unexpected", default="count_fail",
                        choices=["count_fail", "raise"],
                        help="non-platform runner error handling (T-18c D2): "
                             "count_fail records pass=0 in the denominator "
                             "(default), raise aborts the batch")
    parser.add_argument("--out", default=None, help="write the full JSON here")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.pace is not None:
        global MIN_CALL_INTERVAL_SECONDS
        MIN_CALL_INTERVAL_SECONDS = args.pace

    metrics = asyncio.run(evaluate(
        Path(args.testset), runs=args.runs, top_k=args.top_k,
        limit=args.limit, tenant_id=args.tenant, lang=args.lang,
        on_unexpected=args.on_unexpected))

    run_id = None
    if not args.no_persist:
        data = json.loads(Path(args.testset).read_text(encoding="utf-8"))
        from services.knowevo.pipeline.eval_v1 import _testset_hash
        run_id = persist_eval_run(metrics, _testset_hash(data), args.tenant)
        append_cost_ledger(
            metrics, run_id,
            f"E1 纯RAG 基线 {metrics['n_questions']}题×{args.runs} runs; "
            f"acc={metrics['acc']} pass2={metrics['pass2']} "
            f"n_judged={metrics['n_judged']}/{metrics['n_expected']} "
            f"trace={metrics['trace_machine']}")

    # The full run record (per-question answers, evidence and verdicts) goes
    # to a durable repo path by default: /tmp scratch files are cleaned and
    # the detailed evidence is exactly what a review or an appeal needs.
    out_path = Path(args.out) if args.out else (
        REPO_ROOT / "competition" / "deliverables" / "e1-baseline-report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("full report written to %s", out_path)

    print(json.dumps({
        "eval_run_id": run_id,
        "acc": metrics["acc"],
        "pass2": metrics["pass2"],
        "pass3": metrics["pass3"],
        "n_questions": metrics["n_questions"],
        "n_runs": metrics["n_runs"],
        "n_judged": metrics["n_judged"],
        "n_expected": metrics["n_expected"],
        "integrity_warning": metrics["integrity_warning"],
        "trace_machine": metrics["trace_machine"],
        "trace_answer_cite": metrics["trace_answer_cite"],
        "p95_latency_s": metrics["p95_latency_s"],
        "tokens_in": metrics["tokens_in"],
        "tokens_out": metrics["tokens_out"],
        "judge_parse_errors": metrics["judge_parse_errors"],
        "runner_errors": metrics["runner_errors"],
        "platform_faults": metrics["platform_faults"],
        "by_type": metrics["by_type"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())