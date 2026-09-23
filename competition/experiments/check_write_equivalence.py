#!/usr/bin/env python3
"""A/B write-path equivalence check: batched store vs HEAD row-by-row store.

WHY THIS EXISTS
---------------
``bench_write_path.py`` measures how *fast* the batched rewrite of
``PgJsonbGraphStore.upsert_entities`` / ``upsert_relations`` is. It records only
timings and the row count is never asserted, so a 54x speedup would look just as
good if the batched path silently dropped rows, duplicated them, or mis-handled
the merge/claim branches. Speed without equivalence is worthless, so this script
answers the question the benchmark cannot:

    **does the batched writer produce byte-identical table state to the
    row-by-row writer it replaced?**

Method
------
Both arms consume the *same* deterministic dataset (``build_graph`` with a fixed
seed) into *fresh, distinct tenants*, then the meaningful columns are compared as
canonicalised multisets. Columns that are auto-populated by the database
(``id``, ``tenant_id``, ``created_at``, and the ``valid_at`` server default) are
excluded from the bulk comparison -- but a dedicated case writes *explicit*
``valid_at`` values so that path is compared exactly rather than assumed.

Cases exercised (these are the semantics the batch rewrite claims to preserve):

  A. bulk all-new insert                 -- the hot path
  B. idempotent rerun                    -- same rows twice must be a no-op
  C. merge branch                        -- same key, extra props + new alias
  D. claim-change branch (relations)     -- same key, different claim -> new row
  E. in-batch duplicate keys             -- the "working copy" logic
  F. explicit valid_at honoured on insert

Usage::

    source /home/qianqian/pg-env.sh
    cd /home/qianqian/Work/All/Nexent/nexent
    backend/.venv/bin/python competition/experiments/check_write_equivalence.py

Exit code 0 = all cases equivalent; 1 = at least one case differs (and the
differing rows are printed). No LLM, no network. Requires PostgreSQL.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
if EXPERIMENTS_DIR not in sys.path:
    sys.path.insert(0, EXPERIMENTS_DIR)

import bench_write_path as bwp  # noqa: E402  (needs the path insert above)

bwp._ensure_importable()

from backend.services.knowevo.graph_store import PgJsonbGraphStore  # noqa: E402
from database.knowevo_db import KgEntity, KgRelation, _get_db_session  # noqa: E402

BATCH_SIZE = 1000


# ---------------------------------------------------------------------------
# canonical snapshots
# ---------------------------------------------------------------------------

def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def snapshot_entities(tenant_id: str) -> list[tuple]:
    """Meaningful entity columns, canonicalised, sorted.

    ``valid_at`` is excluded here because the dataset does not set it and the
    column defaults to ``now()``, which necessarily differs between the two
    arms. The explicit-valid_at case (F) compares it directly instead.
    """
    with _get_db_session() as session:
        rows = (
            session.query(
                KgEntity.stable_id, KgEntity.name, KgEntity.class_ref,
                KgEntity.status, KgEntity.props, KgEntity.aliases,
                KgEntity.embedding,
            )
            .filter(KgEntity.tenant_id == tenant_id)
            .all()
        )
    return sorted(
        (r.stable_id, r.name, r.class_ref, r.status,
         _canon(r.props), _canon(r.aliases), _canon(r.embedding))
        for r in rows
    )


def snapshot_relations(tenant_id: str) -> list[tuple]:
    with _get_db_session() as session:
        rows = (
            session.query(
                KgRelation.src, KgRelation.dst, KgRelation.rel_type,
                KgRelation.claim, KgRelation.props, KgRelation.contested,
            )
            .filter(KgRelation.tenant_id == tenant_id)
            .all()
        )
    return sorted(
        (r.src, r.dst, r.rel_type, r.claim, _canon(r.props), bool(r.contested))
        for r in rows
    )


def snapshot_explicit_valid_at(tenant_id: str) -> list[tuple]:
    """Entity/relation ``valid_at`` compared exactly (case F)."""
    out: list[tuple] = []
    with _get_db_session() as session:
        for r in session.query(KgEntity.stable_id, KgEntity.valid_at).filter(
            KgEntity.tenant_id == tenant_id
        ).all():
            out.append(("E", r.stable_id, r.valid_at.isoformat() if r.valid_at else None))
        for r in session.query(
            KgRelation.src, KgRelation.dst, KgRelation.rel_type,
            KgRelation.claim, KgRelation.valid_at,
        ).filter(KgRelation.tenant_id == tenant_id).all():
            out.append(("R", f"{r.src}->{r.dst}:{r.rel_type}:{r.claim}",
                        r.valid_at.isoformat() if r.valid_at else None))
    return sorted(out)


# ---------------------------------------------------------------------------
# arms
# ---------------------------------------------------------------------------

async def write_batched(tenant_id: str, ents, rels) -> None:
    store = PgJsonbGraphStore()
    store.batch_size = BATCH_SIZE
    await store.upsert_entities(tenant_id, ents)
    await store.upsert_relations(tenant_id, rels)


async def write_legacy(tenant_id: str, ents, rels) -> None:
    _mod, LegacyStore = bwp._load_legacy_store()
    store = LegacyStore()
    await store.upsert_entities(tenant_id, ents)
    await store.upsert_relations(tenant_id, rels)


def _t() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------

async def case_a_bulk(ents, rels) -> tuple[bool, str]:
    tl, tb = _t(), _t()
    await write_legacy(tl, ents, rels)
    await write_batched(tb, ents, rels)
    el, eb = snapshot_entities(tl), snapshot_entities(tb)
    rl, rb = snapshot_relations(tl), snapshot_relations(tb)
    ok = el == eb and rl == rb and len(el) == len(ents) and len(rl) == len(rels)
    detail = (f"entities legacy={len(el)} batched={len(eb)} (expected {len(ents)}); "
              f"relations legacy={len(rl)} batched={len(rb)} (expected {len(rels)})")
    if not ok:
        detail += f"\n  entity diff sample: {_first_diff(el, eb)}" \
                  f"\n  relation diff sample: {_first_diff(rl, rb)}"
    return ok, detail


async def case_b_idempotent(ents, rels) -> tuple[bool, str]:
    """Same payload written twice must leave the table unchanged (arm-local)."""
    tl, tb = _t(), _t()
    await write_legacy(tl, ents, rels)
    await write_legacy(tl, ents, rels)
    await write_batched(tb, ents, rels)
    await write_batched(tb, ents, rels)
    el, eb = snapshot_entities(tl), snapshot_entities(tb)
    rl, rb = snapshot_relations(tl), snapshot_relations(tb)
    ok = el == eb and rl == rb and len(el) == len(ents) and len(rl) == len(rels)
    detail = (f"after 2 identical writes: entities legacy={len(el)} batched={len(eb)}; "
              f"relations legacy={len(rl)} batched={len(rb)}")
    return ok, detail


async def case_c_merge(ents, rels) -> tuple[bool, str]:
    """Second payload touches the same keys with extra props + a new alias."""
    tl, tb = _t(), _t()
    await write_legacy(tl, ents, rels)
    await write_batched(tb, ents, rels)

    patch = [
        {"stable_id": e["stable_id"],
         "props": {"patched": True, "weight": 0.5},
         "aliases": [{"alias": "extra_alias", "type": "patch"}]}
        for e in ents[:200]
    ]
    await write_legacy(tl, patch, [])
    await write_batched(tb, patch, [])
    el, eb = snapshot_entities(tl), snapshot_entities(tb)
    ok = el == eb
    detail = f"after merge patch: entities legacy={len(el)} batched={len(eb)}"
    if not ok:
        detail += f"\n  diff sample: {_first_diff(el, eb)}"
    return ok, detail


async def case_d_claim_change(ents, rels) -> tuple[bool, str]:
    """Same (src,dst,rel_type) with a DIFFERENT claim must insert a new row."""
    tl, tb = _t(), _t()
    await write_legacy(tl, ents[:100], rels)
    await write_batched(tb, ents[:100], rels)

    changed = [dict(r, claim="CHANGED_CLAIM") for r in rels[:50]]
    await write_legacy(tl, [], changed)
    await write_batched(tb, [], changed)
    rl, rb = snapshot_relations(tl), snapshot_relations(tb)
    ok = rl == rb
    detail = (f"after claim change on 50 keys: relations legacy={len(rl)} "
              f"batched={len(rb)} (each changed key should be present twice, old+new)")
    if not ok:
        detail += f"\n  diff sample: {_first_diff(rl, rb)}"
    return ok, detail


async def case_e_in_batch_duplicates(ents, rels) -> tuple[bool, str]:
    """The same key twice *within one call* -- exercises the working-copy logic."""
    tl, tb = _t(), _t()
    dupes = [
        {"stable_id": "dup-1", "name": "first", "props": {"a": 1}, "aliases": [{"alias": "x"}]},
        {"stable_id": "dup-1", "name": "second", "props": {"b": 2}, "aliases": [{"alias": "y"}]},
        {"stable_id": "dup-2", "name": "only", "props": {"c": 3}},
    ]
    await write_legacy(tl, dupes, [])
    await write_batched(tb, dupes, [])
    el, eb = snapshot_entities(tl), snapshot_entities(tb)
    ok = el == eb
    detail = f"in-batch duplicate keys: entities legacy={len(el)} batched={len(eb)}"
    if not ok:
        detail += (f"\n  legacy : {el}\n  batched: {eb}")
    return ok, detail


async def case_f_explicit_valid_at(ents, rels) -> tuple[bool, str]:
    """Explicit valid_at must be honoured identically by both arms."""
    ts = datetime(2022, 3, 1, 12, 0, tzinfo=timezone.utc)
    e_sub = [dict(e, valid_at=ts) for e in ents[:50]]
    r_sub = [dict(r, valid_at=ts) for r in rels[:50]]
    tl, tb = _t(), _t()
    await write_legacy(tl, e_sub, r_sub)
    await write_batched(tb, e_sub, r_sub)
    vl, vb = snapshot_explicit_valid_at(tl), snapshot_explicit_valid_at(tb)
    ok = vl == vb and len(vl) == 100
    detail = f"explicit valid_at: rows legacy={len(vl)} batched={len(vb)} (expected 100)"
    if not ok:
        detail += f"\n  diff sample: {_first_diff(vl, vb)}"
    return ok, detail


def _first_diff(a, b, n: int = 3):
    sa, sb = set(map(repr, a)), set(map(repr, b))
    only_a = list(sa - sb)[:n]
    only_b = list(sb - sa)[:n]
    return f"only-legacy={only_a} only-batched={only_b}"


CASES = [
    ("A bulk all-new insert", case_a_bulk),
    ("B idempotent rerun", case_b_idempotent),
    ("C merge branch", case_c_merge),
    ("D claim-change branch", case_d_claim_change),
    ("E in-batch duplicate keys", case_e_in_batch_duplicates),
    ("F explicit valid_at", case_f_explicit_valid_at),
]


async def main() -> int:
    import argparse

    labels = [label for label, _ in CASES]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entities", type=int, default=2000)
    parser.add_argument("--edges", type=int, default=3000)
    parser.add_argument("--cases", default="",
                        help=f"comma-separated subset of: {','.join(labels[0][0] + '..' + labels[-1][0])} "
                             f"(default: all). Labels: {'; '.join(labels)}")
    args = parser.parse_args()

    selected = CASES
    if args.cases:
        wanted = {c.strip().upper() for c in args.cases.split(",") if c.strip()}
        selected = [(lbl, fn) for lbl, fn in CASES if lbl.split()[0].upper() in wanted]
        if not selected:
            parser.error(f"--cases matched nothing; labels: {'; '.join(labels)}")

    n_ent, n_rel = args.entities, args.edges
    print("== write-path A/B equivalence: batched vs HEAD row-by-row ==")
    print(f"   dataset: build_graph({n_ent}, {n_rel}) seed={bwp.SEED}; batch_size={BATCH_SIZE}")
    print()
    ents, rels = bwp.build_graph(n_ent, n_rel)

    results = []
    for label, fn in selected:
        try:
            ok, detail = await fn(ents, rels)
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, not a crash
            ok, detail = False, f"EXCEPTION {type(exc).__name__}: {exc}"
        results.append((label, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"         {detail}")

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"  => {passed}/{len(results)} cases equivalent")
    if passed != len(results):
        print("  => NOT equivalent: the batched writer does not reproduce the "
              "row-by-row writer's table state. Do not claim the speedup.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
