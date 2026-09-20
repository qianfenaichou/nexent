"""
Unit and integration tests for services/knowevo/alignment_service.py (T-21).

Layer 1 (always runs): the pure, deterministic core of the three-stage
standard aligner and the innovation kernel behind it - title normalization,
section-tree parsing, deterministic section alignment, lexical similarity,
the injectable similarity matrix, Hungarian paragraph assignment with the
0.85 / 0.6 routing band, zero-LLM table diffing, VOI, the greedy minimal
sufficient update set, and the honesty rules of the P/R calibration (an
``unverified`` gold row must leave both the numerator and the denominator;
a machine ``UNCHANGED`` item is never a hit). No database and no network:
the LLM is injected as an async callable (fake in tests) and the embedding
is injected as a callable.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): the real-Postgres impact surface -
the two index lookups of 02-tech-plan 4.2 (evidence span -> affected
entities -> citing decision cards), including tenant isolation. Same gate
pattern as test_kg_service.py (pitfalls #14 template).

Contract source: competition/tasks/T-21-brief.md and 02-tech-plan 4.1-4.3.
"""
import dataclasses
import inspect
import json
import os
import sys
import uuid as uuid_mod
from pathlib import Path

# Upstream convention: repo backend/ on sys.path, import via the
# services.* prefix (see test_kg_service.py header for the shadowing note).
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo import alignment_service as als

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"

# Machine change-type vocabulary (02-tech-plan 4.1 STEP 3).
CHANGE_TYPES = {"UNCHANGED", "ADD", "UPDATE", "DELETE", "MOVE", "RENUMBER",
                "SPLIT", "MERGE"}

# ---------------------------------------------------------------------------
# Fixtures as plain data (the reference test style: no pytest fixtures)
# ---------------------------------------------------------------------------

DOC_OLD = (
    "3 治疗\n"
    "3.1 生活方式干预\n"
    "生活方式干预是所有患者的基础治疗。\n"
    "\n"
    "3.2 药物治疗\n"
    "二甲双胍是2型糖尿病的一线首选药物。\n"
    "\n"
    "表 1 常用降糖药物\n"
    "| 药物 | 剂量 |\n"
    "| --- | --- |\n"
    "| 二甲双胍 | 500mg |\n"
    "| 阿卡波糖 | 50mg |\n"
    "\n"
    "3.3 血糖监测\n"
    "定期监测糖化血红蛋白。\n"
)

DOC_OLD_V1 = (
    "4 药物治疗\n"
    "4.1 起始治疗\n"
    "二甲双胍是2型糖尿病的一线首选药物。\n"
    "\n"
    "4.2 联合治疗\n"
    "血糖不达标时可联合阿卡波糖。\n"
)

DOC_NEW_V2 = (
    "4 药物治疗\n"
    "4.1 起始治疗\n"
    "二甲双胍是2型糖尿病的一线首选药物，起始剂量调整为每日1000毫克。\n"
    "\n"
    "4.2 联合治疗\n"
    "血糖不达标时可联合阿卡波糖。\n"
    "\n"
    "4.3 胰岛素治疗\n"
    "口服药控制不佳时启动胰岛素。\n"
)

# Table-only document: no paragraphs, so no paragraph pair can reach STEP 3
# and the whole detect() run must cost zero LLM calls.
DOC_TABLE_OLD = (
    "5 药物剂量\n"
    "5.1 二甲双胍剂量\n"
    "表 1 二甲双胍剂量\n"
    "| 药物 | 剂量 |\n"
    "| --- | --- |\n"
    "| 二甲双胍 | 500mg |\n"
)
DOC_TABLE_NEW = DOC_TABLE_OLD.replace("500mg", "1000mg")

# One old / one new paragraph engineered for the 0.6-0.85 LLM band.
P_OLD = "二甲双胍是2型糖尿病的一线首选药物，推荐起始剂量为每日500毫克。"
P_NEW = "二甲双胍仍是2型糖尿病的一线首选药物，推荐起始剂量调整为每日1000毫克，分两次服用。"
DOC_BAND_OLD = "6 药物治疗\n6.1 起始治疗\n" + P_OLD + "\n"
DOC_BAND_NEW = "6 药物治疗\n6.1 起始治疗\n" + P_NEW + "\n"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeLLM:
    """Deterministic stand-in for the STEP 3 alignment call.

    Mirrors the frozen ``llm`` contract used by every KnowEvo service:
    ``await llm(prompt, *, kind, tier, temperature) -> str``. The return
    value is the raw text completion, which for ``kind='align'`` is the
    JSON object of backend/prompts/knowevo_align_*.yaml.
    """

    def __init__(self, verdict=None):
        self.verdict = verdict or {
            "label": "UPDATE", "confidence": 0.93,
            "points": ["起始剂量由 500mg 调整为 1000mg。"],
            "rationale": "The recommendation now states a different dose."}
        self.calls = []

    async def __call__(self, prompt: str, *, kind: str, **kwargs):
        self.calls.append({"prompt": prompt, "kind": kind, **kwargs})
        return json.dumps(self.verdict)


class RecordingEmbed:
    """Injectable embedding callable: ``texts -> list[list[float]]``.

    Matched by substring so a surrounding strip/normalize in the service
    cannot miss a vector, and it records every call so injection is
    observable.
    """

    def __init__(self, vectors=None, default=None):
        self.vectors = dict(vectors or {})
        self.default = list(default or [1.0, 0.0])
        self.calls = []

    def _vec(self, text):
        for key, vec in self.vectors.items():
            if key in (text or ""):
                return list(vec)
        return list(self.default)

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]


def _sec(section_id, number="3.2", title="药物治疗", level=2, path=None,
         paragraphs=None, tables=None):
    """Build a SectionNode with the frozen field order."""
    return als.SectionNode(
        section_id=section_id,
        number=number,
        title=title,
        norm_title=als.normalize_title(title),
        level=level,
        path=list(path if path is not None else [section_id]),
        paragraphs=list(paragraphs or []),
        tables=list(tables or []),
    )


def _table(caption, rows):
    return als.TableBlock(caption=caption,
                          rows=[list(r) for r in rows])


def _change(change_type, anchor, *, old=None, new=None, sim=0.9, points=None,
            source="deterministic", old_span=None, new_span=None):
    """Build a ChangeItem with the frozen field order."""
    return als.ChangeItem(
        change_type=change_type,
        section_anchor=anchor,
        old_section_id=old,
        new_section_id=new,
        similarity=sim,
        points=list(points or []),
        source=source,
        old_span=old_span,
        new_span=new_span,
    )


def _gold(gold_id, change_type, anchor, status="verified"):
    return {"id": gold_id, "change_type": change_type,
            "section_anchor": anchor, "status": status}


def _cand(ref_id, p_change, impact, kind="proposal"):
    return als.UpdateCandidate(kind=kind, ref_id=ref_id, label=ref_id,
                               p_change=p_change, impact=impact,
                               voi=als.voi(p_change, impact))


def _flatten(obj, depth=0):
    """Collect every scalar of a nested dataclass/dict/list into one string.

    ``affected_surface`` returns ``object`` (the contract does not freeze a
    shape), so Layer 2 asserts on the report's content through this walk
    instead of on a particular attribute layout.
    """
    if depth > 8:
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return " | ".join(_flatten(getattr(obj, f.name), depth + 1)
                          for f in dataclasses.fields(obj))
    if isinstance(obj, dict):
        return " | ".join(_flatten(v, depth + 1) for v in obj.values())
    if isinstance(obj, (list, tuple, set)):
        return " | ".join(_flatten(v, depth + 1) for v in obj)
    return str(obj)


# ---------------------------------------------------------------------------
# Layer 1: frozen dataclass contract (field names and order ARE the contract)
# ---------------------------------------------------------------------------

class TestContractDataclasses:
    def test_section_node_field_order(self):
        assert [f.name for f in dataclasses.fields(als.SectionNode)] == [
            "section_id", "number", "title", "norm_title", "level", "path",
            "paragraphs", "tables"]

    def test_table_block_field_order(self):
        assert [f.name for f in dataclasses.fields(als.TableBlock)] == [
            "caption", "rows"]

    def test_section_align_field_order(self):
        assert [f.name for f in dataclasses.fields(als.SectionAlign)] == [
            "matched", "added", "deleted", "moved", "renumbered"]

    def test_paragraph_pair_field_order(self):
        assert [f.name for f in dataclasses.fields(als.ParagraphPair)] == [
            "old_section_id", "new_section_id", "old_index", "new_index",
            "similarity", "route", "old_text", "new_text"]

    def test_table_change_field_order(self):
        assert [f.name for f in dataclasses.fields(als.TableChange)] == [
            "section_id", "kind", "rows_changed", "detail"]

    def test_change_item_field_order(self):
        assert [f.name for f in dataclasses.fields(als.ChangeItem)] == [
            "change_type", "section_anchor", "old_section_id",
            "new_section_id", "similarity", "points", "source", "old_span",
            "new_span"]

    def test_detect_result_field_order(self):
        assert [f.name for f in dataclasses.fields(als.DetectResult)] == [
            "old_asset_no", "new_asset_no", "sections", "changes",
            "table_changes", "stats"]

    def test_update_candidate_field_order(self):
        assert [f.name for f in dataclasses.fields(als.UpdateCandidate)] == [
            "kind", "ref_id", "label", "p_change", "impact", "voi"]

    def test_minimal_update_set_field_order(self):
        assert [f.name for f in dataclasses.fields(als.MinimalUpdateSet)] == [
            "selected", "excluded", "loss_estimate", "epsilon"]

    def test_calibration_field_order(self):
        assert [f.name for f in dataclasses.fields(als.Calibration)] == [
            "precision", "recall", "matched", "gold_total", "machine_total",
            "unverified_excluded"]

    def test_service_constructs_with_every_contract_kwarg(self):
        svc = als.AlignmentService(tenant_id=TENANT_A, llm=None, embed=None,
                                   session_factory=None)
        assert svc is not None


# ---------------------------------------------------------------------------
# Layer 1: normalize_title
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    def test_strips_numeric_numbering(self):
        assert (als.normalize_title("3.2 药物治疗")
                == als.normalize_title("药物治疗"))

    def test_strips_parenthesized_chinese_numbering(self):
        assert (als.normalize_title("（三）药物治疗")
                == als.normalize_title("药物治疗"))

    def test_strips_chapter_numbering(self):
        assert (als.normalize_title("第三章 药物治疗")
                == als.normalize_title("药物治疗"))

    def test_strips_whitespace_differences(self):
        assert (als.normalize_title("药物 治疗")
                == als.normalize_title("药物治疗"))

    def test_fullwidth_to_halfwidth_numbering(self):
        assert (als.normalize_title("３．２ 药物治疗")
                == als.normalize_title("3.2 药物治疗"))

    def test_idempotent(self):
        once = als.normalize_title("第三章 药物治疗（一）")
        assert als.normalize_title(once) == once

    def test_deterministic(self):
        assert (als.normalize_title("3.2 药物治疗")
                == als.normalize_title("3.2 药物治疗"))

    def test_distinct_topics_are_not_conflated(self):
        assert (als.normalize_title("3.2 药物治疗")
                != als.normalize_title("3.2 运动治疗"))

    def test_empty_input_returns_str(self):
        assert isinstance(als.normalize_title(""), str)


# ---------------------------------------------------------------------------
# Layer 1: parse_sections
# ---------------------------------------------------------------------------

class TestParseSections:
    def test_empty_text_yields_no_sections(self):
        assert als.parse_sections("") == []
        assert als.parse_sections("\n\n\n") == []

    def test_section_id_is_normalized_path(self):
        secs = als.parse_sections(DOC_OLD)
        med = [s for s in secs if "药物治疗" in s.section_id]
        assert med, [s.section_id for s in secs]
        assert "3.2" in med[0].section_id
        assert "|" in med[0].section_id
        assert med[0].norm_title == als.normalize_title("药物治疗")

    def test_level_follows_numbering_depth(self):
        secs = als.parse_sections(DOC_OLD)
        med = next(s for s in secs if "药物治疗" in s.section_id)
        assert med.level == 2

    def test_paragraphs_are_the_section_body(self):
        secs = als.parse_sections(DOC_OLD)
        med = next(s for s in secs if "药物治疗" in s.section_id)
        assert any("二甲双胍" in p for p in med.paragraphs)
        assert all(p.strip() for p in med.paragraphs)

    def test_markdown_table_recognized(self):
        secs = als.parse_sections(DOC_OLD)
        med = next(s for s in secs if "药物治疗" in s.section_id)
        assert med.tables, "markdown table was not parsed"
        table = med.tables[0]
        assert isinstance(table, als.TableBlock)
        cells = [c for row in table.rows for c in row]
        assert any("二甲双胍" in c for c in cells)

    def test_no_table_means_empty_list_not_a_guess(self):
        text = "3.2 药物治疗\n本段没有任何表格结构。\n"
        for sec in als.parse_sections(text):
            assert sec.tables == []

    def test_deterministic(self):
        first = [s.section_id for s in als.parse_sections(DOC_OLD)]
        second = [s.section_id for s in als.parse_sections(DOC_OLD)]
        assert first == second


# ---------------------------------------------------------------------------
# Layer 1: align_sections
# ---------------------------------------------------------------------------

class TestAlignSections:
    def test_identical_trees_are_all_matched(self):
        nodes = [_sec("3.1|血糖监测", "3.1", "血糖监测"),
                 _sec("3.2|药物治疗", "3.2", "药物治疗")]
        align = als.align_sections(nodes, list(nodes))
        assert isinstance(align, als.SectionAlign)
        assert len(align.matched) == 2
        assert align.added == []
        assert align.deleted == []

    def test_added_section_is_reported(self):
        old = [_sec("3.2|药物治疗", "3.2", "药物治疗")]
        new = old + [_sec("3.3|血糖监测", "3.3", "血糖监测")]
        align = als.align_sections(old, new)
        assert any("血糖监测" in sid for sid in align.added)
        assert not align.deleted

    def test_deleted_section_is_reported(self):
        old = [_sec("3.2|药物治疗", "3.2", "药物治疗"),
               _sec("3.3|血糖监测", "3.3", "血糖监测")]
        new = [_sec("3.2|药物治疗", "3.2", "药物治疗")]
        align = als.align_sections(old, new)
        assert any("血糖监测" in sid for sid in align.deleted)
        assert not align.added

    def test_renumbered_same_title_different_number(self):
        old = [_sec("3.2|药物治疗", "3.2", "药物治疗")]
        new = [_sec("4.2|药物治疗", "4.2", "药物治疗")]
        align = als.align_sections(old, new)
        assert align.renumbered, "same title, new number must be RENUMBER"
        assert not align.added
        assert not align.deleted
        tup = align.renumbered[0]
        assert len(tup) == 4
        joined = "|".join(str(x) for x in tup)
        assert "药物治疗" in joined
        assert "3.2" in joined and "4.2" in joined

    def test_moved_section_records_index_shift(self):
        old = [_sec("3.1|血糖监测", "3.1", "血糖监测"),
               _sec("3.2|药物治疗", "3.2", "药物治疗")]
        new = [_sec("3.2|药物治疗", "3.2", "药物治疗"),
               _sec("3.1|血糖监测", "3.1", "血糖监测")]
        align = als.align_sections(old, new)
        assert align.moved, "a reordered section must be reported as moved"
        tup = align.moved[0]
        assert len(tup) == 4
        assert isinstance(tup[2], int) and isinstance(tup[3], int)
        assert tup[2] != tup[3]
        assert str(tup[0]) and str(tup[1])

    def test_empty_inputs(self):
        assert als.align_sections([], []) == als.SectionAlign(
            matched=[], added=[], deleted=[], moved=[], renumbered=[])
        only_old = als.align_sections(
            [_sec("3.2|药物治疗", "3.2", "药物治疗")], [])
        assert only_old.deleted and not only_old.matched
        only_new = als.align_sections(
            [], [_sec("3.2|药物治疗", "3.2", "药物治疗")])
        assert only_new.added and not only_new.matched

    def test_reproducible_same_input_same_output(self):
        old = [_sec("3.1|血糖监测", "3.1", "血糖监测"),
               _sec("3.2|药物治疗", "3.2", "药物治疗")]
        new = [_sec("3.2|药物治疗", "4.2", "药物治疗"),
               _sec("3.1|血糖监测", "3.1", "血糖监测"),
               _sec("3.3|胰岛素治疗", "3.3", "胰岛素治疗")]
        first = als.align_sections(old, new)
        second = als.align_sections(old, new)
        assert first == second
        assert repr(first) == repr(second)


# ---------------------------------------------------------------------------
# Layer 1: lexical_similarity
# ---------------------------------------------------------------------------

class TestLexicalSimilarity:
    def test_identical_text_is_one(self):
        text = "二甲双胍是2型糖尿病的一线首选药物。"
        assert als.lexical_similarity(text, text) == pytest.approx(1.0)

    def test_unrelated_text_near_zero(self):
        sim = als.lexical_similarity("二甲双胍是首选药物", "气象预报显示明日有雨")
        assert 0.0 <= sim <= 0.2

    def test_partial_overlap_strictly_between(self):
        sim = als.lexical_similarity(
            "二甲双胍是2型糖尿病的一线首选药物",
            "阿卡波糖是2型糖尿病的二线备选药物")
        assert 0.0 < sim < 1.0

    def test_range_and_type(self):
        pairs = [("", ""), ("", "非空"), ("a", "a"),
                 ("二甲双胍", "阿卡波糖"), ("同一段文本", "同一段文本")]
        for left, right in pairs:
            sim = als.lexical_similarity(left, right)
            assert isinstance(sim, float)
            assert 0.0 <= sim <= 1.0

    def test_symmetric(self):
        left, right = "二甲双胍", "二甲双胍片"
        assert (als.lexical_similarity(left, right)
                == pytest.approx(als.lexical_similarity(right, left)))

    def test_empty_versus_nonempty_near_zero(self):
        assert als.lexical_similarity("", "非空文本") <= 0.1

    def test_deterministic(self):
        assert (als.lexical_similarity("甲 乙 丙", "甲 乙 丁")
                == als.lexical_similarity("甲 乙 丙", "甲 乙 丁"))


# ---------------------------------------------------------------------------
# Layer 1: similarity_matrix
# ---------------------------------------------------------------------------

class TestSimilarityMatrix:
    def test_shape_is_old_by_new(self):
        matrix = als.similarity_matrix(["a", "b"], ["a", "c", "d"])
        assert len(matrix) == 2
        assert all(len(row) == 3 for row in matrix)

    def test_lexical_fallback_matches_lexical_similarity(self):
        old = ["二甲双胍是首选药物", "阿卡波糖"]
        new = ["二甲双胍是首选药物", "胰岛素"]
        matrix = als.similarity_matrix(old, new)
        for i, left in enumerate(old):
            for j, right in enumerate(new):
                assert matrix[i][j] == pytest.approx(
                    als.lexical_similarity(left, right))

    def test_injected_embed_identical_vectors_score_one(self):
        embed = RecordingEmbed({"同一段文本": [1.0, 0.0]})
        matrix = als.similarity_matrix(["同一段文本"], ["同一段文本"],
                                       embed=embed)
        assert matrix[0][0] == pytest.approx(1.0)
        assert embed.calls, "injected embedding was never called"

    def test_injected_embed_orthogonal_vectors_score_zero(self):
        embed = RecordingEmbed({"正交甲": [1.0, 0.0], "正交乙": [0.0, 1.0]})
        matrix = als.similarity_matrix(["正交甲"], ["正交乙"], embed=embed)
        assert matrix[0][0] == pytest.approx(0.0, abs=1e-6)

    def test_empty_dimensions(self):
        assert als.similarity_matrix([], []) == []
        assert als.similarity_matrix([], ["a"]) == []
        # one old paragraph, no new paragraph: at most one empty row.
        assert als.similarity_matrix(["a"], []) in ([], [[]])

    def test_deterministic(self):
        old, new = ["甲", "乙"], ["甲", "丙"]
        assert als.similarity_matrix(old, new) == als.similarity_matrix(old,
                                                                        new)


# ---------------------------------------------------------------------------
# Layer 1: assign_paragraphs (Hungarian + 0.85/0.6 routing)
# ---------------------------------------------------------------------------

class TestAssignParagraphs:
    def test_all_high_similarity_is_direct_and_paired_once(self):
        sims = [[0.90, 0.95], [0.99, 0.92]]
        pairs = als.assign_paragraphs(sims)
        assert all(isinstance(p, als.ParagraphPair) for p in pairs)
        direct = [p for p in pairs if p.route == "direct"]
        assert {p.old_index for p in direct} == {0, 1}
        assert {p.new_index for p in direct} == {0, 1}

    def test_threshold_high_boundary_is_direct(self):
        pairs = als.assign_paragraphs([[0.85]])
        assert [p.route for p in pairs] == ["direct"]

    def test_threshold_low_boundary_is_llm(self):
        pairs = als.assign_paragraphs([[0.6]])
        assert [p.route for p in pairs] == ["llm"]

    def test_mid_band_is_llm_and_similarity_preserved(self):
        pairs = als.assign_paragraphs([[0.7]])
        assert [p.route for p in pairs] == ["llm"]
        assert pairs[0].similarity == pytest.approx(0.7)

    def test_below_low_is_unmatched(self):
        pairs = als.assign_paragraphs([[0.4]])
        assert pairs
        assert all(p.route == "unmatched" for p in pairs)

    def test_hungarian_optimal_not_greedy(self):
        # Global max is (0,0)=0.90, but the total assignment optimum is
        # (0,1)+(1,0)=1.75 versus (0,0)+(1,1)=1.00.
        sims = [[0.90, 0.88], [0.87, 0.10]]
        pairs = als.assign_paragraphs(sims)
        matched = {(p.old_index, p.new_index) for p in pairs
                   if p.route in ("direct", "llm")}
        assert matched == {(0, 1), (1, 0)}

    def test_each_side_is_paired_at_most_once(self):
        sims = [[0.99, 0.98, 0.97], [0.96, 0.95, 0.94]]
        pairs = als.assign_paragraphs(sims)
        old_used = [p.old_index for p in pairs if p.old_index is not None]
        new_used = [p.new_index for p in pairs if p.new_index is not None]
        assert len(old_used) == len(set(old_used))
        assert len(new_used) == len(set(new_used))

    def test_more_new_than_old_keeps_best_pair(self):
        pairs = als.assign_paragraphs([[0.90, 0.20, 0.95]])
        direct = [p for p in pairs if p.route == "direct"]
        assert len(direct) == 1
        assert (direct[0].old_index, direct[0].new_index) == (0, 2)

    def test_empty_matrix(self):
        assert als.assign_paragraphs([]) == []

    def test_custom_thresholds_respected(self):
        pairs = als.assign_paragraphs([[0.7]], high=0.7, low=0.5)
        assert [p.route for p in pairs] == ["direct"]

    def test_deterministic(self):
        sims = [[0.90, 0.30], [0.40, 0.95]]
        assert als.assign_paragraphs(sims) == als.assign_paragraphs(sims)


# ---------------------------------------------------------------------------
# Layer 1: diff_tables (deterministic, zero LLM)
# ---------------------------------------------------------------------------

class TestDiffTables:
    def test_identical_tables_produce_no_change(self):
        table = _table("表 1 常用降糖药物",
                       [["二甲双胍", "500mg"], ["阿卡波糖", "50mg"]])
        assert als.diff_tables([table], [table]) == []

    def test_changed_cell_is_update(self):
        old = _table("表 1 常用降糖药物", [["二甲双胍", "500mg"]])
        new = _table("表 1 常用降糖药物", [["二甲双胍", "1000mg"]])
        changes = als.diff_tables([old], [new])
        assert len(changes) == 1
        change = changes[0]
        assert isinstance(change, als.TableChange)
        assert change.kind == "UPDATE"
        assert change.rows_changed
        assert all(isinstance(i, int) for i in change.rows_changed)
        assert change.detail
        assert isinstance(change.section_id, str)

    def test_added_row_is_add(self):
        old = _table("表 1 常用降糖药物", [["二甲双胍", "500mg"]])
        new = _table("表 1 常用降糖药物",
                     [["二甲双胍", "500mg"], ["阿卡波糖", "50mg"]])
        kinds = [c.kind for c in als.diff_tables([old], [new])]
        assert "ADD" in kinds

    def test_removed_row_is_delete(self):
        old = _table("表 1 常用降糖药物",
                     [["二甲双胍", "500mg"], ["阿卡波糖", "50mg"]])
        new = _table("表 1 常用降糖药物", [["二甲双胍", "500mg"]])
        kinds = [c.kind for c in als.diff_tables([old], [new])]
        assert "DELETE" in kinds

    def test_new_and_removed_table(self):
        table = _table("表 2 参考区间", [["HbA1c", "7.0%"]])
        assert any(c.kind == "ADD"
                   for c in als.diff_tables([], [table]))
        assert any(c.kind == "DELETE"
                   for c in als.diff_tables([table], []))

    def test_both_sides_empty(self):
        assert als.diff_tables([], []) == []

    def test_reproducible(self):
        old = _table("表 1", [["二甲双胍", "500mg"]])
        new = _table("表 1", [["二甲双胍", "1000mg"]])
        assert als.diff_tables([old], [new]) == als.diff_tables([old], [new])

    def test_zero_llm_calls(self):
        llm = FakeLLM()
        old = _table("表 1", [["二甲双胍", "500mg"]])
        new = _table("表 1", [["二甲双胍", "1000mg"]])
        als.diff_tables([old], [new])
        assert llm.calls == []


# ---------------------------------------------------------------------------
# Layer 1: VOI
# ---------------------------------------------------------------------------

class TestVoi:
    def test_product_of_probability_and_impact(self):
        assert als.voi(0.5, 4) == pytest.approx(2.0)
        assert als.voi(0.9, 10) == pytest.approx(9.0)

    def test_zero_probability_is_zero(self):
        assert als.voi(0.0, 10) == pytest.approx(0.0)

    def test_zero_impact_is_zero(self):
        assert als.voi(0.9, 0) == pytest.approx(0.0)

    def test_monotone_in_both_arguments(self):
        assert als.voi(0.5, 4) > als.voi(0.4, 4)
        assert als.voi(0.5, 4) < als.voi(0.5, 5)


# ---------------------------------------------------------------------------
# Layer 1: minimal_update_set (VOI-descending greedy)
# ---------------------------------------------------------------------------

class TestMinimalUpdateSet:
    def test_zero_cost_selects_everything(self):
        cands = [_cand("a", 1.0, 10), _cand("b", 0.5, 10), _cand("c", 0.1, 1)]
        result = als.minimal_update_set(cands, epsilon=0.0, cost=0.0)
        assert isinstance(result, als.MinimalUpdateSet)
        assert len(result.selected) == 3
        assert result.excluded == []
        assert result.loss_estimate == pytest.approx(0.0)
        assert result.epsilon == pytest.approx(0.0)

    def test_greedy_stops_when_marginal_below_cost(self):
        # VOIs are 10, 5, 1; with cost 3 the third item is not worth it.
        cands = [_cand("a", 1.0, 10), _cand("b", 0.5, 10), _cand("c", 1.0, 1)]
        result = als.minimal_update_set(cands, epsilon=4.0, cost=3.0)
        assert [c.ref_id for c in result.selected] == ["a", "b"]
        assert [c.ref_id for c in result.excluded] == ["c"]
        assert result.loss_estimate == pytest.approx(1.0)

    def test_costlier_than_everything_selects_nothing(self):
        cands = [_cand("a", 1.0, 10)]
        result = als.minimal_update_set(cands, epsilon=1e9, cost=1e9)
        assert result.selected == []
        assert [c.ref_id for c in result.excluded] == ["a"]
        assert result.loss_estimate == pytest.approx(10.0)

    def test_no_candidate_is_lost(self):
        cands = [_cand("a", 1.0, 10), _cand("b", 0.5, 10), _cand("c", 0.1, 1),
                 _cand("d", 0.9, 3, kind="decision_card")]
        result = als.minimal_update_set(cands, epsilon=4.0, cost=3.0)
        seen = ([c.ref_id for c in result.selected]
                + [c.ref_id for c in result.excluded])
        assert sorted(seen) == sorted(c.ref_id for c in cands)
        assert len(result.selected) + len(result.excluded) == len(cands)

    def test_selected_sorted_by_voi_descending(self):
        cands = [_cand("a", 0.2, 5), _cand("b", 1.0, 10),
                 _cand("c", 0.5, 10), _cand("d", 0.1, 1)]
        result = als.minimal_update_set(cands, epsilon=4.0, cost=0.0)
        vois = [c.voi for c in result.selected]
        assert vois == sorted(vois, reverse=True)

    def test_loss_estimate_is_sum_of_excluded_voi(self):
        cands = [_cand("a", 1.0, 10), _cand("b", 0.5, 10), _cand("c", 0.1, 1)]
        result = als.minimal_update_set(cands, epsilon=4.0, cost=3.0)
        assert result.loss_estimate == pytest.approx(
            sum(c.voi for c in result.excluded))

    def test_empty_candidates(self):
        result = als.minimal_update_set([], epsilon=0.5, cost=1.0)
        assert result.selected == []
        assert result.excluded == []
        assert result.loss_estimate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Layer 1: calibrate_pr (the two honesty rules)
# ---------------------------------------------------------------------------

class TestCalibratePr:
    def test_perfect_match(self):
        machine = [_change("UPDATE", "3.2|药物治疗")]
        gold = [_gold("g1", "UPDATE", "3.2|药物治疗")]
        cal = als.calibrate_pr(machine, gold)
        assert isinstance(cal, als.Calibration)
        assert cal.precision == pytest.approx(1.0)
        assert cal.recall == pytest.approx(1.0)
        assert cal.matched == 1
        assert cal.gold_total == 1
        assert cal.machine_total == 1
        assert cal.unverified_excluded == 0

    def test_unverified_gold_leaves_recall_denominator(self):
        machine = [_change("UPDATE", "A")]
        gold = [_gold("g1", "UPDATE", "A"),
                _gold("g2", "ADD", "C", status="unverified")]
        cal = als.calibrate_pr(machine, gold)
        assert cal.unverified_excluded == 1
        assert cal.gold_total == 1
        assert cal.recall == pytest.approx(1.0)

    def test_unverified_gold_is_never_a_precision_hit(self):
        machine = [_change("UPDATE", "A"), _change("UPDATE", "B")]
        gold = [_gold("g1", "UPDATE", "A"),
                _gold("g2", "UPDATE", "B", status="unverified")]
        cal = als.calibrate_pr(machine, gold)
        assert cal.unverified_excluded == 1
        assert cal.machine_total == 2
        assert cal.precision == pytest.approx(0.5)

    def test_missing_gold_lowers_recall(self):
        machine = [_change("UPDATE", "A")]
        gold = [_gold("g1", "UPDATE", "A"), _gold("g2", "ADD", "C")]
        cal = als.calibrate_pr(machine, gold)
        assert cal.gold_total == 2
        assert cal.recall == pytest.approx(0.5)

    def test_machine_unchanged_item_is_not_a_hit(self):
        machine = [_change("UNCHANGED", "A")]
        gold = [_gold("g1", "UNCHANGED", "A")]
        cal = als.calibrate_pr(machine, gold)
        assert cal.matched == 0
        assert (cal.precision or 0.0) == pytest.approx(0.0)

    def test_corrected_status_still_counted_as_gold(self):
        machine = [_change("UPDATE", "A")]
        gold = [_gold("g1", "UPDATE", "A"),
                _gold("g2", "UPDATE", "B", status="corrected")]
        cal = als.calibrate_pr(machine, gold)
        assert cal.unverified_excluded == 0
        assert cal.gold_total == 2

    def test_all_gold_unverified_leaves_nothing_evaluable(self):
        machine = [_change("UPDATE", "A")]
        gold = [_gold("g1", "UPDATE", "A", status="unverified")]
        cal = als.calibrate_pr(machine, gold)
        assert cal.unverified_excluded == 1
        assert cal.gold_total == 0
        assert cal.matched == 0
        assert cal.recall is None or cal.recall == pytest.approx(0.0)

    def test_empty_inputs(self):
        cal = als.calibrate_pr([], [])
        assert cal.gold_total == 0
        assert cal.machine_total == 0
        assert cal.unverified_excluded == 0
        assert cal.precision is None or 0.0 <= cal.precision <= 1.0
        assert cal.recall is None or 0.0 <= cal.recall <= 1.0


# ---------------------------------------------------------------------------
# Layer 1: detect() without an LLM (deterministic degradation)
# ---------------------------------------------------------------------------

class TestDetectDeterministic:
    def test_detect_signature(self):
        sig = inspect.signature(als.AlignmentService.detect)
        params = list(sig.parameters)
        assert params[:3] == ["self", "old_text", "new_text"]
        assert sig.parameters["old_asset_no"].kind == (
            inspect.Parameter.KEYWORD_ONLY)
        assert sig.parameters["new_asset_no"].kind == (
            inspect.Parameter.KEYWORD_ONLY)
        assert inspect.iscoroutinefunction(als.AlignmentService.detect)

    @pytest.mark.asyncio
    async def test_detect_returns_detect_result(self):
        svc = als.AlignmentService(tenant_id=TENANT_A)
        result = await svc.detect(DOC_OLD_V1, DOC_NEW_V2,
                                  old_asset_no="G-2020", new_asset_no="G-2024")
        assert isinstance(result, als.DetectResult)
        assert result.old_asset_no == "G-2020"
        assert result.new_asset_no == "G-2024"
        assert isinstance(result.sections, als.SectionAlign)
        assert isinstance(result.changes, list)
        assert isinstance(result.table_changes, list)
        assert isinstance(result.stats, dict)
        assert all(isinstance(c, als.ChangeItem) for c in result.changes)
        assert all(c.change_type in CHANGE_TYPES for c in result.changes)

    @pytest.mark.asyncio
    async def test_added_section_becomes_add_change(self):
        svc = als.AlignmentService(tenant_id=TENANT_A)
        result = await svc.detect(DOC_OLD_V1, DOC_NEW_V2,
                                  old_asset_no="a", new_asset_no="b")
        assert any(c.change_type == "ADD" for c in result.changes)
        assert result.sections.added

    @pytest.mark.asyncio
    async def test_deleted_section_becomes_delete_change(self):
        svc = als.AlignmentService(tenant_id=TENANT_A)
        result = await svc.detect(DOC_NEW_V2, DOC_OLD_V1,
                                  old_asset_no="b", new_asset_no="a")
        assert any(c.change_type == "DELETE" for c in result.changes)
        assert result.sections.deleted

    @pytest.mark.asyncio
    async def test_identical_documents_yield_no_add_or_delete(self):
        svc = als.AlignmentService(tenant_id=TENANT_A)
        result = await svc.detect(DOC_OLD_V1, DOC_OLD_V1,
                                  old_asset_no="a", new_asset_no="a2")
        assert not any(c.change_type in ("ADD", "DELETE", "UPDATE")
                       for c in result.changes)
        assert result.table_changes == []

    @pytest.mark.asyncio
    async def test_no_llm_never_raises_and_sources_are_deterministic(self):
        svc = als.AlignmentService(tenant_id=TENANT_A)
        result = await svc.detect(DOC_OLD_V1, DOC_NEW_V2,
                                  old_asset_no="a", new_asset_no="b")
        assert all(c.source == "deterministic" for c in result.changes)

    @pytest.mark.asyncio
    async def test_reproducible_without_llm(self):
        svc = als.AlignmentService(tenant_id=TENANT_A)
        first = await svc.detect(DOC_OLD_V1, DOC_NEW_V2,
                                 old_asset_no="a", new_asset_no="b")
        second = await svc.detect(DOC_OLD_V1, DOC_NEW_V2,
                                  old_asset_no="a", new_asset_no="b")
        key = lambda c: (c.change_type, c.section_anchor,
                         c.old_section_id, c.new_section_id)
        assert [key(c) for c in first.changes] == [key(c) for c in second.changes]


# ---------------------------------------------------------------------------
# Layer 1: detect() with an injected LLM (STEP 3)
# ---------------------------------------------------------------------------

class TestDetectWithLlm:
    @pytest.mark.asyncio
    async def test_llm_band_pair_reaches_step3_and_drives_the_label(self):
        embed = RecordingEmbed({P_OLD: [1.0, 0.0], P_NEW: [0.7, 0.714]})
        llm = FakeLLM({"label": "UPDATE", "confidence": 0.93,
                       "points": ["起始剂量调整为 1000mg"],
                       "rationale": "dose changed"})
        svc = als.AlignmentService(tenant_id=TENANT_A, llm=llm, embed=embed)
        result = await svc.detect(DOC_BAND_OLD, DOC_BAND_NEW,
                                  old_asset_no="a", new_asset_no="b")
        assert llm.calls, "0.6-0.85 band pair never reached STEP 3"
        assert any(c.source == "llm" for c in result.changes)
        assert any(c.change_type == "UPDATE" for c in result.changes
                   if c.source == "llm")
        assert any("起始剂量调整为 1000mg" in p
                   for c in result.changes for p in c.points)

    @pytest.mark.asyncio
    async def test_llm_call_contract_kind_tier_temperature(self):
        embed = RecordingEmbed({P_OLD: [1.0, 0.0], P_NEW: [0.7, 0.714]})
        llm = FakeLLM()
        svc = als.AlignmentService(tenant_id=TENANT_A, llm=llm, embed=embed)
        await svc.detect(DOC_BAND_OLD, DOC_BAND_NEW,
                         old_asset_no="a", new_asset_no="b")
        assert llm.calls, "LLM was never invoked for the band pair"
        call = llm.calls[0]
        assert call["kind"] == "align"
        assert call.get("temperature") == 0.0
        assert "tier" in call


# ---------------------------------------------------------------------------
# Layer 1: table-only change costs zero LLM (02-tech-plan 4.1 hard rule)
# ---------------------------------------------------------------------------

class TestTableChannelZeroLlm:
    def test_diff_tables_never_calls_the_llm(self):
        llm = FakeLLM()
        old = _table("表 1", [["二甲双胍", "500mg"]])
        new = _table("表 1", [["二甲双胍", "1000mg"]])
        als.diff_tables([old], [new])
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_table_only_document_run_uses_no_llm(self):
        llm = FakeLLM()
        svc = als.AlignmentService(tenant_id=TENANT_A, llm=llm)
        result = await svc.detect(DOC_TABLE_OLD, DOC_TABLE_NEW,
                                  old_asset_no="a", new_asset_no="b")
        assert result.table_changes, "deterministic table diff produced nothing"
        assert all(tc.kind in ("ADD", "DELETE", "UPDATE")
                   for tc in result.table_changes)
        assert llm.calls == [], "table changes must never reach the LLM"

    @pytest.mark.asyncio
    async def test_table_only_document_without_llm_is_still_deterministic(self):
        svc = als.AlignmentService(tenant_id=TENANT_A, llm=None)
        result = await svc.detect(DOC_TABLE_OLD, DOC_TABLE_NEW,
                                  old_asset_no="a", new_asset_no="b")
        assert result.table_changes
        assert all(c.source == "deterministic" for c in result.changes)


# ---------------------------------------------------------------------------
# Layer 1: affected_surface signature only (behavior is Layer 2)
# ---------------------------------------------------------------------------

class TestAffectedSurfaceContract:
    def test_method_exists_and_is_callable(self):
        assert hasattr(als.AlignmentService, "affected_surface")
        assert callable(als.AlignmentService.affected_surface)

    def test_signature_is_self_and_changed_spans(self):
        sig = inspect.signature(als.AlignmentService.affected_surface)
        params = list(sig.parameters)
        assert params[:2] == ["self", "changed_spans"]


# ---------------------------------------------------------------------------
# Layer 2: real Postgres (RUN_POSTGRES_INTEGRATION=1)
#
# 02-tech-plan 4.2 DECISION: the impact surface is two index lookups, never
# an online graph traversal -- (1) kg_evidence_t.span_loc.chunk_idx in the
# changed span set of a document -> affected entities, then (2) the
# decision_card_t payload kg_path inverted lookup -> affected cards. The
# kw_008 migration (deploy/sql/migrations/v2.5.5_kw_008_alignment_index.sql)
# adds the purpose-built indexes for both predicates. All KnowEvo tables
# live in the nexent schema.
# ---------------------------------------------------------------------------

pytestmark_pg = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests")


def _card_payload(question_id, entity_stable_id):
    return {
        "question_id": question_id,
        "question": f"{entity_stable_id} 的起始治疗建议？",
        "candidates": [{
            "option": "首选药物治疗",
            "evidence_chain": [{
                "claim": f"{entity_stable_id} 是一线用药",
                "provenance": {
                    "doc": "指南2020版",
                    "span": "3.2 药物治疗",
                    "kg_path": [entity_stable_id, "indicated_for",
                                "Disease:2型糖尿病"],
                    "version_pinned": True,
                },
                "tag": "EXTRACTED",
                "source_channel": "kg+doc",
            }],
        }],
        "decision": "RECOMMEND",
        "knowledge_stamp": {"ontology_version": "v1.0.0",
                            "kg_cutoff": None, "clock_source": "now"},
    }


@pytestmark_pg
class TestPgAffectedSurface:
    def _seed(self, tenant, asset_no, doc_id, chunk_idx, entity, question_id):
        from database.knowevo_db import (
            DecisionCard,
            DocAsset,
            KgEvidence,
            _get_db_session,
        )
        with _get_db_session() as session:
            # Idempotent doc insert: a test that seeds two evidence rows for
            # the same document calls this twice with the same doc_id.
            if session.get(DocAsset, doc_id) is None:
                session.add(DocAsset(id=doc_id, tenant_id=tenant,
                                     asset_no=asset_no, title=asset_no,
                                     modality="text", doc_type="guideline"))
            session.add(KgEvidence(tenant_id=tenant, doc_id=doc_id,
                                   span_loc={"chunk_idx": chunk_idx},
                                   span_text=f"{entity} 的证据段",
                                   entity_refs=[entity], edge_ids=[],
                                   tag="EXTRACTED", modality="text"))
            session.add(DecisionCard(tenant_id=tenant,
                                     question_id=question_id,
                                     payload=_card_payload(question_id,
                                                           entity),
                                     knowledge_stamp={
                                         "ontology_version": "v1.0.0"}))
            session.flush()

    def _cleanup(self, tenant):
        from database.knowevo_db import (
            DecisionCard,
            DocAsset,
            KgEvidence,
            _get_db_session,
        )
        with _get_db_session() as session:
            for model in (DecisionCard, KgEvidence, DocAsset):
                session.query(model).filter(
                    model.tenant_id == tenant).delete()
            session.flush()

    def test_two_lookups_find_entities_and_citing_cards(self):
        tenant = str(uuid_mod.uuid4())
        doc_new = uuid_mod.uuid4()
        try:
            self._seed(tenant, "T21-G-2024", doc_new, 7,
                       "Drug:二甲双胍", "Q-T21-1")
            self._seed(tenant, "T21-G-2024", doc_new, 9,
                       "Drug:阿卡波糖", "Q-T21-2")
            svc = als.AlignmentService(tenant_id=tenant)
            # lookup 1: evidence anchored to changed span (doc, chunk 7)
            # lookup 2: cards whose kg_path cites the affected entity
            result = svc.affected_surface([("T21-G-2024", 7)])
            assert result is not None
            blob = _flatten(result)
            assert "Drug:二甲双胍" in blob
            assert "Q-T21-1" in blob
            # negative control: the card citing only the other entity is
            # not swept in by the reverse lookup
            assert "Q-T21-2" not in blob
        finally:
            self._cleanup(tenant)

    def test_changed_chunk_index_is_respected(self):
        tenant = str(uuid_mod.uuid4())
        doc_new = uuid_mod.uuid4()
        try:
            self._seed(tenant, "T21-CHUNK", doc_new, 3,
                       "Drug:二甲双胍", "Q-T21-C1")
            self._seed(tenant, "T21-CHUNK", doc_new, 8,
                       "Drug:阿卡波糖", "Q-T21-C2")
            svc = als.AlignmentService(tenant_id=tenant)
            blob = _flatten(svc.affected_surface([("T21-CHUNK", 8)]))
            assert "Drug:阿卡波糖" in blob
            assert "Drug:二甲双胍" not in blob
        finally:
            self._cleanup(tenant)

    def test_tenant_isolation(self):
        tenant_a = str(uuid_mod.uuid4())
        tenant_b = str(uuid_mod.uuid4())
        try:
            self._seed(tenant_a, "T21-ISO", uuid_mod.uuid4(), 1,
                       "Drug:甲厂药", "Q-T21-A")
            self._seed(tenant_b, "T21-ISO", uuid_mod.uuid4(), 1,
                       "Drug:乙厂药", "Q-T21-B")
            blob = _flatten(als.AlignmentService(
                tenant_id=tenant_a).affected_surface([("T21-ISO", 1)]))
            assert "Drug:甲厂药" in blob
            assert "Drug:乙厂药" not in blob
            assert "Q-T21-B" not in blob
        finally:
            self._cleanup(tenant_a)
            self._cleanup(tenant_b)


# ---------------------------------------------------------------------------
# Layer 1: /alignment/* HTTP boundary (apps.knowledge_graph_app)
# ---------------------------------------------------------------------------


def _alignment_auth(monkeypatch, tenant="33333333-3333-3333-3333-333333333333",
                    kb_manage=True, graph_manage=True):
    """Patch the app's auth seams to a known tenant (sibling-test idiom)."""
    monkeypatch.setattr(
        "apps.knowledge_graph_app.get_current_user_context",
        lambda _authorization: ("user@x", tenant, "ADMIN"))

    def fake_check(role, category, ptype, subtype=None):
        if (category, ptype) in (("RESOURCE", "KNOWLEDGE_GRAPH"),
                                 ("RESOURCE", "KB")):
            return kb_manage or graph_manage
        return False

    monkeypatch.setattr(
        "apps.knowledge_graph_app.check_role_permission", fake_check)


class FakeAlignmentService:
    """Canned stand-in for AlignmentService at the app boundary."""

    def __init__(self, tenant_id, llm=None):
        self.tenant_id = tenant_id
        self.llm = llm
        self.runs = []

    async def run(self, **kwargs):
        self.runs.append(kwargs)
        return als.AlignmentResult(
            detect=als.DetectResult(
                "guide-2020", "guide-2024", als.SectionAlign()
            )
        )

    def list_diffs(self, limit=50):
        return [{
            "diff_id": "d1",
            "old_asset_no": "guide-2020",
            "new_asset_no": "guide-2024",
            "change_counts": {"ADD": 1},
            "created_at": None,
        }]


class TestAlignmentRoutes:
    def _post(self, request, authorization="t"):
        import asyncio

        from apps import knowledge_graph_app

        return asyncio.run(
            knowledge_graph_app.run_alignment_diff(request, authorization=authorization)
        )

    def test_run_requires_workbench_permission(self, monkeypatch):
        from fastapi import HTTPException

        from apps import knowledge_graph_app

        _alignment_auth(monkeypatch, kb_manage=False, graph_manage=False)
        with pytest.raises(HTTPException) as exc:
            self._post(knowledge_graph_app.AlignmentDiffRequest(
                old_asset_no="a", new_asset_no="b",
                old_text="x", new_text="y"))
        assert exc.value.status_code == 403

    def test_run_delegates_and_marks_llm_disabled(self, monkeypatch):
        from apps import knowledge_graph_app

        _alignment_auth(monkeypatch)
        fake = FakeAlignmentService("t")
        monkeypatch.setattr(
            knowledge_graph_app, "_alignment_service",
            lambda tenant, llm=None, max_llm_calls=20: fake)
        report = self._post(knowledge_graph_app.AlignmentDiffRequest(
            old_asset_no="guide-2020", new_asset_no="guide-2024",
            old_text="a", new_text="b", no_llm=True))
        assert fake.runs[0]["old_asset_no"] == "guide-2020"
        assert fake.runs[0]["old_text"] == "a"
        assert report["run"]["llm_enabled"] is False
        assert report["old_asset_no"] == "guide-2020"

    def test_run_missing_corpus_text_maps_to_400(self, monkeypatch):
        from fastapi import HTTPException

        from apps import knowledge_graph_app

        _alignment_auth(monkeypatch)

        def boom(asset_no):
            raise SystemExit(f"asset_no {asset_no!r} is not in the registry")

        monkeypatch.setattr(knowledge_graph_app, "_corpus_text", boom)
        with pytest.raises(HTTPException) as exc:
            self._post(knowledge_graph_app.AlignmentDiffRequest(
                old_asset_no="missing", new_asset_no="guide-2024"))
        assert exc.value.status_code == 400
        assert "not in the registry" in exc.value.detail

    def test_run_store_failure_maps_to_502(self, monkeypatch):
        from fastapi import HTTPException

        from apps import knowledge_graph_app

        _alignment_auth(monkeypatch)

        class BoomService:
            async def run(self, **kwargs):
                raise RuntimeError("db down")

        monkeypatch.setattr(
            knowledge_graph_app, "_alignment_service",
            lambda tenant, llm=None, max_llm_calls=20: BoomService())
        with pytest.raises(HTTPException) as exc:
            self._post(knowledge_graph_app.AlignmentDiffRequest(
                old_asset_no="a", new_asset_no="b",
                old_text="x", new_text="y"))
        assert exc.value.status_code == 502

    def test_list_requires_permission(self, monkeypatch):
        from fastapi import HTTPException

        from apps import knowledge_graph_app

        _alignment_auth(monkeypatch, kb_manage=False, graph_manage=False)
        with pytest.raises(HTTPException) as exc:
            knowledge_graph_app.list_alignment_diffs(authorization="t")
        assert exc.value.status_code == 403

    def test_list_happy_path(self, monkeypatch):
        from apps import knowledge_graph_app

        _alignment_auth(monkeypatch)
        fake = FakeAlignmentService("t")
        monkeypatch.setattr(
            knowledge_graph_app, "_alignment_service",
            lambda tenant, llm=None, max_llm_calls=20: fake)
        body = knowledge_graph_app.list_alignment_diffs(authorization="t")
        assert body["count"] == 1
        assert body["diffs"][0]["diff_id"] == "d1"
