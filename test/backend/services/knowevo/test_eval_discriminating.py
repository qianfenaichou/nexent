"""T-28 tests: discriminating testset + eval_v1 additive path support.

Layer 1 (always runs): the T-28 discriminating testset
(competition/corpus/testset-discriminating-v1.json) passes the frozen
validate_testset contract, the default-path CLI behavior is unchanged
(the frozen v1 seed), and the additive question fields
(discriminating / probe_evidence) are tolerated. No database, no LLM.
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.knowevo.pipeline import eval_v1
from services.knowevo.pipeline.eval_v1 import (
    DEFAULT_TESTSET_PATH,
    _testset_hash,
    validate_testset,
    validate_testset_file,
)

SEED_PATH = _REPO_ROOT / "competition" / "corpus" / "testset-v1-seed.json"
DISCRIM_PATH = (_REPO_ROOT / "competition" / "corpus"
                / "testset-discriminating-v1.json")


class TestDiscriminatingTestset:
    def test_discriminating_set_exists_and_is_valid(self):
        assert DISCRIM_PATH.exists()
        data = json.loads(DISCRIM_PATH.read_text(encoding="utf-8"))
        errors = validate_testset(data)
        assert errors == [], f"discriminating set invalid: {errors}"

    def test_discriminating_set_shape(self):
        data = json.loads(DISCRIM_PATH.read_text(encoding="utf-8"))
        qs = data["questions"]
        assert len(qs) == 8
        for q in qs:
            # every question is a V-type with dual gold labels and the
            # discriminating markers, per the T-28 brief
            assert q["type"] == "V"
            assert q["id"].startswith("V-")
            ans = q["answer"]
            assert isinstance(ans, dict)
            assert "answer_old" in ans and "answer_new" in ans
            assert q["discriminating"] is True
            pe = q["probe_evidence"]
            # the brief's discriminative criterion, recorded per question
            assert pe["valid_evidence_edges_old"] == 0
            assert pe["valid_evidence_edges_new"] >= 1
            assert pe["entity"] and pe["entity_stable_id"]

    def test_discriminating_set_hash_is_frozen(self):
        data = json.loads(DISCRIM_PATH.read_text(encoding="utf-8"))
        assert data.get("testset_hash") == _testset_hash(data)

    def test_probe_entities_meet_discriminative_rule(self):
        """At least 4 distinct entities, each with the 0-old / >=1-new
        evidence-edge signature the brief requires."""
        data = json.loads(DISCRIM_PATH.read_text(encoding="utf-8"))
        entities = {q["probe_evidence"]["entity_stable_id"]
                    for q in data["questions"]}
        assert len(entities) >= 4


class TestDefaultPathUnchanged:
    def test_default_testset_path_is_the_v1_seed(self):
        assert DEFAULT_TESTSET_PATH == SEED_PATH
        assert DEFAULT_TESTSET_PATH.exists()

    def test_default_seed_still_validates(self):
        errors = validate_testset_file(DEFAULT_TESTSET_PATH)
        assert errors == [], f"seed invalid: {errors}"
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        assert len(data["questions"]) == 20

    def test_cli_validate_without_testset_uses_default(self, capsys):
        """--validate with no --testset now resolves to the frozen seed
        (previously it crashed on None); the output shape is unchanged."""
        rc = eval_v1.main(["--validate"])
        assert rc == 0
        out = capsys.readouterr().out
        assert out.startswith("VALID: 20 questions, hash=")

    def test_cli_validate_explicit_testset_unchanged(self, capsys):
        rc = eval_v1.main(["--testset", str(DISCRIM_PATH), "--validate"])
        assert rc == 0
        out = capsys.readouterr().out
        assert out.startswith("VALID: 8 questions, hash=")

    def test_cli_validate_missing_file_fails_loudly(self, tmp_path):
        """A nonexistent path fails hard (uncaught OSError -> non-zero
        process exit), never silently validates an empty testset."""
        import pytest

        with pytest.raises(FileNotFoundError):
            eval_v1.main(["--testset", str(tmp_path / "nope.json"),
                          "--validate"])


class TestAdditiveFieldTolerance:
    def test_unknown_question_fields_tolerated(self):
        """validate_testset inspects only the frozen shape: additive
        fields (discriminating / probe_evidence / any future key) must
        not produce violations - the K4 semantic rules stay intact."""
        base = {
            "id": "V-901", "type": "V",
            "question": "additive-field probe",
            "answer": {"answer_old": "refuse", "answer_new": "7.0%"},
            "rubric": {
                "key_facts": ["7.0%"],
                "weights": {"key_facts": 0.6, "refusal": 0.1,
                            "evidence": 0.1, "reasoning": 0.1,
                            "safety": 0.1},
            },
            "evidence_origin": {"doc_ids": ["d1"], "blind_set_ratio": 0.0},
            "discriminating": True,
            "probe_evidence": {"entity": "e", "entity_stable_id": "E:x",
                               "valid_evidence_edges_old": 0,
                               "valid_evidence_edges_new": 1},
            "some_future_field": {"nested": [1, 2, 3]},
        }
        assert validate_testset({"questions": [base]}) == []

    def test_seed_without_additive_fields_still_validates(self):
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        for q in data["questions"]:
            assert "discriminating" not in q
            assert "probe_evidence" not in q
        assert validate_testset(data) == []
