"""Unit tests for services/knowevo/pipeline/ablation.py (T-22).

Orchestration with fakes only (no network, no LLM, no real database):
  * four-level config routing and eval_run_t config capture,
  * by_type x by_level cross table incl. insufficient_data cells,
  * pin on/off through the version_pin single entry (as_of captured),
  * E8 arm-gold judging (old/new rubric selection),
  * Wilson CI + budget checkpointing.

The end-to-end LLM path is exercised by the CLI smoke runs recorded in the
brief/report, not here.
"""
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.decision_service import DecisionService
from services.knowevo.e1_retrieval import Chunk, Retriever
from services.knowevo.graph_store import EdgeCard, EntityCard, Subgraph
from services.knowevo.pipeline import ablation, eval_e1
from services.knowevo.pipeline.eval_v1 import cross_table, wilson_interval

PIN_DT = datetime(2024, 6, 1, tzinfo=UTC)

ITEM = {
    "id": "M-001",
    "type": "M",
    "question": "磺脲类药物通过促进哪个器官分泌什么物质降糖？",
    "rubric": {
        "key_facts": ["胰岛β细胞", "分泌胰岛素"],
        "refusal_expected": False,
        "evidence_required": True,
        "reasoning_steps": [],
        "safety_boundary": "",
        "weights": {"key_facts": 0.6, "refusal": 0.1, "evidence": 0.1,
                    "reasoning": 0.1, "safety": 0.1},
    },
    "evidence_origin": {"blind_set_ratio": 0.0},
}

V_ITEM = {
    "id": "V-004",
    "type": "V",
    "question": "2020版与2024版指南对老年糖尿病患者的血糖控制目标有何变化？",
    "answer": {"answer_old": "相对宽松",
               "answer_new": "相对宽松（个体化）"},
    "rubric": {
        "key_facts": ["宽松", "个体化"],
        "refusal_expected": False,
        "evidence_required": True,
        "reasoning_steps": [],
        "safety_boundary": "",
        "weights": {"key_facts": 0.6, "refusal": 0.1, "evidence": 0.1,
                    "reasoning": 0.1, "safety": 0.1},
    },
    "evidence_origin": {"blind_set_ratio": 0.0},
}

DOC_TEXT = "磺脲类药物通过促进胰岛β细胞分泌胰岛素降低血糖。（指南原文片段）"


def make_retriever() -> Retriever:
    return Retriever(chunks=[Chunk(doc_id="g1", title="中国2型糖尿病防治指南",
                                   chunk_idx=0, text=DOC_TEXT, source="g1",
                                   authority_level=1, split="build")])


def make_graph(contested: bool = False):
    ents = [EntityCard(stable_id="ent_sulfonylurea", name="磺脲类",
                       class_ref="Drug"),
            EntityCard(stable_id="ent_beta_cell", name="胰岛β细胞",
                       class_ref="Organ")]
    edges = [EdgeCard(id="e1", src="ent_sulfonylurea", dst="ent_beta_cell",
                      rel_type="stimulates",
                      claim="磺脲类药物促进胰岛β细胞分泌胰岛素",
                      contested=contested)]
    return FakeStore(entities=ents, edges=edges)


class FakeStore:
    """In-memory store double: entity_lookup + neighbors with as_of capture."""

    def __init__(self, entities=(), edges=()):
        self.entities = list(entities)
        self.edges = list(edges)
        self.neighbor_calls: list[dict] = []

    async def entity_lookup(self, tenant_id, query, top_k=5):
        return [e for e in self.entities if query in e.name][:top_k]

    async def neighbors(self, tenant_id, entity_ids, hop=1, valid_view=True,
                        **kwargs):
        self.neighbor_calls.append({"ids": list(entity_ids),
                                    "valid_view": valid_view,
                                    "kwargs": kwargs})
        ids = set(entity_ids)
        return Subgraph(
            entities=[e for e in self.entities if e.stable_id in ids],
            edges=[e for e in self.edges if e.src in ids or e.dst in ids])


class FakeRouter:
    """Scripted three-tier router: dispatches on the call kind."""

    def __init__(self, answer: str = "磺脲类药物促进胰岛β细胞分泌胰岛素[1]。"):
        self.answer = answer
        self.calls: list[dict] = []

    async def call_with_usage(self, prompt, *, kind, tier, temperature):
        self.calls.append({"kind": kind, "prompt": prompt, "tier": tier})
        usage = {"input_tokens": 10, "output_tokens": 4}
        if kind.endswith("route_llm"):
            return json.dumps({"route": "RM", "confidence": 0.9,
                               "reason": "few-shot"}), usage
        if kind.endswith("hop_plan"):
            return json.dumps({"hops": [{"rel_types": []}]}), usage
        if kind.endswith("decision_card"):
            return json.dumps({
                "decision": "RECOMMEND",
                "candidates": [{
                    "option": "磺脲类药物",
                    "score": 0.8,
                    "confidence_calibrated": 0.7,
                    "evidence_chain": [{
                        "claim": "磺脲类药物促进胰岛β细胞分泌胰岛素",
                        "provenance": {"doc": "指南", "span": "",
                                       "kg_path": ["磺脲类"],
                                       "version_pinned": False},
                        "tag": "EXTRACTED", "source_channel": "kg"}],
                    "risks": [],
                }],
                "uncertainty_notes": [],
            }), usage
        if kind.endswith("judge"):
            return json.dumps({
                "pass": 1,
                "items": {"key_facts": 1, "refusal": 1, "evidence": 1,
                          "reasoning": 1, "safety": 1},
                "reason": "ok"}), usage
        # generation (e1_answer / ablation_answer)
        return self.answer, usage


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(eval_e1, "MIN_CALL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(eval_e1, "RETRY_BACKOFF_SECONDS", 0.0)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Seed terms / arm gold
# ---------------------------------------------------------------------------

class TestExtractSeedTerms:
    def test_ascii_words_and_cjk_runs(self):
        terms = ablation.extract_seed_terms("SGLT2抑制剂通过哪个器官排泄葡萄糖？")
        assert "SGLT2" in terms
        assert any("抑制剂" in t or "抑制剂" == t for t in terms)

    def test_long_run_yields_windows(self):
        run = "磺脲类药物通过促进胰岛素分泌降血糖"  # len > 6
        terms = ablation.extract_seed_terms(run)
        assert run in terms
        assert any(len(t) == 3 and t in run for t in terms)
        assert "磺脲类" in terms  # first 3-char window

    def test_window_can_substring_match_entity_name(self):
        # the store matches names CONTAINING the query, so the window must
        # be able to land inside a short entity name
        terms = ablation.extract_seed_terms(ITEM["question"])
        assert "磺脲类" in terms

    def test_dedup_and_cap(self):
        run = "磺脲类药物通过促进胰岛素分泌降血糖" * 3
        terms = ablation.extract_seed_terms(run, max_terms=3)
        assert len(terms) == 3
        assert len(set(terms)) == len(terms)


class TestArmItem:
    def test_pin_on_narrows_to_old_facts(self):
        out = ablation.arm_item(V_ITEM, pin_on=True)
        assert out["rubric"]["key_facts"] == ["宽松"]
        assert out["arm_gold"] == "相对宽松"

    def test_pin_off_keeps_full_rubric_when_gold_covers_all(self):
        out = ablation.arm_item(V_ITEM, pin_on=False)
        # answer_new contains both facts -> no narrowing (nothing to strip)
        assert out["rubric"]["key_facts"] == ["宽松", "个体化"]
        assert out["arm_gold"] == "相对宽松（个体化）"

    def test_equal_golds_leave_rubric_unchanged(self):
        item = dict(V_ITEM, answer={"answer_old": "5.3", "answer_new": "5.3"},
                    rubric=dict(V_ITEM["rubric"], key_facts=["5.3"]))
        out = ablation.arm_item(item, pin_on=True)
        assert out["rubric"]["key_facts"] == ["5.3"]

    def test_f_type_passthrough(self):
        out = ablation.arm_item(ITEM, pin_on=True)
        assert out is ITEM
        assert "arm_gold" not in out


# ---------------------------------------------------------------------------
# Graph channel (A2) + pin switch (B4)
# ---------------------------------------------------------------------------

class TestA2Channel:
    def test_neighborhood_merged_and_contested_marked(self):
        store = make_graph(contested=True)
        svc = DecisionService(store=store, llm=None, tenant_id="t")
        fused, records, chain, chain_text, kg = _run(ablation.build_channel(
            "A2_graph", ITEM, tenant_id="t", store=store, svc=svc,
            retriever=make_retriever(), top_k=3, pin_on=False,
            dt_pin_as_of=None))
        assert kg["n_seeds"] >= 1 and kg["n_edges"] == 1
        kg_records = [r for r in records if r.get("channel") == "kg"]
        assert len(kg_records) == 1
        assert kg_records[0]["chunk_idx"] is None  # graph facts are not chunks
        assert kg_records[0]["contested"] is True
        assert "contested" in fused  # surfaced in the generator context
        assert "(图谱)" in chain_text
        assert kg_records[0]["rank"] == len(records)  # numbering continues

    def test_empty_graph_is_visible_not_silent(self):
        store = FakeStore()
        svc = DecisionService(store=store, llm=None, tenant_id="t")
        _fused, records, _chain, _txt, kg = _run(ablation.build_channel(
            "A2_graph", ITEM, tenant_id="t", store=store, svc=svc,
            retriever=make_retriever(), top_k=3, pin_on=False,
            dt_pin_as_of=None))
        assert kg == {**kg, "n_seeds": 0, "n_edges": 0}
        assert all(r.get("channel") == "doc" for r in records)


class TestPinSwitch:
    """B4: the pin flows through the version_pin single entry only."""

    def test_pin_on_uses_explicit_as_of(self):
        store = make_graph()
        svc = DecisionService(store=store, llm=None, tenant_id="t")
        _fused, _recs, _chain, _txt, kg = _run(ablation.build_channel(
            "A4_full", ITEM, tenant_id="t", store=store, svc=svc,
            retriever=make_retriever(), top_k=3, pin_on=True,
            dt_pin_as_of=PIN_DT))
        assert kg["pinned"] is True
        assert kg["clock_source"] == "explicit"
        assert kg["kg_cutoff"] == PIN_DT.isoformat()
        pinned_calls = [c for c in store.neighbor_calls
                        if "as_of" in c["kwargs"]]
        assert pinned_calls, "pinned walk must query with as_of"
        assert all(c["kwargs"]["as_of"] == PIN_DT
                   for c in pinned_calls)

    def test_pin_off_never_passes_as_of(self):
        store = make_graph()
        svc = DecisionService(store=store, llm=None, tenant_id="t")
        _fused, _recs, _chain, _txt, kg = _run(ablation.build_channel(
            "A3_multihop", ITEM, tenant_id="t", store=store, svc=svc,
            retriever=make_retriever(), top_k=3, pin_on=False,
            dt_pin_as_of=PIN_DT))
        assert kg["pinned"] is False
        assert all("as_of" not in c["kwargs"]
                   for c in store.neighbor_calls)


# ---------------------------------------------------------------------------
# Level runners + config
# ---------------------------------------------------------------------------

class TestRunLevel:
    def _run_level(self, level, item, *, store=None, pin_on=False,
                   arm_gold=False, budget_deadline=None):
        router = FakeRouter()
        metrics, complete = _run(ablation.run_level(
            level, [item], router=router, retriever=make_retriever(),
            store=store or FakeStore(), runs=1, top_k=2,
            pin_on=pin_on, arm_gold=arm_gold,
            dt_pin_as_of=PIN_DT if pin_on else None,
            budget_deadline=budget_deadline))
        return metrics, complete, router

    def test_a2_config_and_judged_run(self):
        metrics, complete, router = self._run_level(
            "A2_graph", ITEM, store=make_graph())
        assert complete is True
        cfg = metrics["config"]
        assert cfg["ablation_level"] == "A2_graph"
        assert cfg["pin"] == "n/a"
        assert cfg["knowledge_stamp"]["graph"] == "pg_jsonb_store"
        assert metrics["n_judged"] == 1 and metrics["acc"] == 1.0
        assert "pass2_ci95" in metrics and "acc_ci95" in metrics
        # generation + judge went through the router; the fused channel
        # carried the graph claim into the generation prompt
        kinds = [c["kind"] for c in router.calls]
        assert "ablation_answer" in kinds and "ablation_judge" in kinds
        gen_prompt = next(c["prompt"] for c in router.calls
                          if c["kind"] == "ablation_answer")
        assert "图谱路径" in gen_prompt

    def test_a4_pin_on_config(self):
        metrics, _complete, router = self._run_level(
            "A4_full", ITEM, store=make_graph(), pin_on=True)
        cfg = metrics["config"]
        assert cfg["ablation_level"] == "A4_full"
        assert cfg["pin"] == "on"
        assert cfg["pin_as_of"] == PIN_DT.isoformat()
        assert metrics["decision_llm_calls"] >= 2  # route/hop-plan/card
        kinds = [c["kind"] for c in router.calls]
        assert "ablation_decision_card" in kinds
        detail_kg = metrics["details"][0]["kg_channel"]
        assert detail_kg["pinned"] is True
        assert "route" in metrics["details"][0]

    def test_a4_pin_off_config(self):
        metrics, _complete, _router = self._run_level(
            "A4_full", ITEM, store=make_graph(), pin_on=False)
        assert metrics["config"]["pin"] == "off"
        assert metrics["config"]["pin_as_of"] is None

    def test_unknown_level_rejected(self):
        with pytest.raises(ValueError, match="unknown ablation level"):
            _run(ablation.run_level("A9_bogus", [ITEM], router=FakeRouter(),
                                    retriever=make_retriever()))

    def test_budget_checkpoint_marks_incomplete(self):
        metrics, complete, _router = self._run_level(
            "A2_graph", ITEM, store=make_graph(),
            budget_deadline=0.0)  # already expired
        assert complete is False
        assert metrics["level_complete"] is False
        assert metrics["n_questions_run"] == 0
        assert metrics["n_expected"] == 0


class TestArmGoldJudging:
    """E8: the judge prompt must carry the arm's gold, only for E8 arms."""

    def test_e8_on_arm_judges_against_old_facts(self):
        router = FakeRouter()
        _run(ablation.run_level(
            "A4_full", [V_ITEM], router=router, retriever=make_retriever(),
            store=make_graph(), runs=1, top_k=2, pin_on=True, arm_gold=True,
            dt_pin_as_of=PIN_DT))
        judge_prompt = next(c["prompt"] for c in router.calls
                            if c["kind"] == "ablation_judge")
        assert "宽松" in judge_prompt
        assert "个体化" not in judge_prompt  # old-edition fact only

    def test_headline_run_keeps_standard_rubric(self):
        router = FakeRouter()
        _run(ablation.run_level(
            "A4_full", [V_ITEM], router=router, retriever=make_retriever(),
            store=make_graph(), runs=1, top_k=2, pin_on=True,
            arm_gold=False, dt_pin_as_of=PIN_DT))
        judge_prompt = next(c["prompt"] for c in router.calls
                            if c["kind"] == "ablation_judge")
        assert "宽松" in judge_prompt and "个体化" in judge_prompt


class TestEvalRunPersist:
    def test_config_written_insert_only(self, monkeypatch):
        captured: dict = {}

        def fake_create_row(model, **values):
            captured.update(values)
            return {"id": "row-1"}

        import database.knowevo_db as db
        monkeypatch.setattr(db, "create_row", fake_create_row)
        metrics = {"acc": 0.5, "pass2": 0.5, "pass3": 0.0,
                   "n_questions": 2, "n_runs": 2,
                   "config": {"ablation_level": "A3_multihop",
                              "pin": "n/a"}}
        run_id = eval_e1.persist_eval_run(metrics, "hash123", "tenant-1",
                                          task_ref="T-22")
        assert run_id == "row-1"
        assert captured["config"]["ablation_level"] == "A3_multihop"
        assert captured["task_ref"] == "T-22"
        assert captured["testset_hash"] == "hash123"
        # details/runs never reach the column
        assert "details" not in captured["metrics"]
        assert "runs" not in captured["metrics"]


# ---------------------------------------------------------------------------
# Aggregation: cross table, Wilson, E8 section
# ---------------------------------------------------------------------------

def _level_metrics(by_type):
    return {"by_type": by_type}


class TestCrossTable:
    def test_all_cells_present_with_insufficient_data(self):
        a1 = _level_metrics({
            "F": {"acc": 1.0, "n_judged": 3},
            "M": {"acc": 0.0, "n_judged": 3},
            "V": {"acc": 0.5, "n_judged": 2},
            "X": {"n": 0, "n_judged": 0, "acc": None,
                  "insufficient_data": True},
        })
        a4 = _level_metrics({
            "F": {"acc": 1.0, "n_judged": 3},
            "M": {"acc": 0.5, "n_judged": 4},
            "V": {"acc": 1.0, "n_judged": 2},
            "X": {"acc": 0.0, "n_judged": 3},
        })
        ct = cross_table({"A1_pure_rag": a1, "A4_full": a4})
        assert set(ct) == {"F", "M", "V", "X"}
        for qtype in ("F", "M", "V", "X"):
            assert set(ct[qtype]) == {"A1_pure_rag", "A4_full"}
        assert ct["F"]["A1_pure_rag"] == {"acc": 1.0, "n_judged": 3}
        assert ct["X"]["A1_pure_rag"]["insufficient_data"] is True
        assert ct["X"]["A1_pure_rag"]["acc"] is None
        assert "insufficient_data" not in ct["X"]["A4_full"]

    def test_missing_type_becomes_insufficient(self):
        ct = cross_table({"A2_graph": _level_metrics({})})
        assert ct["V"]["A2_graph"] == {"acc": None, "n_judged": 0,
                                       "insufficient_data": True}


class TestWilson:
    def test_centred_on_point_estimate(self):
        ci = wilson_interval(8, 10)
        assert ci["lo"] < 0.8 < ci["hi"]

    def test_degenerate_counts(self):
        assert wilson_interval(0, 5)["lo"] == 0.0
        assert wilson_interval(0, 5)["hi"] > 0
        assert wilson_interval(5, 5)["hi"] == 1.0
        assert wilson_interval(0, 0) == {"lo": None, "hi": None}


class TestE8Section:
    def test_delta_computed_when_both_arms_present(self):
        results = [
            {"level": "A4_full", "pin": "on", "arm_gold": True,
             "run_id": "r-on",
             "metrics": _level_metrics({
                 "F": {"acc": 0.6, "n_judged": 5},
                 "V": {"acc": 1.0, "n_judged": 1}})},
            {"level": "A4_full", "pin": "off", "arm_gold": True,
             "run_id": "r-off",
             "metrics": _level_metrics({
                 "F": {"acc": 0.8, "n_judged": 5},
                 "V": {"acc": 1.0, "n_judged": 1}})},
        ]
        e8 = ablation._e8_section(results)
        assert e8["per_type"]["F"]["delta_acc_on_minus_off"] == -0.2
        assert e8["per_type"]["F"]["pin_on"]["n_judged"] == 5
        assert e8["per_type"]["V"]["delta_acc_on_minus_off"] == 0.0
        assert "caveat" in e8 and "构建租户" in e8["caveat"]

    def test_absent_off_arm_returns_none(self):
        results = [{"level": "A4_full", "pin": "on", "arm_gold": False,
                    "run_id": "r", "metrics": _level_metrics({})}]
        assert ablation._e8_section(results) is None


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

class TestPlanParsing:
    def test_aliases_and_plan_fanout(self):
        levels = ablation._parse_levels("A1,A4")
        assert levels == ["A1_pure_rag", "A4_full"]
        plan = ablation.build_plan(levels, ["on", "off"])
        assert plan == [("A1_pure_rag", None), ("A4_full", "on"),
                        ("A4_full", "off")]

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            ablation._parse_levels("A9")
        with pytest.raises(ValueError):
            ablation._parse_pins("maybe")
        with pytest.raises(ValueError):
            ablation._parse_types("Z")

    def test_build_plan_single_pin(self):
        plan = ablation.build_plan(["A2_graph"], ["on"])
        assert plan == [("A2_graph", None)]
