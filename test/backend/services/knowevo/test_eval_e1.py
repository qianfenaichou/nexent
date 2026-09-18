"""Unit tests for services/knowevo/pipeline/eval_e1.py (T-10a-2).

Layer 1: prompt rendering, judge-output parsing, pass^k aggregation, trace
completeness and cost-ledger / eval_run_t persistence (monkey-patched to
in-memory records). No network, no LLM, no real database.

The end-to-end LLM path (retrieve -> generate -> judge against the live
corpus) is exercised by the CLI smoke runs recorded in the brief, not here -
these tests lock the pure semantics so a regression in the pipeline logic is
caught without paying real tokens.
"""
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.pipeline import eval_e1
from services.knowevo.pipeline.eval_e1 import (
    _is_platform_fault,
    evidence_chain_text,
    parse_judge_output,
    passk_aggregate,
    render_answer_prompt,
    render_judge_prompt,
    summarize,
    trace_completeness,
)

ITEM = {
    "id": "M-001",
    "type": "M",
    "question": "磺脲类药物通过促进哪个器官分泌什么物质降糖？",
    "rubric": {
        "key_facts": ["胰岛β细胞", "分泌胰岛素"],
        "refusal_expected": False,
        "evidence_required": True,
        "reasoning_steps": ["识别磺脲类作用靶点", "链接到胰岛素分泌"],
        "safety_boundary": "用药须遵医嘱",
        "weights": {"key_facts": 0.6, "refusal": 0.1, "evidence": 0.1,
                    "reasoning": 0.1, "safety": 0.1},
    },
    "evidence_origin": {"blind_set_ratio": 0.0},
}


class TestRenderAnswerPrompt:
    def test_context_and_question_are_injected(self):
        prompt = render_answer_prompt("片段内容", "问题内容", lang="zh")
        assert "片段内容" in prompt
        assert "问题内容" in prompt
        assert "知识库" in prompt  # zh system prompt loaded

    def test_en_prompt_loads(self):
        prompt = render_answer_prompt("ctx", "q", lang="en")
        assert "Retrieved knowledge base" in prompt


class TestRenderJudgePrompt:
    def test_all_five_tuple_placeholders_filled(self):
        prompt = render_judge_prompt(ITEM, "答案内容", "证据链内容", lang="zh")
        assert "胰岛β细胞" in prompt
        assert "分泌胰岛素" in prompt
        assert "答案内容" in prompt
        assert "证据链内容" in prompt
        assert "False" in prompt  # refusal_expected
        assert "用药须遵医嘱" in prompt

    def test_missing_rubric_keys_do_not_crash(self):
        item = {"id": "X-1", "type": "X", "question": "q",
                "rubric": {"key_facts": []}}
        prompt = render_judge_prompt(item, "a", "", lang="zh")
        assert prompt


class TestParseJudgeOutput:
    def test_valid_json(self):
        raw = ('{"pass":1,"total":0.9,"items":{"key_facts":1,"refusal":1,'
               '"evidence":1,"reasoning":1,"safety":1},"reason":"ok"}')
        out = parse_judge_output(raw)
        assert out["pass"] == 1
        assert out["total"] == pytest.approx(1.0)
        assert out["parse_error"] is False

    def test_truncated_json_is_parse_error(self):
        # Provider truncation mid-JSON (observed on the free tier): this must
        # surface as a judge failure, never as a silent wrong answer.
        out = parse_judge_output('{"pass": 0, "total": 0.3, "items": {"')
        assert out["parse_error"] is True and out["pass"] == 0

    def test_partial_items_reweight(self):
        # key_facts covered, everything else 0 -> 0.6
        raw = ('{"pass":0,"total":0.6,"items":{"key_facts":1,"refusal":0,'
               '"evidence":0,"reasoning":0,"safety":0},"reason":"missing"}')
        out = parse_judge_output(raw)
        assert out["total"] == pytest.approx(0.6)
        assert out["pass"] == 0

    def test_empty_output(self):
        out = parse_judge_output("")
        assert out["pass"] == 0 and out["parse_error"] is True

    def test_garbage_output(self):
        out = parse_judge_output("我觉得这个回答不错")
        assert out["pass"] == 0 and out["parse_error"] is True

    def test_veto_overrides_arithmetic_pass(self):
        raw = ('{"pass":0,"total":0.95,"items":{"key_facts":1,"refusal":1,'
               '"evidence":1,"reasoning":1,"safety":0.75},'
               '"reason":"veto: fabricated facts"}')
        out = parse_judge_output(raw)
        assert out["pass"] == 0

    def test_non_veto_zero_does_not_override(self):
        raw = ('{"pass":0,"total":0.9,"items":{"key_facts":1,"refusal":1,'
               '"evidence":1,"reasoning":1,"safety":0.5},'
               '"reason":"safety missing"}')
        out = parse_judge_output(raw)
        assert out["pass"] == 1  # arithmetic >= 0.8 wins without a veto


class TestPassk:
    def test_pass2_pass3_share(self):
        runs = [
            {"question_id": "M-001", "pass": 1},
            {"question_id": "M-001", "pass": 1},
            {"question_id": "M-001", "pass": 0},
            {"question_id": "F-001", "pass": 1},
            {"question_id": "F-001", "pass": 1},
            {"question_id": "F-001", "pass": 1},
        ]
        agg = passk_aggregate(runs)
        assert agg["pass2"] == 1.0  # both questions >=2 passes
        assert agg["pass3"] == 0.5  # only F-001 has 3 passes
        assert agg["n_questions"] == 2 and agg["n_runs"] == 6

    def test_single_run_per_question(self):
        runs = [{"question_id": "F-001", "pass": 1},
                {"question_id": "M-001", "pass": 1}]
        agg = passk_aggregate(runs)
        assert agg["acc"] == 1.0
        # pass2 needs >=2 runs per question; with 1 run each it stays 0 - the
        # K4 protocol runs 3x, so this only happens for smoke runs.
        assert agg["pass2"] == 0.0


class TestTrace:
    def test_full_locators(self):
        ev = [{"rank": 1, "doc_id": "a", "chunk_idx": 0, "span_hash": "h",
               "title": "t"}]
        assert trace_completeness(ev) == 1.0

    def test_missing_locator_dropped(self):
        ev = [{"rank": 1, "doc_id": "a", "chunk_idx": 0, "span_hash": "h",
               "title": "t"},
              {"rank": 2, "doc_id": "b"}]  # missing chunk_idx/span_hash/title
        assert trace_completeness(ev) == 0.5

    def test_empty(self):
        assert trace_completeness([]) == 0.0


class TestEvidenceChainText:
    def test_renders_locators(self):
        ev = [{"rank": 1, "title": "指南", "doc_id": "G24", "chunk_idx": 3,
               "score": 9.1, "split": "build"}]
        text = evidence_chain_text(ev)
        assert "[1]" in text and "G24" in text and "build" in text

    def test_empty(self):
        assert "(no passages retrieved)" in evidence_chain_text([])


class TestPlatformFaultDetection:
    def test_free_tier_gateway_message(self):
        assert _is_platform_fault(
            "Error code: 500 - {'error': {'message': 'no active accounts "
            "available: total=36 active=0 cooldown=36'}}")

    def test_rate_limit_and_quota(self):
        assert _is_platform_fault("HTTP 429 Too Many Requests")
        assert _is_platform_fault("insufficient_quota")

    def test_real_verdict_is_not_a_fault(self):
        assert not _is_platform_fault("答案遗漏了必答点 胰岛β细胞")

    def test_empty_string(self):
        assert not _is_platform_fault("")


class TestSummarizePlatformFaults:
    def test_faults_excluded_from_denominator(self):
        # 2 judged runs for F-001 (1 pass), 1 platform fault for M-001: the
        # fault must not appear as a wrong answer.
        runs = [
            {"question_id": "F-001", "pass": 1, "type": "F"},
            {"question_id": "F-001", "pass": 0, "type": "F"},
            {"question_id": "M-001", "pass": None, "type": "M",
             "platform_fault": True, "error": "judge_unavailable"},
        ]
        details = [{"question_id": "F-001", "type": "F", "p50_latency_s": 4.0,
                    "trace_completeness": 1.0, "tokens_in": 10, "tokens_out": 5}]
        m = summarize(runs, details, {"ablation_level": "A1_pure_rag"})
        assert m["acc"] == 0.5  # 1 pass / 2 judged, fault excluded
        assert m["n_runs"] == 2
        assert m["platform_faults"]["n"] == 1
        assert m["platform_faults"]["judge"] == 1
        assert "M" not in m["by_type"]  # M had no judged run

    def test_all_faults_yields_zero_metrics(self):
        runs = [{"question_id": "F-001", "pass": None, "type": "F",
                 "platform_fault": True, "error": "generation_failed"}]
        m = summarize(runs, [], {})
        assert m["acc"] == 0.0 and m["n_runs"] == 0
        assert m["platform_faults"]["generation"] == 1

    def test_generation_fault_not_counted_as_wrong(self):
        # A generation outage carries pass=None, never pass=0: with one real
        # pass and one outage the accuracy must be 1.0, not 0.5.
        runs = [
            {"question_id": "F-001", "pass": 1, "type": "F"},
            {"question_id": "F-002", "pass": None, "type": "F",
             "platform_fault": True, "error": "generation_failed"},
        ]
        m = summarize(runs, [], {})
        assert m["acc"] == 1.0
        assert m["n_runs"] == 1
        assert m["platform_faults"]["n"] == 1


class TestCallWithRetry:
    """Retry/abandon semantics for the LLM call wrapper (no real network)."""

    class _FakeRouter:
        def __init__(self, outcomes):
            self.outcomes = list(outcomes)
            self.calls = 0

        async def call_with_usage(self, prompt, *, kind, tier, temperature):
            self.calls += 1
            outcome = self.outcomes.pop(0) if self.outcomes else ("ok", {})
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    @pytest.fixture(autouse=True)
    def _fast_backoff(self, monkeypatch):
        # Retry pacing/backoff exist to survive real rate limits; tests must
        # not pay them (they were 8s per call before this fixture).
        monkeypatch.setattr(eval_e1, "RETRY_BACKOFF_SECONDS", 0.0)
        monkeypatch.setattr(eval_e1, "MIN_CALL_INTERVAL_SECONDS", 0.0)

    def test_platform_fault_then_success(self):
        router = self._FakeRouter([
            RuntimeError("no active accounts available: total=36"),
            ("answer", {"input_tokens": 5, "output_tokens": 3}),
        ])
        content, usage = asyncio.run(eval_e1._call_with_retry(
            router, "p", kind="k", tier="mid", qid="F-1", run_idx=0, label="judge"))
        assert content == "answer"
        assert usage["input_tokens"] == 5
        assert router.calls == 2  # retried exactly once

    def test_non_platform_error_abandons_without_retry(self):
        router = self._FakeRouter([ValueError("prompt template broken")])
        content, usage = asyncio.run(eval_e1._call_with_retry(
            router, "p", kind="k", tier="mid", qid="F-1", run_idx=0, label="gen"))
        assert content is None and usage is None
        assert router.calls == 1  # a code bug must not be retried as flaky infra

    def test_timeout_is_retried_then_abandoned(self):
        router = self._FakeRouter([RuntimeError("Read timed out")] * 6)
        content, _usage = asyncio.run(eval_e1._call_with_retry(
            router, "p", kind="k", tier="mid", qid="F-1", run_idx=0, label="judge"))
        assert content is None
        assert router.calls == eval_e1.JUDGE_RETRIES  # bounded, not infinite

    def test_hard_timeout_bounds_a_hung_call(self, monkeypatch):
        monkeypatch.setattr(eval_e1, "CALL_HARD_TIMEOUT_SECONDS", 0.05)

        class _Hanging:
            async def call_with_usage(self, prompt, *, kind, tier, temperature):
                await asyncio.sleep(30)

        content, usage = asyncio.run(eval_e1._call_with_retry(
            _Hanging(), "p", kind="k", tier="mid", qid="F-1", run_idx=0,
            label="judge"))
        assert content is None and usage is None


class TestRetryAfter:
    def test_parses_retry_after_seconds(self):
        assert eval_e1._retry_after_seconds("Retry in 147.9251267455876 seconds") \
            == pytest.approx(147.9251267455876)

    def test_parses_retry_after_colon_form(self):
        assert eval_e1._retry_after_seconds("retry_after: 30") == 30.0

    def test_capped_so_a_garbled_value_cannot_park_the_batch(self):
        assert eval_e1._retry_after_seconds("retry in 999999 seconds") == 300.0

    def test_absent_returns_none(self):
        assert eval_e1._retry_after_seconds("inference exceeds tpm/rpm limit") is None


class TestSummarize:
    def test_shape_matches_eval_run_t_metrics(self):
        runs = [
            {"question_id": "F-001", "pass": 1, "run": 0, "type": "F"},
            {"question_id": "F-001", "pass": 1, "run": 1, "type": "F"},
            {"question_id": "M-001", "pass": 0, "run": 0, "type": "M"},
            {"question_id": "M-001", "pass": 0, "run": 1, "type": "M"},
        ]
        details = [
            {"question_id": "F-001", "type": "F", "p50_latency_s": 5.0,
             "trace_completeness": 1.0, "tokens_in": 100, "tokens_out": 50},
            {"question_id": "M-001", "type": "M", "p50_latency_s": 15.0,
             "trace_completeness": 0.5, "tokens_in": 200, "tokens_out": 80},
        ]
        config = {"ablation_level": "A1_pure_rag", "model_plan": {},
                  "knowledge_stamp": {}}
        m = summarize(runs, details, config)
        assert m["acc"] == 0.5
        assert m["n_questions"] == 2 and m["n_runs"] == 4
        assert m["trace_machine"] == 0.75
        assert m["p95_latency_s"] == pytest.approx(15.0)
        assert m["tokens_in"] == 300 and m["tokens_out"] == 130
        assert m["by_type"]["F"]["acc"] == 1.0
        assert m["by_type"]["M"]["acc"] == 0.0
        assert m["config"] is config  # config carried through