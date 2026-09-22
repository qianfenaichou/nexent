#!/usr/bin/env python3
"""P5 probe: T-21 alignment P/R caliber audit (item-level vs topic-level).

Answers one question with existing artifacts only: **which unit does the
T-21 (standard-aligner) precision/recall actually measure, and what do the
two published calibers really mean?**

Inputs (both already on disk, no LLM, no DB, no network):

* ``competition/deliverables/alignment-diff.json`` - one real aligner run of
  guide-2020 -> guide-2024 (691 machine change items; the same run the two
  published number sets were computed from, see its ``calibration`` and
  ``topic_calibration`` blocks);
* ``competition/corpus/guideline_diff_seed.md`` - the T-21 gold seed
  (16 curated change topics, 9 ``verified`` / 7 ``unverified``).

It recomputes BOTH calibers from scratch over those artifacts and
cross-checks the result against the numbers persisted inside the JSON:

* caliber A - **item level** (the "chunk/paragraph-level" first measurement):
  numerator = machine *items* whose ``(change_type, normalized anchor)``
  equals a gold topic key; denominator(precision) = all 691 machine items,
  denominator(recall) = the 9 evaluable gold rows.
* caliber B - **topic level** (the current official caliber): machine items
  are collapsed into ``(change_type, normalized section)`` groups, and each
  evaluable gold topic is matched to groups by discriminative-token overlap
  (CJK trigrams + ASCII words, document-frequency filter, >= 2 shared tokens).
  recall = share of gold topics with >= 1 matching group;
  precision_lower_bound = share of machine groups matching some gold topic.

Plus three honesty diagnostics that neither caliber reports on its own:

1. the structural ceiling of caliber A (a *perfect* detector still cannot
   exceed ``n_gold / n_items`` precision) - this is why 0.00145 must not be
   quoted as a quality score;
2. the provenance decomposition of the 691 items (structural section
   add/delete vs real paragraph/table edits) and the section-parser
   pollution count - i.e. the *real* quality caveats that survive after the
   caliber is fixed;
3. a df / min_shared sweep and unrelated-topic negative controls, so the
   topic-level recall is not a single unexamined default.

Engine: prefers the real backend functions (``services.knowevo.
alignment_service`` / ``pipeline.diff_guidelines``) so the recomputation is
the production logic itself; falls back to a byte-for-byte stdlib mirror of
the same algorithms when the backend import is unavailable (the mirror path
is reported as such in the output and both paths are compared when the real
module is importable).

Run (from ``nexent/``, either interpreter works; < 2s):

    python3 competition/experiments/probe_p5_alignment_caliber.py
    backend/.venv/bin/python competition/experiments/probe_p5_alignment_caliber.py

Add ``--output PATH`` to persist the JSON report (default: stdout only, so a
bare run leaves no new file).

适用范围：对**既有真实产物**的回溯核算（零 LLM、零 DB、零网络），
不是一次新的抽取/评测跑分，也不构成真实医疗数据上的效果结论。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

SCOPE_NOTE = (
    "适用范围：对既有真实产物（alignment-diff.json + guideline_diff_seed.md）的"
    "回溯核算，零 LLM / 零 DB / 零网络；不是新一次抽取跑分。"
)

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPETITION_DIR = os.path.dirname(EXPERIMENTS_DIR)
REPO_ROOT = os.path.dirname(COMPETITION_DIR)

DEFAULT_RUN = os.path.join(COMPETITION_DIR, "deliverables", "alignment-diff.json")
DEFAULT_GOLD = os.path.join(COMPETITION_DIR, "corpus", "guideline_diff_seed.md")

DEFAULT_SEED = 20260923  # no randomness in the core audit; kept for probe-family consistency

# ---------------------------------------------------------------------------
# stdlib mirror of the production helpers (fallback path only)
# ---------------------------------------------------------------------------

_MIRROR_NUM_RE = re.compile(
    r"^(?P<num>(第\s*[0-9一二三四五六七八九十百]+\s*[章节篇])"
    r"|(?P<dec>\d{1,2}(?:\.\d{1,2}){0,3}))"
    r"(?=$|[\s、.．,，:：/])"
    r"\s*[、.．,，:：]?\s*(?P<title>.*)$"
)
_MIRROR_CN_NUM_RE = re.compile(r"^[（(]\s*[0-9一二三四五六七八九十]+\s*[)）]\s*")
_MIRROR_TRAILING_YEAR_RE = re.compile(r"[（(]\s*(19|20)\d{2}\s*年?[版]?\s*[)）]\s*$")
_MIRROR_PUNCT_RE = re.compile(r"[\s\u3000,，、;；:：。.!！?？\"'“”‘’()（）\[\]【】<>《》|/\\\-—_]+")
_MIRROR_TITLE_FOLD = {
    "总论": "概述", "引言": "概述", "前言": "概述", "导言": "概述",
    "诊断标准": "诊断", "治疗原则": "治疗", "防治管理": "管理", "治疗管理": "管理",
}

_MIRROR_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_MIRROR_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")

_GOLD_TYPE_MAP = {
    "ADD": "ADD", "UPD": "UPDATE", "UPDATE": "UPDATE", "DEL": "DELETE",
    "DELETE": "DELETE", "MOVE": "MOVE", "RENUMBER": "RENUMBER",
    "SPLIT": "SPLIT", "MERGE": "MERGE",
}
_EVALUABLE = ("verified", "corrected")


def mirror_normalize_title(raw: str) -> str:
    """Stdlib mirror of ``alignment_service.normalize_title``."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).strip()
    text = _MIRROR_TRAILING_YEAR_RE.sub("", text)
    text = _MIRROR_CN_NUM_RE.sub("", text)
    match = _MIRROR_NUM_RE.match(text)
    if match:
        text = match.group("title") or ""
    text = _MIRROR_PUNCT_RE.sub("", text)
    text = text.lower()
    for src, dst in _MIRROR_TITLE_FOLD.items():
        if text == src:
            return dst
    return text


def mirror_topic_tokens(text: str | None) -> set[str]:
    """Stdlib mirror of ``alignment_service.topic_tokens`` (CJK trigrams)."""
    if not text:
        return set()
    tokens = {w.lower() for w in _MIRROR_ASCII_WORD_RE.findall(text)}
    for run in _MIRROR_CJK_RUN_RE.findall(text):
        for i in range(len(run) - 2):
            tokens.add(run[i : i + 3])
    return tokens


def mirror_calibrate_topic(
    machine: list[dict],
    gold: list[dict],
    *,
    max_df_fraction: float = 0.05,
    min_shared_tokens: int = 2,
) -> dict:
    """Stdlib mirror of ``alignment_service.calibrate_topic`` (+ group aggregation)."""
    buckets: dict[tuple[str, str], dict] = {}
    for item in machine:
        if item["change_type"] == "UNCHANGED":
            continue
        key = (item["change_type"], mirror_normalize_title(item["section_anchor"]))
        group = buckets.get(key)
        if group is None:
            group = {"count": 0, "tokens": set()}
            buckets[key] = group
        group["count"] += 1
        group["tokens"] |= mirror_topic_tokens(item["section_anchor"])
        for point in item["points"]:
            group["tokens"] |= mirror_topic_tokens(point)
    groups = list(buckets.values())
    eligible = [g for g in gold if str(g.get("status", "")).lower() in _EVALUABLE]
    unverified = len(gold) - len(eligible)
    matched_groups: set[int] = set()
    match_counts: dict[str, int] = {}
    matched_topics = 0
    if groups and eligible:
        df: dict[str, int] = {}
        for group in groups:
            for token in group["tokens"]:
                df[token] = df.get(token, 0) + 1
        df_limit = max(3, int(len(groups) * max_df_fraction))
        for row in eligible:
            gold_tokens = mirror_topic_tokens(str(row.get("section_anchor", ""))) | (
                mirror_topic_tokens(str(row.get("field", "") or ""))
            )
            hits = 0
            for index, group in enumerate(groups):
                shared = {
                    token
                    for token in (gold_tokens & group["tokens"])
                    if df.get(token, 0) <= df_limit
                }
                if len(shared) >= min_shared_tokens:
                    hits += 1
                    matched_groups.add(index)
            match_counts[str(row.get("id") or row.get("section_anchor") or "")] = hits
            if hits:
                matched_topics += 1
    return {
        "recall": (matched_topics / len(eligible)) if eligible else None,
        "precision_lower_bound": (
            (len(matched_groups) / len(groups)) if (groups and eligible) else None
        ),
        "matched_topics": matched_topics,
        "gold_total": len(eligible),
        "matched_groups": len(matched_groups),
        "machine_groups": len(groups),
        "machine_total": sum(g["count"] for g in groups),
        "unverified_excluded": unverified,
        "max_df_fraction": max_df_fraction,
        "min_shared_tokens": min_shared_tokens,
        "topic_match_counts": match_counts,
        "_group_keys": buckets,
    }


# ---------------------------------------------------------------------------
# engine resolution: real backend functions when importable, mirror otherwise
# ---------------------------------------------------------------------------


class Engine:
    """Thin adapter over the real production functions or their stdlib mirror."""

    def __init__(self) -> None:
        self.kind = "stdlib_mirror"
        self.path_note = ""
        self.als = None
        self.cli = None
        if os.path.join(REPO_ROOT, "backend") not in sys.path:
            sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
        try:  # pragma: no cover - depends on the running interpreter
            from services.knowevo import alignment_service as als
            from services.knowevo.pipeline import diff_guidelines as cli

            self.als = als
            self.cli = cli
            self.kind = "real_backend_module"
            self.path_note = os.path.relpath(als.__file__, REPO_ROOT)
        except Exception as exc:  # noqa: BLE001 - fall back, never crash a probe
            self.path_note = f"{type(exc).__name__}: {exc}"

    def normalize_title(self, raw: str) -> str:
        if self.als is not None:
            return self.als.normalize_title(raw)
        return mirror_normalize_title(raw)

    def item_level(self, machine: list[dict], gold: list[dict]) -> dict:
        """Both item-level matchers: strict keys and the loose `calibrate_loose`."""
        if self.als is not None and self.cli is not None:
            items = [
                self.als.ChangeItem(
                    change_type=c["change_type"],
                    section_anchor=c.get("section_anchor") or "",
                    points=list(c.get("points") or []),
                )
                for c in machine
            ]
            strict = self.als.calibrate_pr(items, gold)
            loose = self.cli.calibrate_loose(items, gold)
            return {
                "strict": {
                    "precision": strict.precision, "recall": strict.recall,
                    "matched": strict.matched, "gold_total": strict.gold_total,
                    "machine_total": strict.machine_total,
                    "unverified_excluded": strict.unverified_excluded,
                },
                "loose": {
                    "precision": loose.precision, "recall": loose.recall,
                    "matched": loose.matched, "gold_total": loose.gold_total,
                    "machine_total": loose.machine_total,
                    "unverified_excluded": loose.unverified_excluded,
                },
            }
        return {
            "strict": self._mirror_item_level(machine, gold, loose=False),
            "loose": self._mirror_item_level(machine, gold, loose=True),
        }

    def _mirror_item_level(self, machine: list[dict], gold: list[dict], *, loose: bool) -> dict:
        machine_real = [c for c in machine if c["change_type"] != "UNCHANGED"]
        eligible = [g for g in gold if str(g.get("status", "")).lower() in _EVALUABLE]
        unverified = len(gold) - len(eligible)
        remaining = list(eligible)
        matched = 0
        for item in machine_real:
            own = (
                item["change_type"].upper(),
                self.normalize_title(item.get("section_anchor") or ""),
            )
            found = None
            for row in remaining:
                key = (str(row["change_type"]).upper(), self.normalize_title(row["section_anchor"]))
                if key == own:
                    found = row["section_anchor"]
                    break
            if found is None and loose:
                haystack = f"{item.get('section_anchor') or ''} {item.get('points') or []}"
                for row in remaining:
                    if row["change_type"] != item["change_type"]:
                        continue
                    label = row.get("field") or row["section_anchor"]
                    if label and label in haystack:
                        found = row["section_anchor"]
                        break
            if found is not None:
                matched += 1
                remaining = [r for r in remaining if r["section_anchor"] != found]
        return {
            "precision": (matched / len(machine_real)) if (machine_real and eligible) else None,
            "recall": (matched / len(eligible)) if eligible else None,
            "matched": matched,
            "gold_total": len(eligible),
            "machine_total": len(machine_real),
            "unverified_excluded": unverified,
        }

    def topic_level(
        self, machine: list[dict], gold: list[dict], *, max_df_fraction: float = 0.05,
        min_shared_tokens: int = 2,
    ) -> dict:
        if self.als is not None:
            items = [
                self.als.ChangeItem(
                    change_type=c["change_type"],
                    section_anchor=c.get("section_anchor") or "",
                    points=list(c.get("points") or []),
                )
                for c in machine
            ]
            topic = self.als.calibrate_topic(
                items, gold,
                max_df_fraction=max_df_fraction, min_shared_tokens=min_shared_tokens,
            )
            groups = self.als.aggregate_change_groups(items)
            return {
                "recall": topic.recall,
                "precision_lower_bound": topic.precision_lower_bound,
                "matched_topics": topic.matched_topics,
                "gold_total": topic.gold_total,
                "matched_groups": topic.matched_groups,
                "machine_groups": topic.machine_groups,
                "machine_total": topic.machine_total,
                "unverified_excluded": topic.unverified_excluded,
                "max_df_fraction": topic.max_df_fraction,
                "min_shared_tokens": topic.min_shared_tokens,
                "topic_match_counts": topic.topic_match_counts,
                "_group_keys": Counter(
                    (g.change_type, g.section_anchor) for g in groups
                ),
            }
        return mirror_calibrate_topic(
            machine, gold,
            max_df_fraction=max_df_fraction, min_shared_tokens=min_shared_tokens,
        )


# ---------------------------------------------------------------------------
# gold seed + artifact loading
# ---------------------------------------------------------------------------


def load_gold(path: str, engine: Engine) -> list[dict]:
    """Parse the gold seed with the production parser when available."""
    if engine.cli is not None:
        return engine.cli.parse_gold(__import__("pathlib").Path(path))
    rows: list[dict] = []
    headers: list[str] = []
    text = open(path, encoding="utf-8").read()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            headers = []
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells if c):
            continue
        if not headers:
            if any("类型" in c or "change_type" in c for c in cells):
                headers = cells
            continue
        row = dict(zip(headers, cells))
        change_type = _GOLD_TYPE_MAP.get(
            (row.get("类型") or row.get("change_type") or "").strip().upper()
        )
        if change_type is None:
            continue
        anchor = (
            row.get("章节锚点") or row.get("anchor")
            or row.get("section_anchor") or row.get("领域") or ""
        ).strip()
        status = (row.get("核验结论") or row.get("status") or "").strip().lower()
        if status not in ("verified", "corrected", "unverified"):
            status = "unverified"
        rows.append({
            "id": (row.get("#") or row.get("id") or str(len(rows) + 1)).strip(),
            "change_type": change_type,
            "section_anchor": anchor,
            "status": status,
            "field": (row.get("领域") or row.get("field") or "").strip(),
        })
    return rows


def load_artifact(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------

_STRUCTURAL_PREFIXES = (
    "new section:", "removed section:", "section moved:", "section renumbered:",
)


def item_provenance(machine: list[dict]) -> dict:
    """Split the emitted items by what produced them (structural vs content)."""
    buckets: Counter = Counter()
    for item in machine:
        point = (item.get("points") or [""])[0]
        if point.startswith("new section:"):
            buckets["structural_section_add"] += 1
        elif point.startswith("removed section:"):
            buckets["structural_section_delete"] += 1
        elif point.startswith("section moved:"):
            buckets["structural_section_move"] += 1
        elif point.startswith("section renumbered:"):
            buckets["structural_section_renumber"] += 1
        elif point.startswith("table added:"):
            buckets["table_add"] += 1
        elif point.startswith("table removed:"):
            buckets["table_delete"] += 1
        elif point.startswith("row "):
            buckets["table_row_change"] += 1
        elif point in ("added paragraph", "removed paragraph"):
            buckets["paragraph_unmatched_add_delete"] += 1
        elif point.startswith("similarity "):
            buckets["paragraph_similarity_judged"] += 1
        else:
            buckets["paragraph_llm_verdict"] += 1
    structural = sum(v for k, v in buckets.items() if k.startswith(("structural_", "table_")))
    return {
        "buckets": dict(sorted(buckets.items())),
        "structural_and_table_items": structural,
        "paragraph_items": len(machine) - structural,
    }


def _looks_like_body_text(anchor: str) -> bool:
    """A section anchor is 'polluted' when the heading detector kept body prose."""
    if "。" in anchor or "，," in anchor:
        return True
    return len(re.sub(r"\s+", "", anchor)) > 30


def section_parser_pollution(machine: list[dict]) -> dict:
    adds = [i for i in machine if (i.get("points") or [""])[0].startswith("new section:")]
    dels = [i for i in machine if (i.get("points") or [""])[0].startswith("removed section:")]
    return {
        "section_add_items": len(adds),
        "section_add_polluted": sum(1 for i in adds if _looks_like_body_text(i["section_anchor"])),
        "section_delete_items": len(dels),
        "section_delete_polluted": sum(1 for i in dels if _looks_like_body_text(i["section_anchor"])),
    }


def anchor_relaxation_ladder(machine: list[dict], gold: list[dict], engine: Engine) -> dict:
    """How much of the item-level miss is anchor formatting vs change-type wording.

    Rung 1  exact   : (type, normalized anchor) equality - the strict matcher.
    Rung 2  contains: same type AND the gold anchor appears inside the machine
                      anchor (normalized containment) - isolates anchor-shape loss.
    Rung 3  type    : same type AND >= 2 shared discriminative tokens ("topic
                      reachable by wording, any anchor shape") - isolates the
                      change-type wording loss.
    """
    eligible = [g for g in gold if str(g.get("status", "")).lower() in _EVALUABLE]
    machine_real = [c for c in machine if c["change_type"] != "UNCHANGED"]
    m_anchors = [engine.normalize_title(i.get("section_anchor") or "") for i in machine_real]

    def matches(predicate) -> tuple[int, int]:
        remaining = list(eligible)
        matched = 0
        for item, norm in zip(machine_real, m_anchors):
            hit = None
            for row in remaining:
                if predicate(item, norm, row):
                    hit = row["section_anchor"]
                    break
            if hit is not None:
                matched += 1
                remaining = [r for r in remaining if r["section_anchor"] != hit]
        return matched, len(machine_real) - matched

    def pred_exact(item, norm, row) -> bool:
        return item["change_type"] == row["change_type"] and engine.normalize_title(
            row["section_anchor"]
        ) == norm

    def pred_contains(item, norm, row) -> bool:
        gold_norm = engine.normalize_title(row["section_anchor"])
        return (
            item["change_type"] == row["change_type"]
            and bool(gold_norm)
            and gold_norm in norm
        )

    def pred_type(item, norm, row) -> bool:
        gold_norm = engine.normalize_title(row["section_anchor"])
        tokens = mirror_topic_tokens(gold_norm) & mirror_topic_tokens(norm)
        return item["change_type"] == row["change_type"] and len(tokens) >= 2

    out = {}
    for name, predicate in (
        ("exact", pred_exact), ("contains", pred_contains), ("same_type_token_overlap", pred_type),
    ):
        matched, unmatched = matches(predicate)
        out[name] = {
            "matched_items": matched,
            "unmatched_items": unmatched,
            "precision_item_level": matched / len(machine_real) if machine_real else None,
        }
    return out


NEGATIVE_TOPICS = [
    "运载火箭姿态控制", "量子纠错码阈值", "区块链分片共识", "半导体光刻胶配方",
    "航空发动机叶片冷却", "锂离子电池热失控", "碳纤维复合材料固化", "卫星轨道机动规划",
    "核聚变等离子体约束", "深海钻探取样", "地震波反演成像", "超导磁悬浮导向",
    "工业机器人焊接路径", "气象雷达回波外推",
]


def negative_control(machine: list[dict], engine: Engine) -> dict:
    """Unrelated topics must match nothing (topic-level matcher, zero LLM)."""
    gold_shaped = [
        {"id": f"neg{i}", "change_type": "UPDATE", "section_anchor": topic,
         "status": "verified", "field": ""}
        for i, topic in enumerate(NEGATIVE_TOPICS, start=1)
    ]
    cal = engine.topic_level(machine, gold_shaped)
    return {
        "n_negative_topics": len(NEGATIVE_TOPICS),
        "matched_topics": cal["matched_topics"],
        "matched_groups": cal["matched_groups"],
        "hits_per_topic": cal["topic_match_counts"],
    }


def sensitivity_sweep(machine: list[dict], gold: list[dict], engine: Engine) -> dict:
    rows = []
    for df in (0.02, 0.05, 0.10, 0.20, 1.00):
        for min_shared in (2, 3):
            cal = engine.topic_level(
                machine, gold, max_df_fraction=df, min_shared_tokens=min_shared
            )
            rows.append({
                "max_df_fraction": df,
                "min_shared_tokens": min_shared,
                "recall": cal["recall"],
                "matched_topics": cal["matched_topics"],
                "gold_total": cal["gold_total"],
                "precision_lower_bound": cal["precision_lower_bound"],
                "matched_groups": cal["matched_groups"],
                "machine_groups": cal["machine_groups"],
            })
    return {"grid": rows}


# ---------------------------------------------------------------------------
# the audit
# ---------------------------------------------------------------------------


def run_probe(run_path: str, gold_path: str, seed: int, engine: Engine) -> dict:
    artifact = load_artifact(run_path)
    machine = artifact["changes"]
    gold = load_gold(gold_path, engine)

    eligible = [g for g in gold if str(g.get("status", "")).lower() in _EVALUABLE]
    excluded = [g for g in gold if str(g.get("status", "")).lower() not in _EVALUABLE]

    # -- caliber A ----------------------------------------------------------
    item = engine.item_level(machine, gold)
    persisted_a = artifact.get("calibration")
    n_items = len(machine)
    ceiling = (len(eligible) / n_items) if n_items else None
    achieved = item["strict"]["matched"] / n_items if n_items else None

    # -- caliber B ----------------------------------------------------------
    topic = engine.topic_level(machine, gold)
    persisted_b = artifact.get("topic_calibration")

    # -- fidelity cross-check (mirror vs real vs persisted) -----------------
    def close(a, b) -> bool:
        if a is None or b is None:
            return a is b
        return abs(float(a) - float(b)) < 1e-12

    cross_check = {
        "engine": engine.kind,
        "engine_path": engine.path_note,
        "artifact_machine_total_equals_len_changes": (
            (persisted_a or {}).get("machine_total") == n_items
        ),
        "artifact_gold_total_equals_verified_rows": (
            (persisted_a or {}).get("gold_total") == len(eligible)
        ),
        "recomputed_strict_equals_persisted_calibration": bool(
            persisted_a
            and close(item["strict"]["precision"], persisted_a.get("precision"))
            and close(item["strict"]["recall"], persisted_a.get("recall"))
            and item["strict"]["matched"] == persisted_a.get("matched")
        ),
        "recomputed_topic_equals_persisted_topic_calibration": bool(
            persisted_b
            and close(topic["recall"], persisted_b.get("recall"))
            and close(topic["precision_lower_bound"], persisted_b.get("precision_lower_bound"))
            and topic["matched_topics"] == persisted_b.get("matched_topics")
            and topic["matched_groups"] == persisted_b.get("matched_groups")
            and topic["machine_groups"] == persisted_b.get("machine_groups")
        ),
    }

    payload = {
        "probe": "p5_alignment_caliber",
        "question": (
            "T-21 standard aligner: which unit do the published P/R numbers measure, "
            "and is the low item-level precision a quality defect or a caliber "
            "(numerator/denominator) mismatch?"
        ),
        "config": {
            "seed": seed,
            "run_artifact": os.path.relpath(run_path, REPO_ROOT),
            "gold_seed": os.path.relpath(gold_path, REPO_ROOT),
            "gold_seed_declared_scope": "16 curated topics, 9 verified / 7 unverified",
            "llm_enabled_in_artifact": artifact.get("run", {}).get("llm_enabled"),
            "zero_llm_rerun": True,
            "engine": engine.kind,
        },
        "results": {
            "a_gold_seed": {
                "rows_total": len(gold),
                "evaluable_verified_or_corrected": len(eligible),
                "excluded_unverified": len(excluded),
                "evaluable_by_change_type": dict(
                    sorted(Counter(g["change_type"] for g in eligible).items())
                ),
                "excluded_ids": [g["id"] for g in excluded],
            },
            "b_machine_run": {
                "items_total": n_items,
                "items_by_change_type": dict(
                    sorted(Counter(c["change_type"] for c in machine).items())
                ),
                "sections_matched": len(artifact["sections"]["matched"]),
                "sections_added": len(artifact["sections"]["added"]),
                "sections_deleted": len(artifact["sections"]["deleted"]),
                "table_changes": len(artifact.get("table_changes") or []),
                "provenance": item_provenance(machine),
                "section_parser_pollution": section_parser_pollution(machine),
            },
            "c_caliber_a_item_level": {
                "unit": "one machine change item (paragraph/section/table row event)",
                "precision_numerator": "machine items whose (type, anchor) key equals a gold topic",
                "precision_denominator": f"{n_items} machine items",
                "recall_numerator": "same matches",
                "recall_denominator": f"{len(eligible)} verified gold topics",
                "strict": item["strict"],
                "loose_domain_label": item["loose"],
                "structural_ceiling_precision": ceiling,
                "ceiling_derivation": (
                    f"a perfect detector emitting {n_items} items can score at most "
                    f"{len(eligible)}/{n_items} = {ceiling:.6f} precision against a "
                    f"{len(eligible)}-topic gold"
                ),
                "achieved_over_ceiling": (
                    (achieved / ceiling) if (achieved is not None and ceiling) else None
                ),
                "anchors": anchor_relaxation_ladder(machine, gold, engine),
                "equivalence_note": (
                    "precision_item = (matched/n_items) is recall x n_gold/n_items when the "
                    "same matches are counted; the two calibers share one numerator, so the "
                    "item-level precision is not an independent quality signal"
                ),
            },
            "d_caliber_b_topic_level": {
                "unit": "one (change_type, normalized section) group; gold unit = one curated topic",
                "recall_numerator": "gold topics with >= 1 matching group",
                "recall_denominator": f"{len(eligible)} verified gold topics",
                "precision_denominator": f"{topic['machine_groups']} machine groups",
                "recall": topic["recall"],
                "matched_topics": topic["matched_topics"],
                "gold_total": topic["gold_total"],
                "matched_groups": topic["matched_groups"],
                "machine_groups": topic["machine_groups"],
                "machine_total": topic["machine_total"],
                "precision_lower_bound": topic["precision_lower_bound"],
                "max_df_fraction": topic["max_df_fraction"],
                "min_shared_tokens": topic["min_shared_tokens"],
                "topic_match_counts": topic["topic_match_counts"],
                "is_lower_bound_why": (
                    "the gold seed is a curated, non-exhaustive sample of 16 topics, so a "
                    "machine group with no gold counterpart is not necessarily wrong"
                ),
            },
            "e_robustness": {
                "sensitivity": sensitivity_sweep(machine, gold, engine),
                "negative_control": negative_control(machine, engine),
            },
            "f_cross_check": cross_check,
            "g_persisted": {"calibration": persisted_a, "topic_calibration": persisted_b},
            "reading_notes": [
                "Caliber A (item level) and caliber B (topic level) share ONE numerator: the "
                "topic that the item-level matcher credits is a subset of the topics the "
                "topic-level matcher credits.",
                "0.00145 = 1/691 is bounded above by 9/691 = 0.0130 no matter how good the "
                "detector is, because the gold has 9 rows and the detector emitted 691 items; "
                "it measures the unit ratio, not detection quality.",
                "Caliber B's recall 9/9 has a denominator of 9 (verified rows of a 16-row "
                "curated seed) - it is evidence of coverage on 9 topics, not a precise "
                "recall estimate, and it must never be converted into a precision claim.",
                "precision_lower_bound 64/517 is a LOWER BOUND against a non-exhaustive gold; "
                "it is not a precision measurement and must never be quoted as one.",
                "The real quality caveats that survive the caliber fix are in "
                "b_machine_run.provenance and .section_parser_pollution.",
            ],
            "scope_note": SCOPE_NOTE,
        },
    }
    return payload


# ---------------------------------------------------------------------------
# stdout report
# ---------------------------------------------------------------------------


def print_report(payload: dict) -> None:
    r = payload["results"]
    g, m = r["a_gold_seed"], r["b_machine_run"]
    a, b, x = r["c_caliber_a_item_level"], r["d_caliber_b_topic_level"], r["f_cross_check"]

    print("== P5: T-21 alignment caliber audit (item level vs topic level) ==")
    print(f"  engine : {x['engine']}  ({x['engine_path']})")
    print(f"  inputs : {payload['config']['run_artifact']} + {payload['config']['gold_seed']}")
    print()
    print("[A] gold seed (guideline_diff_seed.md)")
    print(f"    rows={g['rows_total']}  evaluable(verified/corrected)={g['evaluable_verified_or_corrected']}"
          f"  excluded(unverified)={g['excluded_unverified']}  by type={g['evaluable_by_change_type']}")
    print(f"    unverified ids (excluded from BOTH numerator and denominator): {g['excluded_ids']}")
    print()
    print("[B] machine run (alignment-diff.json, the same run both calibers were computed from)")
    print(f"    items={m['items_total']}  by type={m['items_by_change_type']}")
    print(f"    sections matched/added/deleted={m['sections_matched']}/{m['sections_added']}"
          f"/{m['sections_deleted']}  table changes={m['table_changes']}")
    prov = m["provenance"]
    print(f"    provenance: structural+table={prov['structural_and_table_items']}"
          f"  paragraph={prov['paragraph_items']}  detail={prov['buckets']}")
    pol = m["section_parser_pollution"]
    print(f"    section-parser pollution: add {pol['section_add_polluted']}/{pol['section_add_items']}"
          f"  delete {pol['section_delete_polluted']}/{pol['section_delete_items']}"
          f"  (anchors that look like body prose)")
    print()
    print("[C] caliber A - ITEM level (the 'chunk-level' first measurement)")
    for name, cal in (("strict", a["strict"]), ("loose", a["loose_domain_label"])):
        p = f"{cal['precision']:.6f}" if cal["precision"] is not None else "None"
        rc = f"{cal['recall']:.6f}" if cal["recall"] is not None else "None"
        print(f"    {name:6s}: matched={cal['matched']}  precision={cal['matched']}/{cal['machine_total']}"
              f"={p}  recall={cal['matched']}/{cal['gold_total']}={rc}")
    print(f"    structural ceiling: {g['evaluable_verified_or_corrected']}/{m['items_total']}"
          f"={a['structural_ceiling_precision']:.6f}  (perfect detector, same 691 items)")
    print(f"    achieved/ceiling = {a['achieved_over_ceiling']:.6f}")
    print("    anchor relaxation ladder (matched items / precision):")
    for name, row in a["anchors"].items():
        print(f"      {name:26s} matched={row['matched_items']:3d}  precision={row['precision_item_level']:.6f}")
    print()
    print("[D] caliber B - TOPIC level (current official caliber)")
    print(f"    unit: gold topic (n={g['evaluable_verified_or_corrected']}) vs machine group"
          f" (n={b['machine_groups']}, collapsed from {b['machine_total']} items)")
    print(f"    recall = {b['matched_topics']}/{b['gold_total']} = {b['recall']}")
    print(f"    precision_lower_bound = {b['matched_groups']}/{b['machine_groups']}"
          f" = {b['precision_lower_bound']:.6f}  (LOWER BOUND, not a precision)")
    print(f"    per-topic group hits: {b['topic_match_counts']}")
    print()
    print("[E] robustness")
    print("    sweep (recall matched/total, precision_lower_bound):")
    for row in r["e_robustness"]["sensitivity"]["grid"]:
        print(f"      df<={row['max_df_fraction']:<5} min_shared={row['min_shared_tokens']}"
              f"  recall={row['matched_topics']}/{row['gold_total']}"
              f"  p_lb={row['matched_groups']}/{row['machine_groups']}"
              f"={row['precision_lower_bound']:.6f}")
    neg = r["e_robustness"]["negative_control"]
    print(f"    negative control: {neg['n_negative_topics']} unrelated topics ->"
          f" matched_topics={neg['matched_topics']}  matched_groups={neg['matched_groups']}")
    print()
    print("[F] recomputation fidelity (this run vs numbers persisted in the artifact)")
    for key in (
        "artifact_machine_total_equals_len_changes",
        "artifact_gold_total_equals_verified_rows",
        "recomputed_strict_equals_persisted_calibration",
        "recomputed_topic_equals_persisted_topic_calibration",
    ):
        print(f"    {key}: {'PASS' if x[key] else 'FAIL'}")
    print()
    print(f"  scope: {SCOPE_NOTE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="P5 probe: recompute both T-21 alignment P/R calibers from artifacts (zero LLM)."
    )
    parser.add_argument("--run", default=DEFAULT_RUN, help=f"alignment-diff.json (default: {DEFAULT_RUN})")
    parser.add_argument("--gold", default=DEFAULT_GOLD, help=f"gold seed (default: {DEFAULT_GOLD})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"seed (default: {DEFAULT_SEED}; the audit itself is deterministic)")
    parser.add_argument("--output", default=None,
                        help="optional JSON output path (default: stdout only, no file written)")
    args = parser.parse_args()

    engine = Engine()
    payload = run_probe(os.path.abspath(args.run), os.path.abspath(args.gold), args.seed, engine)
    print_report(payload)
    if args.output:
        out = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"[written] {out}")


if __name__ == "__main__":
    main()
