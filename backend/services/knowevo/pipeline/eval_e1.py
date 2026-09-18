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
``knowevo_judge_*`` pair; the scorer logic is rewritten from tau2-bench
(arXiv:2505.23319) "Design inspired by tau2-bench".

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
_PLATFORM_FAULT_MARKERS = (
    "no active accounts available",
    "rate limit",
    "429",
    "insufficient_quota",
    "402",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "temporarily unavailable",
    "bad gateway",
    "502",
    "503",
    "504",
)
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


def _is_platform_fault(message: str) -> bool:
    return any(marker.lower() in (message or "").lower()
               for marker in _PLATFORM_FAULT_MARKERS)


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
    """Share of retrieved hits that carry the full locator set.

    The trace metric needs a doc, a span and a score to be auditable; a hit
    with no span is not a trace. E1 retrieves spans by construction, so this
    measures whether the pipeline kept them intact end to end.
    """
    if not evidence:
        return 0.0
    required = ("doc_id", "chunk_idx", "span_hash", "title")
    complete = sum(1 for e in evidence if all(e.get(k) is not None
                                              for k in required))
    return round(complete / len(evidence), 4)


async def run_question(router, item: dict[str, Any], retriever: Retriever,
                       runs: int, top_k: int, lang: str = "zh"
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One question x ``runs`` trials: retrieve, generate, judge."""
    qid = item["id"]
    question = item["question"]
    context, evidence = retrieve_context(retriever, question, top_k=top_k)
    chain = evidence_chain_text(evidence)
    trace = trace_completeness(evidence)

    per_run: list[dict[str, Any]] = []
    latencies: list[float] = []
    tokens_in = tokens_out = 0
    prompt = render_answer_prompt(context, question, lang=lang)

    for run_idx in range(runs):
        t0 = time.monotonic()
        # Generation retries on platform faults (free-tier gateway 500s are
        # transient and honest retry beats recording a fake fail).
        answer, gen_usage = await _call_with_retry(
            router, prompt, kind="e1_answer", tier=GENERATE_TIER,
            qid=qid, run_idx=run_idx, label="generation",
            raise_on_fault=False)
        if answer is None:
            # pass=None keeps this run out of the acc denominator (a
            # platform outage is not a wrong answer); summarize() only
            # counts pass in (0, 1) as a judged run.
            per_run.append({"question_id": qid, "pass": None, "run": run_idx,
                            "error": "generation_failed",
                            "platform_fault": True})
            continue
        gen_usage = gen_usage or {"input_tokens": 0, "output_tokens": 0}
        tokens_in += gen_usage.get("input_tokens", 0)
        tokens_out += gen_usage.get("output_tokens", 0)

        judge_prompt = render_judge_prompt(item, answer, chain, lang=lang)
        # Judge retries hard on platform faults: an unavailable judge is not
        # a verdict "failed", and we do not pay for a partial run's answer
        # without a verdict - the run is marked platform_fault and excluded
        # from the acc denominator instead of silently counted wrong.
        judge_raw, judge_usage = await _call_with_retry(
            router, judge_prompt, kind="e1_judge", tier=JUDGE_TIER,
            qid=qid, run_idx=run_idx, label="judge")
        if judge_raw is None:
            per_run.append({"question_id": qid, "pass": None, "run": run_idx,
                            "error": "judge_unavailable", "platform_fault": True})
            continue
        judge_usage = judge_usage or {"input_tokens": 0, "output_tokens": 0}
        tokens_in += judge_usage.get("input_tokens", 0)
        tokens_out += judge_usage.get("output_tokens", 0)

        verdict = parse_judge_output(judge_raw)
        if verdict.get("parse_error"):
            # The judge returned empty or truncated output: that is a judge
            # failure, not a wrong answer from the model under test. Keeping
            # it out of the denominator is the same honesty rule as the
            # platform-fault path (E0 limitation 3 generalised to grading).
            logger.warning("judge output unparseable for %s run %d: %s",
                           qid, run_idx, verdict.get("reason", "")[:120])
            per_run.append({"question_id": qid, "pass": None, "run": run_idx,
                            "error": "judge_unparseable",
                            "platform_fault": True,
                            "reason": verdict.get("reason", "")[:200]})
            continue
        latency = time.monotonic() - t0
        latencies.append(latency)
        per_run.append({
            "question_id": qid,
            "pass": verdict["pass"],
            "run": run_idx,
            "total": verdict["total"],
            "type": item.get("type"),
            "latency_s": round(latency, 2),
            "answer": answer[:2000],
            "reason": verdict.get("reason", ""),
            "parse_error": verdict.get("parse_error", False),
        })

    detail = {
        "question_id": qid,
        "type": item.get("type"),
        "question": question,
        "trace_completeness": trace,
        "evidence": evidence,
        "p50_latency_s": round(statistics.median(latencies), 2) if latencies else 0.0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    return per_run, detail


async def _call_with_retry(router, prompt: str, *, kind: str, tier: str,
                           qid: str, run_idx: int, label: str,
                           raise_on_fault: bool = False) -> tuple[str | None, dict[str, int] | None]:
    """Call the router with backoff retries on platform faults.

    Returns ``(content, usage)``; on an unrecoverable platform fault returns
    ``(None, None)`` (caller marks the run platform_fault) unless
    ``raise_on_fault`` is set. Non-platform errors raise immediately - a
    code bug must surface, not masquerade as flaky infra.
    """
    last_message = ""
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
            if attempt < JUDGE_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS * (attempt + 1)
                logger.warning("%s %s for %s run %d (attempt %d); retrying in %.0fs",
                               label, last_message, qid, run_idx, attempt + 1, wait)
                await asyncio.sleep(wait)
            continue
        except Exception as exc:
            last_message = str(exc)
            if _is_platform_fault(last_message):
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
            if raise_on_fault:
                raise
            return None, None
    logger.warning("%s unavailable for %s run %d after %d attempts: %s",
                   label, qid, run_idx, JUDGE_RETRIES, last_message[:200])
    return None, None


def summarize(runs: list[dict[str, Any]], details: list[dict[str, Any]],
              config: dict[str, Any]) -> dict[str, Any]:
    """Aggregate one evaluation run into the ``eval_run_t.metrics`` shape.

    Platform faults (judge/generation unavailable) are excluded from the
    acc/pass^k denominators and reported separately - E0 limitation 3 fix:
    a thundering free-tier gateway must not masquerade as a wrong answer.
    """
    judged = [r for r in runs if r.get("pass") in (0, 1)]
    faults = [r for r in runs if r.get("platform_fault")]
    agg = passk_aggregate(judged) if judged else {
        "acc": 0.0, "pass2": 0.0, "pass3": 0.0,
        "n_questions": 0, "n_runs": 0}
    latencies = [d["p50_latency_s"] for d in details if d.get("p50_latency_s")]
    traces = [d["trace_completeness"] for d in details]
    by_type: dict[str, dict[str, Any]] = {}
    for qtype in ("F", "M", "V", "X"):
        sub = [r for r in judged if r.get("type") == qtype]
        if sub:
            by_type[qtype] = {
                "n": len({r["question_id"] for r in sub}),
                "acc": round(sum(r["pass"] for r in sub) / len(sub), 4),
            }
    parse_errors = sum(1 for r in runs if r.get("parse_error"))
    metric = {
        "acc": agg["acc"],
        "pass2": agg["pass2"],
        "pass3": agg["pass3"],
        "n_questions": agg["n_questions"],
        "n_runs": agg["n_runs"],
        "trace_machine": round(sum(traces) / len(traces), 4) if traces else 0.0,
        "p95_latency_s": _p95(latencies),
        "tokens_in": sum(d["tokens_in"] for d in details),
        "tokens_out": sum(d["tokens_out"] for d in details),
        "judge_parse_errors": parse_errors,
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
                   retriever: Retriever | None = None) -> dict[str, Any]:
    """Run the E1 baseline over a testset and return the metrics payload."""
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
        "testset": Path(testset_path).name,
    }

    all_runs: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for i, item in enumerate(questions, start=1):
        logger.info("[%d/%d] %s", i, len(questions), item.get("id"))
        per_run, detail = await run_question(
            router, item, retriever, runs=runs, top_k=top_k, lang=lang)
        all_runs.extend(per_run)
        details.append(detail)

    metrics = summarize(all_runs, details, config)
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
    parser.add_argument("--out", default=None, help="write the full JSON here")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.pace is not None:
        global MIN_CALL_INTERVAL_SECONDS
        MIN_CALL_INTERVAL_SECONDS = args.pace

    metrics = asyncio.run(evaluate(
        Path(args.testset), runs=args.runs, top_k=args.top_k,
        limit=args.limit, tenant_id=args.tenant, lang=args.lang))

    run_id = None
    if not args.no_persist:
        data = json.loads(Path(args.testset).read_text(encoding="utf-8"))
        from services.knowevo.pipeline.eval_v1 import _testset_hash
        run_id = persist_eval_run(metrics, _testset_hash(data), args.tenant)
        append_cost_ledger(
            metrics, run_id,
            f"E1 纯RAG 基线 {metrics['n_questions']}题×{args.runs} runs; "
            f"acc={metrics['acc']} pass2={metrics['pass2']} "
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
        "trace_machine": metrics["trace_machine"],
        "p95_latency_s": metrics["p95_latency_s"],
        "tokens_in": metrics["tokens_in"],
        "tokens_out": metrics["tokens_out"],
        "judge_parse_errors": metrics["judge_parse_errors"],
        "platform_faults": metrics["platform_faults"],
        "by_type": metrics["by_type"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())