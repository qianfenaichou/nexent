"""Derive registry.csv ``published_at`` (business publication date) - T-18b D1.

The D1 hole: every ``kg_relation_t.valid_at`` was the ingest wall clock
(``server_default=now()``), so a fact's business time was indistinguishable
from the ontology version's ``created_at`` and version pinning could not
separate anything. ``valid_at`` must become the *source document's*
publication date, which means the registry needs that date, derived from
evidence that is already recorded - never invented.

Derivation rules, most specific first (each row's applied rule is printed
so the mapping is auditable):

  R1 explicit_date   license_note carries a full or year-month date
                     (e.g. "2021-04-19发布", "（2026-01修订版）") ->
                     that date (year-month -> day 01, a documented
                     convention for "the month of publication").
  R2 journal_issue   license_note cites a journal issue
                     ("中华糖尿病杂志2021;13(4)") -> YYYY-MM-01: the issue
                     month is the publication month. (guide-2020 -> the
                     2021;13(4) issue -> 2021-04-01, per the T-18b brief.)
  R3 doc_number_year license_note carries a government document number
                     ("国卫办医函〔2016〕1315号") -> YYYY-01-01: the year is
                     documented, the exact day is not, and a January 1
                     convention is the conservative floor for a same-year
                     fact.
  R4 asset_no_year   the asset number ends in the publication year
                     ("cp-t2dm-2009", "edu-insulin-2022") -> YYYY-01-01.
                     The corpus registration recorded the year; the day is
                     a convention floor.
  R5 none            nothing traceable -> NULL. The value is never guessed:
                     undated documents keep their ingest time at backfill
                     time and are counted in the repair report.

Usage (from backend/):
    python -m services.knowevo.pipeline.derive_published_at --dry-run
    python -m services.knowevo.pipeline.derive_published_at --write
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
REGISTRY_PATH = REPO_ROOT / "competition" / "corpus" / "registry.csv"

# R1: 2021-04-19 / 2026-01 / 2023-11 (the surrounding text says 发布/修订版)
_EXPLICIT_DATE = re.compile(r"(20\d{2})-(\d{2})(?:-(\d{2}))?")
# R2: journal citation ...2021;13(4) or 2022;61(3)
_JOURNAL_ISSUE = re.compile(r"(20\d{2});\s*\d+\s*\(\s*(\d{1,2})\s*\)")
# R3: government document number 〔2016〕1315号 / [2016]1315
_DOC_NUMBER = re.compile(r"[〔\[（(]\s*(20\d{2})\s*[〕\]）)]")
# R4: trailing year in the asset number
_ASSET_YEAR = re.compile(r"-(20\d{2})$")

PUBLISHED_AT_COLUMN = "published_at"


def derive_one(asset_no: str, license_note: str) -> tuple[str, str]:
    """Return ``(published_at, rule)`` for one registry row.

    ``published_at`` is ``"YYYY-MM-DD"`` or ``""`` (untraceable). The rule
    tag names which derivation applied, so a reviewer can re-derive any row
    by hand from the license note.
    """
    note = license_note or ""
    if "发布" in note or "修订版" in note:
        match = _EXPLICIT_DATE.search(note)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month}-{day or '01'}", "explicit_date"
    match = _JOURNAL_ISSUE.search(note)
    if match:
        year, issue = match.groups()
        return f"{year}-{int(issue):02d}-01", "journal_issue"
    match = _DOC_NUMBER.search(note)
    if match:
        return f"{match.group(1)}-01-01", "doc_number_year"
    match = _ASSET_YEAR.search(asset_no or "")
    if match:
        return f"{match.group(1)}-01-01", "asset_no_year"
    return "", "none"


def build_rows(rows: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    """``[(asset_no, published_at, rule)]`` - the derivation table."""
    out = []
    for row in rows:
        published, rule = derive_one(row.get("asset_no", ""),
                                     row.get("license_note", ""))
        out.append((row.get("asset_no", ""), published, rule))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive registry.csv published_at from recorded evidence")
    parser.add_argument("--registry", default=str(REGISTRY_PATH))
    parser.add_argument("--write", action="store_true",
                        help="rewrite registry.csv adding the column")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the derivation table only (default)")
    args = parser.parse_args(argv)

    path = Path(args.registry)
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    derived = build_rows(rows)
    counts: dict[str, int] = {}
    for _, published, rule in derived:
        counts[rule] = counts.get(rule, 0) + 1
        print(f"{derived.index((_, published, rule)) if False else ''}"
              f"{_}|{published or '-'}|{rule}")
    print(f"\nrule counts: {counts}")
    print(f"dated: {sum(1 for _, p, _ in derived if p)}/{len(derived)}")

    if not args.write:
        return 0

    if PUBLISHED_AT_COLUMN in header:
        print(f"column {PUBLISHED_AT_COLUMN} already present; rewriting values")
    else:
        header.append(PUBLISHED_AT_COLUMN)
    published_by_asset = {a: p for a, p, _ in derived}
    for row in rows:
        row[PUBLISHED_AT_COLUMN] = published_by_asset.get(
            row.get("asset_no", ""), "")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        # LF (not csv's default CRLF): the repo stores registry.csv with LF
        # and a CRLF rewrite would make every line look changed in review.
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
