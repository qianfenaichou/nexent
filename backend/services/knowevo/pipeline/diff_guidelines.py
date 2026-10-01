"""CLI for the standard aligner : diff two registered documents.

Runs the three-stage change detector over two documents registered in
``competition/corpus/registry.csv``, then (when a tenant is available) the
affected-surface analysis and the VOI minimal update set, writing a JSON
report to ``--out``.

Acceptance-command shapes from competition/tasks/-brief.md:

    python -m services.knowevo.pipeline.diff_guidelines \
        --old guide-2020 --new guide-2024 \
        --gold ../guideline_diff_seed.md \
        --out ../alignment-diff.json

    python -m services.knowevo.pipeline.diff_guidelines --impact-only \
        --old guide-2020 --new guide-2024 --tenant <uuid> \
        --out ../alignment-impact.json

Honest reporting rules baked into the CLI:

* ``--no-llm`` runs the fully deterministic path (STEP 3 falls back to the
  labels the deterministic pipeline can justify); the report says so.
* P/R is only printed when a gold file yields evaluable rows. Gold rows
  without an explicit ``verified``/``corrected`` verdict are treated as
  ``unverified`` and excluded from precision/recall - an unverified row must
  never be counted as correct.
* Gold rows typed ``UNC`` (unchanged topic, batch2 C-rows) are **in-domain
  negatives** (2026-09-27 user ruling): kept as ``status="negative"`` rows
  instead of being silently dropped, they leave the recall denominator (not
  a detectable change) and feed the precision-side false-positive set - a
  machine change reported on an unchanged topic is a false positive.
* Impact analysis is skipped (not faked) when no tenant is given, and any
  changed paragraph that does not match an evidence row is simply absent
  from the affected surface.

Text is taken from ``--old-text``/``--new-text`` when given, otherwise from
the registry entry's ``local_file`` (PDFs are converted once with pdftotext
and cached under ``competition/.alignment-cache`` because /tmp does not
survive across sessions).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "backend"))

from services.knowevo import alignment_service as als

DEFAULT_CORPUS = _REPO_ROOT / "competition" / "corpus"
DEFAULT_CACHE = _REPO_ROOT / "competition" / ".alignment-cache"

# The gold seed uses the abbreviated change-type vocabulary of the L9 seed
# table; the detector uses the full names.
_GOLD_TYPE_MAP = {
    "ADD": "ADD",
    "UPD": "UPDATE",
    "UPDATE": "UPDATE",
    "DEL": "DELETE",
    "DELETE": "DELETE",
    "MOVE": "MOVE",
    "RENUMBER": "RENUMBER",
    "SPLIT": "SPLIT",
    "MERGE": "MERGE",
}


def load_registry(corpus_root: Path) -> dict[str, str]:
    """Map asset_no -> local_file for every registered corpus row."""
    registry = corpus_root / "registry.csv"
    if not registry.exists():
        raise SystemExit(f"registry not found: {registry}")
    with registry.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        row["asset_no"]: (row.get("local_file") or "").strip()
        for row in rows
        if row.get("asset_no")
    }


def document_text(asset_no: str, corpus_root: Path, cache_root: Path) -> str:
    """Read the document text, converting a PDF once into the cache dir."""
    registry = load_registry(corpus_root)
    if asset_no not in registry:
        raise SystemExit(
            f"asset_no {asset_no!r} is not in {corpus_root / 'registry.csv'}; "
            f"known keys: {sorted(registry)[:12]}"
        )
    rel = registry[asset_no]
    if not rel:
        raise SystemExit(f"asset_no {asset_no!r} has no local_file in the registry")
    source = corpus_root / rel
    if not source.exists():
        raise SystemExit(f"corpus file missing for {asset_no!r}: {source}")
    if source.suffix.lower() in (".txt", ".md"):
        return source.read_text(encoding="utf-8", errors="replace")
    cache_root.mkdir(parents=True, exist_ok=True)
    cached = cache_root / f"{asset_no}__{source.stem}.txt"
    if not cached.exists():
        try:
            subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", str(source), str(cached)],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise SystemExit(
                "pdftotext is not installed and no cached text exists for "
                f"{asset_no!r}; install poppler-utils or pass --old-text/--new-text"
            ) from exc
    return cached.read_text(encoding="utf-8", errors="replace")


def parse_gold(path: Path) -> list[dict]:
    """Parse a gold seed: JSON list, or the markdown table used by the L9 seed.

    A row without an explicit verification verdict becomes ``unverified`` so
    it can never inflate precision/recall. A row typed ``UNC`` (unchanged
    topic, batch2 C-rows) becomes an in-domain negative: ``status="negative"``,
    ``change_type=None`` - kept instead of silently dropped, excluded from the
    recall denominator by the usual verdict filter, and counted on the
    false-positive side of the precision axis (2026-09-27 user ruling).
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise SystemExit("gold JSON must be a list of objects")
        return data
    rows: list[dict] = []
    headers: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            headers = []
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells if c):
            continue  # separator row
        if not headers:
            if any("类型" in c or "change_type" in c for c in cells):
                headers = cells
            continue
        row = dict(zip(headers, cells))
        raw_type = (row.get("类型") or row.get("change_type") or "").strip().upper()
        change_type = _GOLD_TYPE_MAP.get(raw_type)
        # 口径（2026-09-27 the agreed caliber）：类型 UNC = 未变更话题（域内阴性）。
        # 不再静默丢弃：保留为 status="negative"、change_type=None 的行——
        # recall 侧被 verified/corrected 过滤器排除（退出 recall 分母，不计为
        # gold change）；precision 侧进入假阳分母的阴性集（机器把未变更话题
        # 报成变更即假阳）。依据：guideline_diff_seed_batch2.md 头部 P/R 规则。
        is_negative = change_type is None and raw_type == "UNC"
        if change_type is None and not is_negative:
            continue  # unknown type: neither a gold change nor a declared negative
        anchor = (
            row.get("章节锚点")
            or row.get("anchor")
            or row.get("section_anchor")
            or row.get("领域")
            or ""
        ).strip()
        if is_negative:
            # A C-row's 核验结论=verified means "the unchanged claim is
            # verified", NOT a change verdict - force the negative status so
            # the row can never enter the recall denominator.
            status = "negative"
        else:
            status = (
                row.get("核验结论") or row.get("status") or ""
            ).strip().lower()
            if status not in ("verified", "corrected", "unverified"):
                status = "unverified"
        rows.append(
            {
                "id": (row.get("#") or row.get("id") or str(len(rows) + 1)).strip(),
                "change_type": change_type,
                "section_anchor": anchor,
                "status": status,
                "field": (row.get("领域") or row.get("field") or "").strip(),
            }
        )
    return rows


def _gold_key(change_type: str, anchor: str) -> tuple[str, str]:
    return als._gold_key(change_type, anchor)


def _match_anchor(item: als.ChangeItem, gold_rows: list[dict]) -> str:
    """Return the gold anchor this change matches, or '' when none does.

    Matching tries the change's own anchor first (section numbers/titles),
    then the gold row's domain label appearing anywhere in the section path.
    This is the *item-level* baseline only; the official calibration is
    :func:`services.knowevo.alignment_service.calibrate_topic`, because the
    gold seed is topic-level while the detector emits paragraph-level items.
    """
    own = _gold_key(item.change_type, item.section_anchor)
    for row in gold_rows:
        if _gold_key(row["change_type"], row["section_anchor"]) == own:
            return row["section_anchor"]
    haystack = f"{item.section_anchor} {item.points}"
    for row in gold_rows:
        if row["change_type"] != item.change_type:
            continue
        label = row.get("field") or row["section_anchor"]
        if label and label in haystack:
            return row["section_anchor"]
    return ""


def calibrate_loose(machine: list[als.ChangeItem], gold: list[dict]) -> als.Calibration:
    """P/R where only *evaluable* gold rows count, using loose matching.

    ``calibrate_pr`` matches anchors exactly; the L9 seed's anchors are
    section titles while the detector emits numbered paths, so this wrapper
    matches on the gold row's domain label as well. The honesty rule is
    unchanged: unverified rows are excluded from both sides, and so are
    in-domain negatives (``status="negative"``, the UNC unchanged topics) -
    ``unverified_excluded`` counts every verdict-filtered row (unverified +
    negatives).
    """
    machine_real = [c for c in machine if c.change_type != "UNCHANGED"]
    eligible = [
        g for g in gold if str(g.get("status", "")).lower() in ("verified", "corrected")
    ]
    unverified = len(gold) - len(eligible)
    remaining = list(eligible)
    matched = 0
    for item in machine_real:
        found = _match_anchor(item, remaining)
        if found:
            matched += 1
            remaining = [
                r for r in remaining if r["section_anchor"] != found
            ]
    return als.Calibration(
        # No evaluable gold row means nothing is measurable: None, not 0.0.
        precision=(matched / len(machine_real)) if (machine_real and eligible) else None,
        recall=(matched / len(eligible)) if eligible else None,
        matched=matched,
        gold_total=len(eligible),
        machine_total=len(machine_real),
        unverified_excluded=unverified,
    )


async def _run(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus).resolve()
    cache = Path(args.cache).resolve()
    if args.old_text:
        old_text = Path(args.old_text).read_text(encoding="utf-8", errors="replace")
    else:
        old_text = document_text(args.old, corpus, cache)
    if args.new_text:
        new_text = Path(args.new_text).read_text(encoding="utf-8", errors="replace")
    else:
        new_text = document_text(args.new, corpus, cache)

    llm = None
    if not args.no_llm:
        if not args.tenant:
            print(
                "[warn] no --tenant given: running the deterministic path "
                "(an LLM call needs a tenant to resolve model routing)",
                file=sys.stderr,
            )
        else:
            from services.knowevo.llm_client import build_llm_callable

            llm = build_llm_callable(args.tenant)

    service = als.AlignmentService(
        args.tenant or "",
        llm=llm,
        lang=args.lang,
        max_llm_calls=args.max_llm_calls,
    )
    detect = await service.detect(
        old_text, new_text, old_asset_no=args.old, new_asset_no=args.new
    )
    counts = detect.stats["change_counts"]
    print(f"[detect] {args.old} -> {args.new}: {sum(counts.values())} changes {counts}")
    print(
        f"[detect] sections matched={len(detect.sections.matched)} "
        f"added={len(detect.sections.added)} deleted={len(detect.sections.deleted)} "
        f"moved={len(detect.sections.moved)} renumbered={len(detect.sections.renumbered)} "
        f"tables={len(detect.table_changes)}"
    )
    print(
        f"[detect] llm_calls={service.llm_calls}/{args.max_llm_calls} "
        f"parse_failures={service.parse_failures} "
        f"changed_paragraphs={ {k: len(v) for k, v in service.last_changed_texts.items()} }"
    )

    result = als.AlignmentResult(detect=detect)
    if args.tenant:
        spans: list[tuple[str, int]] = []
        for asset_no in (args.old, args.new):
            for span in service.resolve_changed_spans(asset_no):
                if span not in spans:
                    spans.append(span)
        print(f"[impact] resolved changed spans: {len(spans)} (unmatched paragraphs are omitted)")
        if spans or args.impact_only:
            result.affected = service.affected_surface(spans)
            result.update_set = service.minimal_update(
                result.affected, epsilon=args.epsilon, cost=args.cost
            )
            print(
                f"[impact] index_queries={result.affected.index_queries} "
                f"entities={len(result.affected.entities)} "
                f"relations={len(result.affected.relations)} "
                f"cards={len(result.affected.decision_cards)}"
            )
            print(
                f"[update-set] selected={len(result.update_set.selected)} "
                f"excluded={len(result.update_set.excluded)} "
                f"loss_estimate={result.update_set.loss_estimate}"
            )
    else:
        print("[impact] skipped: no --tenant (nothing was guessed)")

    negative_calibration = None
    if args.gold and not args.impact_only:
        gold_path = Path(args.gold).resolve()
        gold = parse_gold(gold_path)
        result.calibration = calibrate_loose(detect.changes, gold)
        result.topic_calibration = als.calibrate_topic(detect.changes, gold)
        calibration = result.calibration
        topic = result.topic_calibration
        # 2026-09-27 口径：UNC 行（status="negative"）= 域内阴性，进 precision
        # 轴的假阳分母（阴性集）。阴性侧用同一 calibrate_topic 匹配器计一遍：
        # 命中阴性话题的机器分组即裁决下的假阳。注意 negative_matched_groups
        # 与阳性命中的分组可能相交（同一分组可同时命中变更话题与阴性话题），
        # 这里只有计数；集合语义的精确分解见 probe_p5 的 h_in_domain_negatives。
        negatives = [g for g in gold if g.get("status") == "negative"]
        if negatives:
            shaped = [dict(g, status="verified") for g in negatives]
            neg = als.calibrate_topic(detect.changes, shaped)
            negative_calibration = {
                "ruling": "UNC = in-domain negative (2026-09-27 user ruling; batch2 P/R rule)",
                "n_negative_topics": len(negatives),
                "negative_ids": [g["id"] for g in negatives],
                "negative_topics_matched": neg.matched_topics,
                "negative_matched_groups": neg.matched_groups,
                "machine_groups": neg.machine_groups,
                "note": (
                    "machine groups matching an in-domain negative count toward "
                    "the false-positive side of the precision_lower_bound "
                    "denominator; negative_matched_groups may overlap the "
                    "positive-matched groups (counts, not disjoint sets)"
                ),
            }
        if topic.gold_total == 0:
            print(
                f"[calibration] NOT MEASURED: {topic.unverified_excluded}/"
                f"{len(gold)} gold rows are unverified"
            )
        else:
            # The topic-level numbers are the official ones (gold is topical);
            # the item-level pair is printed too so the granularity mismatch
            # stays visible instead of being quietly dropped.
            print(
                f"[calibration] recall_topic={topic.recall} "
                f"matched_topics={topic.matched_topics}/{topic.gold_total} "
                f"machine_groups={topic.machine_groups} "
                f"(from {topic.machine_total} items) "
                f"precision_lower_bound={topic.precision_lower_bound} "
                f"matched_groups={topic.matched_groups} "
                f"df<={topic.max_df_fraction} min_shared={topic.min_shared_tokens} "
                f"unverified_excluded={topic.unverified_excluded}"
            )
            print(
                f"[calibration] strict_item_level precision={calibration.precision} "
                f"recall={calibration.recall} matched={calibration.matched} "
                f"machine_items={calibration.machine_total} "
                "(granularity-mismatched baseline, not the official number)"
            )
        if negative_calibration:
            print(
                f"[calibration] in_domain_negatives={negative_calibration['n_negative_topics']} "
                f"negative_topics_matched={negative_calibration['negative_topics_matched']} "
                f"negative_matched_groups={negative_calibration['negative_matched_groups']}/"
                f"{negative_calibration['machine_groups']} "
                "(UNC unchanged topics: a machine change report on them is a false positive)"
            )

    if args.tenant and not args.impact_only and not args.no_persist:
        result.persisted = service.persist(
            detect=detect,
            affected=result.affected,
            update_set=result.update_set,
            calibration=result.calibration,
            topic_calibration=result.topic_calibration,
            knowledge_stamp={"ontology_version": args.knowledge_stamp or ""},
        )
        print(f"[persist] {result.persisted}")

    report = als.to_report_json(result)
    report["run"] = {
        "llm_enabled": bool(llm),
        "max_llm_calls": args.max_llm_calls,
        "lang": args.lang,
        "gold_file": str(args.gold) if args.gold else None,
        "mode": "impact-only" if args.impact_only else "full",
    }
    if negative_calibration:
        report["in_domain_negatives"] = negative_calibration
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[out] wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="standard aligner: diff two registered documents"
    )
    parser.add_argument("--old", required=True, help="asset_no of the older document")
    parser.add_argument("--new", required=True, help="asset_no of the newer document")
    parser.add_argument("--old-text", help="explicit text file for --old (skips registry)")
    parser.add_argument("--new-text", help="explicit text file for --new (skips registry)")
    parser.add_argument("--tenant", help="tenant UUID; enables impact analysis + persistence")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--gold", help="gold seed (.json or the L9 markdown table)")
    parser.add_argument("--out", required=True, help="report JSON output path")
    parser.add_argument("--impact-only", action="store_true",
                        help="skip gold calibration and persistence, report impact only")
    parser.add_argument("--no-llm", action="store_true", help="deterministic path only")
    parser.add_argument("--no-persist", action="store_true", help="do not write DB rows")
    parser.add_argument("--max-llm-calls", type=int, default=40,
                        help="LLM budget for STEP 3 (gateway quota is tight)")
    parser.add_argument("--epsilon", type=float, default=0.1,
                        help="quality-loss threshold for the minimal update set")
    parser.add_argument("--cost", type=float, default=1.0,
                        help="per-item cost for the VOI greedy selection")
    parser.add_argument("--lang", default="zh", choices=("zh", "en"))
    parser.add_argument("--knowledge-stamp", default="",
                        help="value recorded in ops_summary.knowledge_stamp")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.no_llm and os.environ.get("KW_FORCE_LLM") == "1":
        print("[warn] KW_FORCE_LLM=1 ignored because --no-llm was given", file=sys.stderr)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
