"""
Unit tests for services/knowevo/pipeline/eval_v1.py (T-10a).

Layer 1 (always runs): testset schema validation (valid seed passes,
each K4 rule has a negative case), pass^k aggregation math, and the
bilingual judge/expand prompt rendering (both langs produce non-empty
system+user with placeholders substituted). No database and no LLM.
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.pipeline.eval_v1 import (
    DEFAULT_WEIGHTS,
    TYPE_TARGETS,
    _testset_hash,
    passk_aggregate,
    render_expand,
    render_judge,
    validate_testset,
)

# The real v1 seed lives in competition/corpus/; tests load it so the
# validator is exercised against the actual deliverable.
SEED_PATH = (_REPO_ROOT / "competition" / "corpus" / "testset-v1-seed.json")


def _load_seed():
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def _q(qid="F-001", qtype="F", answer="二甲双胍", **overrides):
    q = {
        "id": qid, "type": qtype, "question": "测试题", "answer": answer,
        "rubric": {
            "key_facts": ["二甲双胍"],
            "refusal_expected": False, "evidence_required": False,
            "reasoning_steps": [], "safety_boundary": "",
            "weights": dict(DEFAULT_WEIGHTS),
        },
        "evidence_origin": {"doc_ids": ["d1"], "blind_set_ratio": 0.0},
    }
    q.update(overrides)
    return q


# ---------------------------------------------------------------------------
# Testset validation
# ---------------------------------------------------------------------------

class TestValidateTestset:
    def test_v1_seed_is_valid(self):
        data = _load_seed()
        errors = validate_testset(data)
        assert errors == [], f"seed invalid: {errors}"
        qs = data["questions"]
        assert len(qs) == 20
        from collections import Counter
        types = Counter(q["type"] for q in qs)
        assert types == {"F": 5, "M": 5, "V": 5, "X": 5}

    def test_v1_seed_has_refusal_questions(self):
        """K4 6.1 X-type tests knowledge-boundary self-awareness: at least
        half of the X questions must be out-of-corpus refusal items."""
        data = _load_seed()
        x_questions = [q for q in data["questions"] if q["type"] == "X"]
        refusals = [q for q in x_questions
                    if q["rubric"]["refusal_expected"]]
        assert len(refusals) >= len(x_questions) // 2, (
            "X-type must include out-of-corpus refusal questions (K4 6.1)")

    def test_v1_seed_blind_ratio_deferred_is_documented(self):
        """K4 6.1 anti-overfitting isolation (M/F >= 0.5 blind) is a 120-set
        build-time rule; the 20-question seed keeps blind_set_ratio 0 for
        corpus-backed questions (refusal items are 1.0 = fully out-of-corpus)
        and must not silently pretend otherwise (deferred to T-10a-3)."""
        data = _load_seed()
        for q in data["questions"]:
            ratio = q["evidence_origin"]["blind_set_ratio"]
            is_refusal = q["rubric"]["refusal_expected"]
            if is_refusal:
                assert ratio == 1.0
            else:
                assert ratio == 0.0

    def test_invalid_type_rejected(self):
        data = {"questions": [_q(qid="Q-001", qtype="Q")]}
        assert any("invalid type" in e for e in validate_testset(data))

    def test_id_must_match_type(self):
        data = {"questions": [_q(qid="M-001", qtype="F")]}
        assert any("does not start with" in e for e in validate_testset(data))

    def test_v_needs_dual_gold(self):
        data = {"questions": [_q(qid="V-001", qtype="V", answer="7.0%")]}
        assert any("answer_old/answer_new" in e
                   for e in validate_testset(data))

    def test_rubric_weights_must_sum_and_key_facts_0_6(self):
        bad_weights = {"key_facts": 0.5, "refusal": 0.1, "evidence": 0.1,
                       "reasoning": 0.1, "safety": 0.1}
        data = {"questions": [_q(rubric={
            "key_facts": ["x"], "weights": bad_weights})]}
        errs = validate_testset(data)
        assert any("sum to 1.0" in e for e in errs)
        assert any("key_facts must be 0.6" in e for e in errs)

    def test_missing_key_facts_rejected(self):
        data = {"questions": [_q(rubric={"key_facts": [], "weights": {}})]}
        assert any("key_facts must be a non-empty list" in e
                   for e in validate_testset(data))

    def test_missing_evidence_origin_rejected(self):
        q = _q()
        del q["evidence_origin"]
        data = {"questions": [q]}
        assert any("blind_set_ratio" in e for e in validate_testset(data))


# ---------------------------------------------------------------------------
# pass^k aggregation
# ---------------------------------------------------------------------------

class TestPassKAggregate:
    def test_all_pass_gives_pass3_1(self):
        runs = [{"question_id": f"q{i}", "pass": 1} for i in range(3)
                for _ in range(3)]
        out = passk_aggregate(runs)
        assert out["acc"] == 1.0 and out["pass2"] == 1.0 and out["pass3"] == 1.0
        assert out["n_questions"] == 3 and out["n_runs"] == 9

    def test_two_of_three_pass_counts_as_pass2(self):
        runs = ([{"question_id": "a", "pass": 1}] * 2
                + [{"question_id": "a", "pass": 0}]
                + [{"question_id": "b", "pass": 1}] * 3)
        out = passk_aggregate(runs)
        assert out["pass2"] == 1.0   # a: 2/3 >= 2; b: 3/3
        assert out["pass3"] == 0.5   # only b has 3/3
        assert out["acc"] == pytest.approx(5 / 6, abs=1e-3)  # rounded 4dp

    def test_empty_runs_no_crash(self):
        out = passk_aggregate([])
        assert out["n_questions"] == 0 and out["pass2"] == 0.0


# ---------------------------------------------------------------------------
# Prompt rendering (bilingual pairs)
# ---------------------------------------------------------------------------

class TestPromptRendering:
    def test_judge_both_langs_render(self):
        vars_ = {"question": "二甲双胍?", "key_facts": "双胍类",
                 "refusal_expected": "false", "evidence_required": "false",
                 "reasoning_steps": "[]", "safety_boundary": "",
                 "answer": "双胍类", "evidence_chain": ""}
        for lang in ("en", "zh"):
            system, user = render_judge(lang, **vars_)
            assert system and user
            assert "{{" not in user  # all placeholders substituted

    def test_expand_both_langs_render(self):
        vars_ = {"question": "种子题", "answer": "答案",
                 "type": "F"}
        for lang in ("en", "zh"):
            system, user = render_expand(lang, **vars_)
            assert system and user
            assert "{{" not in user

    def test_type_targets_frozen(self):
        assert TYPE_TARGETS == {"F": 35, "M": 45, "V": 20, "X": 20}

    def test_hash_deterministic(self):
        data = _load_seed()
        assert _testset_hash(data) == _testset_hash(data)
        assert len(_testset_hash(data)) == 16
