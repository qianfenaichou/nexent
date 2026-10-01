"""Tests for the L5 zero-LLM ablation probe (probe_l5_ablation).

Spec anchors:
- design doc ``competition/docs/tech-optimization-2026-09-28/
  l5-community-summary-design-2026-09-29.md`` section 5 (arms A0/A1/A3
  mechanism, null path 5.4, Zeng defect mapping 5.2);
- probe module ``competition/experiments/probe_l5_ablation.py``.

Expected numbers were verified by running the implementation first
(pitfalls #150: never back-fill expected values from mental simulation).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PROBE_PATH = (
    _REPO_ROOT / "competition" / "experiments" / "probe_l5_ablation.py"
)
_spec = importlib.util.spec_from_file_location("probe_l5_ablation", _PROBE_PATH)
assert _spec is not None and _spec.loader is not None
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe_l5_ablation"] = probe
_spec.loader.exec_module(probe)


class TestMetrics:
    def test_hit_recall_ndcg_run_verified(self):
        # Cases executed against the probe before being pinned here.
        assert probe.hit_at_k(["a", "b", "c"], {"a"}, 3) == 1.0
        assert probe.recall_at_k(["a", "b", "c"], {"a"}, 3) == 1.0
        assert probe.ndcg_at_k(["a", "b", "c"], {"a"}, 3) == 1.0

        assert probe.hit_at_k(["b", "c", "a"], {"a"}, 3) == 1.0
        assert probe.hit_at_k(["b", "c", "a"], {"a"}, 1) == 0.0
        assert probe.ndcg_at_k(["b", "c", "a"], {"a"}, 3) == 0.5

        assert probe.hit_at_k(["b", "c", "d"], {"a"}, 3) == 0.0
        assert probe.recall_at_k(["b", "c", "d"], {"a"}, 3) == 0.0
        assert probe.ndcg_at_k(["b", "c", "d"], {"a"}, 3) == 0.0

        assert probe.ndcg_at_k(["a", "b"], {"a", "c", "b"}, 5) == pytest.approx(
            0.7653606369886217
        )
        assert probe.recall_at_k(["a", "b"], {"a", "c", "b"}, 5) == pytest.approx(
            2 / 3
        )

    def test_rank_metrics_keys(self):
        m = probe.rank_metrics(["a"], {"a"}, ks=(1, 3))
        assert set(m) == {
            "hit_at_1",
            "hit_at_3",
            "ndcg_at_1",
            "ndcg_at_3",
            "recall_at_1",
            "recall_at_3",
        }


class TestNullRule:
    def test_below_min_effect_is_null(self):
        verdict = probe.decide_main_conclusion(0.04, [0.0, 0.1])
        assert verdict["verdict"] == "null"
        assert verdict["min_effect"] == probe.MIN_EFFECT == 0.05

    def test_at_or_above_min_effect_detected(self):
        assert probe.decide_main_conclusion(0.05, [0.0, 0.1])["verdict"] == (
            "detected_at_mechanism_level"
        )
        assert probe.decide_main_conclusion(0.06, [0.0, 0.1])["verdict"] == (
            "detected_at_mechanism_level"
        )

    def test_missing_delta_is_null(self):
        assert probe.decide_main_conclusion(None, [None, None])["verdict"] == "null"

    def test_null_rule_is_preregistered_text(self):
        rule = probe.decide_main_conclusion(0.0, [0.0, 0.0])["null_rule"]
        assert "Pre-registered" in rule
        assert "No post-hoc" in rule


class TestFlatRank:
    def test_zero_match_falls_back_to_id_order(self):
        ranked, scores = probe.flat_entity_rank(
            "rice paddy",
            {"e001": "FieldUnit001", "e000": "FieldUnit000"},
        )
        assert ranked == ["e000", "e001"]
        assert scores == {"e000": 0, "e001": 0}

    def test_theme_token_on_name_wins(self):
        ranked, scores = probe.flat_entity_rank(
            "rice paddy",
            {"e000": "FieldUnit000", "e001": "rice FieldUnit001", "e002": "FieldUnit002"},
        )
        assert ranked[0] == "e001"
        assert scores["e001"] == 1


class TestGraphConstruction:
    def test_interleaved_ids_do_not_pack_one_community(self):
        nodes, member_of, _blocks = probe.assign_nodes(probe.SIZES)
        assert len(nodes) == sum(probe.SIZES)
        # First len(SIZES) ids come from distinct planted communities.
        first_block = nodes[: len(probe.SIZES)]
        assert len({member_of[n] for n in first_block}) == len(probe.SIZES)

    def test_build_graph_and_questions_deterministic(self):
        import random

        a = probe.build_graph(random.Random(probe.SEED))
        b = probe.build_graph(random.Random(probe.SEED))
        assert a[0] == b[0]
        assert a[1] == b[1]
        qa = probe.build_questions(a[2], a[3])
        qb = probe.build_questions(b[2], b[3])
        assert qa == qb
        assert sum(1 for q in qa if q["in_graph_scope"]) == (
            probe.N_QUESTIONS_PER_COMM * len(probe.SIZES)
        )
        assert sum(1 for q in qa if not q["in_graph_scope"]) == probe.N_OUT_OF_SCOPE


class TestTokenBudget:
    def test_budget_caps_take_and_counts_core(self):
        docs = {f"e{i:03d}": "tok" for i in range(5)}
        ranked = [f"e{i:03d}" for i in range(5)]
        core = ["e000", "e004"]
        # Each entity costs 1 token ("tok").
        out = probe.token_budget_hit(ranked, core, docs, budget=3)
        assert out["n_taken"] == 3
        assert out["tokens_spent"] == 3
        assert out["n_core_hit"] == 1
        assert out["core_hit_rate"] == 0.5


class TestPayload:
    @pytest.fixture(scope="class")
    def payload(self):
        return probe.build_payload()

    def test_checks_all_pass(self, payload):
        assert payload["results"]["checks"]["all_checks_pass"] is True

    def test_primary_is_preregistered_metric(self, payload):
        main = payload["results"]["main_conclusion"]
        assert main["primary_metric"] == probe.PRIMARY_METRIC == "ndcg_at_5"
        assert main["min_effect"] == 0.05
        assert main["verdict"] in ("null", "detected_at_mechanism_level")

    def test_llm_arms_are_insufficient_data(self, payload):
        for arm in payload["results"]["llm_arms"].values():
            assert arm["status"] == "insufficient_data"

    def test_out_of_scope_has_no_route_false_positives(self, payload):
        oos = payload["results"]["ablation"]["out_of_scope_control"]
        assert oos["counts"]["a1_route"] == 0
        assert oos["counts"]["a0_name"] == 0

    def test_a3_mechanism_delta_is_reported(self, payload):
        a3 = payload["results"]["ablation"]["a3_mechanism_token_budget"]
        for cell in a3.values():
            assert cell["delta_structure_minus_flat"] is not None

    def test_double_run_identity(self, payload):
        det = payload["results"]["determinism"]
        assert det["double_run_in_scope_identical"] is True
        assert det["double_run_out_of_scope_identical"] is True
        assert det["double_run_clustering_identical"] is True

    def test_content_sha256_recipe_is_stable(self, payload):
        import hashlib
        import json

        def digest(p: dict) -> str:
            canonical = json.dumps(
                p, sort_keys=True, ensure_ascii=False, indent=2
            ).encode("utf-8")
            return hashlib.sha256(canonical).hexdigest()

        # build_payload() omits content_sha256 on purpose (main() adds it
        # over the canonical dump). Two builds must hash identically.
        assert digest(payload) == digest(probe.build_payload())
        assert payload.get("content_sha256_recipe")
