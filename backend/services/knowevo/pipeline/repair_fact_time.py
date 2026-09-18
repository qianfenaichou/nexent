"""One-shot idempotent fact-time backfill (T-18b D1) - three phases.

The D1 hole: ``kg_relation_t.valid_at`` was never written explicitly, so
every fact inherited the ingest wall clock (``server_default=now()``).
Ontology versions carry a ``created_at`` on that same clock, which made the
version-pinning predicate ``valid_at <= t_v`` constant true - pinned
traversal could never separate facts by knowledge time. Repair semantics
(zero DDL; the 12 domain tables are schema-frozen): ``valid_at`` becomes
the *source document's* business publication date and the version's fact
cutoff lands in ``ontology_version_t.metrics`` (existing JSONB).

Phases - each classifies every row into exactly one bucket, prints every
count, and never re-timestamps a row with an invented date:

  1. docs        registry.csv ``published_at`` -> ``doc_asset_t.metadata``
                 (read-modify-write of the whole JSONB) for rows whose
                 metadata lacks ``published_at``. Assets absent from the
                 registry (eval fixtures G20/G24/M1, stray test tenants)
                 keep their metadata and are counted (``not_in_registry``).
  2. versions    ``ontology_version_t`` rows whose ``metrics`` lacks
                 ``fact_cutoff`` get the tenant's max effective document
                 ``published_at`` as an ISO-8601 string; sibling metric
                 keys survive the read-modify-write. No traceable date ->
                 kept and counted.
  3. relations   ``kg_relation_t.valid_at`` <- the source document's
                 publication date, resolved forward (``props.evidence_id``
                 -> ``kg_evidence_t.doc_id``) or, when props carry no
                 evidence id, reverse (the relation id appears in some
                 ``kg_evidence_t.edge_ids``). Unlinked rows stay put and
                 are counted (``no_evidence_link``): the eval-fixture
                 graph has no evidence chain, so an honest all-buckets
                 ``no_evidence_link`` report is the expected outcome, not
                 a failure.

"Effective" document dates: phases 2 and 3 see the post-phase-1 state
(existing metadata values plus phase-1 planned writes), so one ``--write``
run lands exactly where the ``--dry-run`` report said, and a second
``--write`` run is a no-op (every row then falls into ``already_dated`` /
``already_set``).

Usage (from backend/, with the usual POSTGRES_* / NEXENT_POSTGRES_PASSWORD
env):
    python -m services.knowevo.pipeline.repair_fact_time --dry-run
    python -m services.knowevo.pipeline.repair_fact_time --write

``--dry-run`` is the default and writes nothing; ``--write`` applies the
planned updates and commits once. Back up the relation table first (the
report prints the statement) - this is one-shot maintenance, not pipeline
code.
"""
from __future__ import annotations

import argparse
import csv
import sys
import uuid as uuid_mod
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
REGISTRY_PATH = REPO_ROOT / "competition" / "corpus" / "registry.csv"

BACKUP_SQL = ("CREATE TABLE nexent.kg_relation_t_bak_t18b "
              "AS SELECT * FROM nexent.kg_relation_t;")


def _parse_business_date(value: object) -> datetime | None:
    """Parse a ``YYYY-MM-DD`` business date into UTC-midnight tz-aware.

    Strictly the registry format, matching
    ``ontology_service._parse_iso_date``; malformed input returns None so
    the row is counted as undated instead of backfilled with a guess.
    """
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").replace(
            tzinfo=UTC)
    except ValueError:
        return None


def _load_registry(path: Path) -> dict[str, str]:
    """``asset_no -> raw published_at`` from registry.csv ("" when unset).

    A missing ``published_at`` column degrades to every asset mapping ""
    (each doc is then honestly counted as ``registry_undated``), never to
    an invented date.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {row.get("asset_no", ""): (row.get("published_at") or "").strip()
            for row in rows}


def _plan_docs(session, registry: dict[str, str]):
    """Phase 1: which doc_asset_t rows get a registry publication date.

    Returns ``(counts, plans, effective)``: a plan is
    ``(doc, date_str, parsed)``; ``effective`` maps ``(tenant_id, doc_id)``
    to ``(parsed_date | None, undated_reason | None, asset_no)`` - the
    post-phase-1 view that phases 2 and 3 consume.
    """
    from database.knowevo_db import DocAsset

    counts = {
        "backfill": 0,
        "already_dated": 0,
        "bad_doc_date": 0,
        "not_in_registry": 0,
        "registry_undated": 0,
        "bad_registry_date": 0,
    }
    plans = []
    effective: dict = {}
    docs = (session.query(DocAsset)
            .order_by(DocAsset.asset_no, DocAsset.id).all())
    for doc in docs:
        asset_no = doc.asset_no
        existing = (doc.meta_data or {}).get("published_at")
        if existing:
            parsed = _parse_business_date(existing)
            if parsed is None:
                counts["bad_doc_date"] += 1
                state = (None, "bad_doc_date", asset_no)
            else:
                # First writer wins: an existing date is never overwritten.
                counts["already_dated"] += 1
                state = (parsed, None, asset_no)
        elif asset_no not in registry:
            counts["not_in_registry"] += 1
            state = (None, "not_in_registry", asset_no)
        else:
            raw = registry[asset_no]
            parsed = _parse_business_date(raw) if raw else None
            if raw and parsed is None:
                counts["bad_registry_date"] += 1
                state = (None, "bad_registry_date", asset_no)
            elif parsed is None:
                counts["registry_undated"] += 1
                state = (None, "registry_undated", asset_no)
            else:
                counts["backfill"] += 1
                plans.append((doc, raw, parsed))
                state = (parsed, None, asset_no)
        effective[(doc.tenant_id, doc.id)] = state
    return counts, plans, effective


def _plan_versions(session, effective: dict):
    """Phase 2: which ontology_version_t rows get a fact_cutoff.

    A plan is ``(version_row, cutoff_iso_string)``. The cutoff is the
    tenant's max *effective* document publication date, so it already
    includes phase-1 writes.
    """
    from database.knowevo_db import OntologyVersion

    counts = {"backfill": 0, "already_set": 0, "no_traceable_date": 0}
    plans = []
    latest_pub: dict = {}
    for (tenant_id, _doc_id), (parsed, _reason, _asset_no) in effective.items():
        if parsed is not None:
            current = latest_pub.get(tenant_id)
            if current is None or parsed > current:
                latest_pub[tenant_id] = parsed
    versions = (session.query(OntologyVersion)
                .order_by(OntologyVersion.created_at,
                          OntologyVersion.version).all())
    for ver in versions:
        if (ver.metrics or {}).get("fact_cutoff"):
            counts["already_set"] += 1
            continue
        latest = latest_pub.get(ver.tenant_id)
        if latest is None:
            # No tenant document carries a traceable business date: keep
            # the row as is (the created_at fallback stays the honest t_v).
            counts["no_traceable_date"] += 1
            continue
        counts["backfill"] += 1
        plans.append((ver, latest.isoformat()))
    return counts, plans


def _plan_relations(session, effective: dict):
    """Phase 3: which kg_relation_t rows get a business valid_at.

    A plan is ``(relation, old_valid_at, new_valid_at, asset_no)``. The
    source doc is resolved forward through ``props.evidence_id`` when the
    edge carries one (a broken id is reported, never silently replaced),
    otherwise through the reverse ``kg_evidence_t.edge_ids`` index.
    """
    from database.knowevo_db import KgEvidence, KgRelation

    counts = {
        "backfill": 0,
        "already_dated": 0,
        "no_evidence_link": 0,
        "bad_evidence_id": 0,
        "evidence_missing": 0,
        "no_doc": 0,
        "not_in_registry": 0,
        "registry_undated": 0,
        "bad_doc_date": 0,
        "bad_registry_date": 0,
    }
    plans = []
    forward: dict = {}
    reverse: dict = {}
    for ev in session.query(KgEvidence).all():
        forward[(ev.tenant_id, ev.id)] = ev.doc_id
        for edge_id in (ev.edge_ids or []):
            reverse[(ev.tenant_id, edge_id)] = ev.doc_id

    relations = (session.query(KgRelation)
                 .order_by(KgRelation.created_at, KgRelation.id).all())
    for rel in relations:
        forward_fail = None
        doc_id = None
        evidence_id = (rel.props or {}).get("evidence_id")
        if evidence_id:
            try:
                doc_id = forward.get(
                    (rel.tenant_id, uuid_mod.UUID(str(evidence_id))))
            except ValueError:
                forward_fail = "bad_evidence_id"
            if doc_id is None and forward_fail is None:
                forward_fail = "evidence_missing"
        if doc_id is None and not evidence_id:
            doc_id = reverse.get((rel.tenant_id, rel.id))
        if doc_id is None:
            counts[forward_fail or "no_evidence_link"] += 1
            continue
        eff = effective.get((rel.tenant_id, doc_id))
        if eff is None:
            counts["no_doc"] += 1
            continue
        parsed, reason, asset_no = eff
        if parsed is None:
            counts[reason] += 1
            continue
        if rel.valid_at == parsed:
            counts["already_dated"] += 1
            continue
        counts["backfill"] += 1
        plans.append((rel, rel.valid_at, parsed, asset_no))
    return counts, plans


def _print_doc_plans(heading: str, plans) -> None:
    if not plans:
        return
    print(f"{heading}:")
    for doc, date_str, _parsed in plans:
        print(f"  doc {doc.asset_no} [tenant {str(doc.tenant_id)[:8]}] "
              f"metadata.published_at -> {date_str}")


def _print_version_plans(heading: str, plans) -> None:
    if not plans:
        return
    print(f"{heading}:")
    for ver, cutoff in plans:
        print(f"  version {ver.version} [tenant {str(ver.tenant_id)[:8]}] "
              f"metrics.fact_cutoff -> {cutoff}")


def _print_relation_plans(heading: str, plans) -> None:
    if not plans:
        return
    print(f"{heading}:")
    for rel, old, new, asset_no in plans:
        print(f"  relation {rel.id} [tenant {str(rel.tenant_id)[:8]}] "
              f"valid_at {old.isoformat()} -> {new.isoformat()} "
              f"via doc {asset_no}")


def _print_counts(label: str, counts: dict[str, int]) -> None:
    print(f"{label} scanned: {sum(counts.values())}")
    for name, count in counts.items():
        print(f"  {name}: {count}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill fact business time (T-18b D1; one-shot, "
                    "idempotent, three phases)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="report only, write nothing (default)")
    parser.add_argument("--write", action="store_true",
                        help="apply the planned updates (dry-run first!)")
    parser.add_argument("--registry", default=str(REGISTRY_PATH))
    args = parser.parse_args(argv)

    registry = _load_registry(Path(args.registry))

    from database.knowevo_db import _get_db_session

    with _get_db_session() as session:
        doc_counts, doc_plans, effective = _plan_docs(session, registry)
        ver_counts, ver_plans = _plan_versions(session, effective)
        rel_counts, rel_plans = _plan_relations(session, effective)

        print(f"mode: {'write' if args.write else 'dry-run (no writes)'}")
        _print_counts("[1/3] doc_asset_t", doc_counts)
        _print_counts("[2/3] ontology_version_t", ver_counts)
        _print_counts("[3/3] kg_relation_t", rel_counts)

        if args.write:
            for doc, date_str, _parsed in doc_plans:
                # Read-modify-write the whole JSONB: any sibling metadata
                # keys must survive the published_at add.
                meta = dict(doc.meta_data or {})
                meta["published_at"] = date_str
                doc.meta_data = meta
            for ver, cutoff in ver_plans:
                # Same for version metrics: cov/red/dep/align survive.
                metrics = dict(ver.metrics or {})
                metrics["fact_cutoff"] = cutoff
                ver.metrics = metrics
            for rel, _old, new, _asset_no in rel_plans:
                rel.valid_at = new
            session.commit()
            print(f"committed: {len(doc_plans)} doc rows, "
                  f"{len(ver_plans)} version rows, "
                  f"{len(rel_plans)} relation rows")
            _print_doc_plans("applied docs (before -> after)", doc_plans)
            _print_version_plans("applied versions (before -> after)",
                                 ver_plans)
            _print_relation_plans("applied relations (before -> after)",
                                  rel_plans)
        else:
            _print_doc_plans("planned doc updates", doc_plans)
            _print_version_plans("planned version updates", ver_plans)
            _print_relation_plans("planned relation updates", rel_plans)

    print("\nSAFETY: back up before a real --write run:")
    print(f"  {BACKUP_SQL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
