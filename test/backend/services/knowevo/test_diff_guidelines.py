"""
Unit tests for services/knowevo/pipeline/diff_guidelines.py: gold-seed
parsing and the UNC in-domain-negative caliber.

Caliber under test (2026-09-27 user ruling, mirrored in
competition/experiments/probe_p5_alignment_caliber.py): a gold row typed
``UNC`` (unchanged topic, batch2 C-rows) is an **in-domain negative** - kept
as ``status="negative"`` / ``change_type=None`` instead of being silently
dropped, excluded from the recall denominator by the verified/corrected
verdict filter, and counted on the false-positive side of the precision
axis (a machine change reported on an unchanged topic is a false positive).

Layer 1 only: pure functions, no DB / no network / no LLM.
Reference: competition/corpus/guideline_diff_seed_batch2.md header P/R rule
and competition/corpus/guideline_diff_seed_merged.md caliber note.
"""
import sys
from pathlib import Path

# Upstream convention: repo backend/ on sys.path, import via the
# services.* prefix (see test_kg_service.py header for the shadowing note).
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from services.knowevo import alignment_service as als
from services.knowevo.pipeline import diff_guidelines as cli

GOLD_MD = """\
| # | 领域 | 类型 | 章节锚点 | 核验结论 |
|---|------|-----|----------|----------|
| 1 | 胰岛素泵 | ADD | 胰岛素泵治疗 | verified |
| 2 | 代谢手术 | UPD | 代谢手术 | unverified |
| C-01 | 病因分型 | UNC | 糖尿病的诊断与分型 | verified |
| C-02 | 综合控制目标值 | UNC | 综合控制目标 | verified |
"""


def _write_gold(tmp_path: Path, text: str = GOLD_MD) -> Path:
    path = tmp_path / "gold.md"
    path.write_text(text, encoding="utf-8")
    return path


def _change(change_type: str, anchor: str) -> als.ChangeItem:
    return als.ChangeItem(
        change_type=change_type, section_anchor=anchor, points=[]
    )


class TestParseGoldUnc:
    def test_unc_rows_are_kept_as_in_domain_negatives(self, tmp_path):
        rows = cli.parse_gold(_write_gold(tmp_path))
        assert len(rows) == 4
        negatives = [r for r in rows if r["status"] == "negative"]
        assert [r["id"] for r in negatives] == ["C-01", "C-02"]
        for row in negatives:
            assert row["change_type"] is None
            assert row["section_anchor"]
            assert row["field"]

    def test_unc_row_verified_verdict_never_becomes_a_change(self, tmp_path):
        # The C-row's 核验结论=verified says "the unchanged claim is
        # verified", not "a change is verified": the parser must force
        # status=negative so the row can never enter the recall denominator.
        rows = cli.parse_gold(_write_gold(tmp_path))
        c01 = next(r for r in rows if r["id"] == "C-01")
        assert c01["status"] == "negative"
        assert c01["change_type"] is None

    def test_unknown_type_other_than_unc_is_still_dropped(self, tmp_path):
        path = _write_gold(
            tmp_path, GOLD_MD + "| 9 | 神秘话题 | XYZ | 神秘章节 | verified |\n"
        )
        rows = cli.parse_gold(path)
        assert [r["id"] for r in rows] == ["1", "2", "C-01", "C-02"]

    def test_old_seed_shape_without_unc_is_unchanged(self, tmp_path):
        rows = cli.parse_gold(_write_gold(tmp_path))
        assert [r["id"] for r in rows if r["status"] == "verified"] == ["1"]
        assert [r["id"] for r in rows if r["status"] == "unverified"] == ["2"]


class TestUncNegativeCaliber:
    def test_negatives_leave_the_recall_denominator(self, tmp_path):
        rows = cli.parse_gold(_write_gold(tmp_path))
        machine = [_change("ADD", "1 胰岛素泵治疗的优势")]
        cal = cli.calibrate_loose(machine, rows)
        assert cal.gold_total == 1  # only the verified change row
        # unverified_excluded counts every verdict-filtered row: 1 unverified
        # + 2 in-domain negatives.
        assert cal.unverified_excluded == 3
        assert cal.recall == pytest.approx(1.0)

    def test_negatives_feed_the_precision_negative_set(self, tmp_path):
        rows = cli.parse_gold(_write_gold(tmp_path))
        # Same shaping the CLI / probe use for the negative-side accounting:
        # the unchanged topics run through the production topic matcher.
        negatives = [
            dict(r, status="verified") for r in rows if r["status"] == "negative"
        ]
        machine = [_change("UPDATE", "糖尿病的诊断与分型的分期表述")]
        neg = als.calibrate_topic(machine, negatives)
        assert neg.matched_topics == 1
        assert neg.matched_groups == 1
        assert neg.machine_groups == 1

    def test_topic_calibration_excludes_negatives_from_recall(self, tmp_path):
        rows = cli.parse_gold(_write_gold(tmp_path))
        machine = [
            _change("ADD", "1 胰岛素泵治疗的优势"),
            _change("UPDATE", "糖尿病的诊断与分型相关表述"),
        ]
        topic = als.calibrate_topic(machine, rows)
        assert topic.gold_total == 1
        assert topic.unverified_excluded == 3
        assert topic.recall == pytest.approx(1.0)


class TestMergedGoldSeed:
    def test_merged_seed_parses_to_51_rows_with_12_negatives(self):
        path = _REPO_ROOT / "competition" / "corpus" / "guideline_diff_seed_merged.md"
        if not path.exists():
            pytest.skip("merged gold seed not present")
        rows = cli.parse_gold(path)
        assert len(rows) == 51
        assert sum(1 for r in rows if r["status"] == "negative") == 12
        assert sum(1 for r in rows if r["status"] == "unverified") == 7
        assert sum(1 for r in rows if r["status"] in ("verified", "corrected")) == 32
        ids = [r["id"] for r in rows]
        assert len(set(ids)) == len(ids) == 51
