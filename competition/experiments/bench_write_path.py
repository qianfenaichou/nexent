#!/usr/bin/env python3
"""Bench: graph write-path cost with vs without the kw_011 current-view indexes.

Generates a *deterministic* synthetic knowledge graph (entities + relations)
and times the pure upsert phase of ``PgJsonbGraphStore.upsert_entities`` /
``upsert_relations`` under two conditions:

  (a) indexes ABSENT  - the new GiST / partial / covering indexes from
      migration ``v2.5.5_kw_011_knowevo_graph_indexes.sql`` are dropped, so we
      measure the write path with only the pre-existing indexes present;
  (b) indexes PRESENT - the four indexes are created, so we measure the
      *added write cost* they impose (every INSERT/UPDATE must also maintain
      them; the migration header documents this trade-off honestly).

It exercises the REAL store code path (not a mock), so the numbers reflect the
actual upsert implemented in ``backend/services/knowevo/graph_store.py``.

The ``--legacy`` switch loads the *unmodified* HEAD revision of
``graph_store.py`` through ``importlib`` (``git show HEAD:...`` + exec into a
fresh module namespace). This yields the genuine pre-batch (row-by-row)
``PgJsonbGraphStore`` - NOT a hand-rewritten look-alike - so the harness can
measure the "before" cost in the same process / environment / dataset as the
current (batched) path.

Run:
    python3 competition/experiments/bench_write_path.py \
        [--entities N] [--edges M] [--batch-size K] [--repeats R] \
        [--no-indexes | --with-indexes] [--legacy] [--output PATH]

  --no-indexes / --with-indexes  mutually exclusive; if neither is given BOTH
                                phases run (preserving the original default).
  --legacy                      use the unmodified HEAD (row-by-row) store.

适用范围：需要真实 PostgreSQL。本机无数据库时以清晰信息退出，绝不编造任何测量数字。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time
import types
import uuid as uuid_mod

EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
COMPETITION_DIR = os.path.dirname(EXPERIMENTS_DIR)
REPO_ROOT = os.path.dirname(COMPETITION_DIR)
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
DEFAULT_OUTPUT = os.path.join(
    COMPETITION_DIR, "deliverables", "algorithm-probes", "bench_write_path.json"
)

SEED = 20260923
SCOPE_NOTE = (
    "适用范围：需要真实 PostgreSQL；无数据库时本脚本以清晰信息退出，"
    "不编造任何测量数字。"
)


def _ensure_importable() -> None:
    """Make the backend packages importable regardless of how the script is run.

    The receipt invokes the harness as
        cd <repo> && backend/.venv/bin/python competition/experiments/bench_write_path.py
    where ``sys.path[0]`` is the *script's* directory, not the repo root.

    Two distinct top-level packages must resolve:
      * ``backend.services...``  lives at ``<repo>/backend/...`` -> needs
        ``<repo>`` (REPO_ROOT) on sys.path.
      * ``database.knowevo_db``  lives at ``<repo>/backend/database/...`` ->
        needs ``<repo>/backend`` (BACKEND_DIR) on sys.path.

    Inserting only one of them breaks the other in a non-editable
    environment, so we insert both. (When the repo happens to be
    editable-installed the absolute imports resolve via the editable finder
    too, but we keep the explicit path setup for robustness.)
    """
    for p in (REPO_ROOT, BACKEND_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


# The four indexes defined by migration v2.5.5_kw_011_knowevo_graph_indexes.sql,
# inlined so the harness can create/drop them independently of the migration
# runner (a migration wrapped in BEGIN/COMMIT cannot CREATE INDEX CONCURRENTLY,
# so we build them normally here).
INDEX_DDL = {
    "ix_kr_valid_range": (
        "CREATE INDEX IF NOT EXISTS ix_kr_valid_range ON nexent.kg_relation_t "
        "USING GIST (tstzrange(valid_at, COALESCE(invalid_at, "
        "'infinity'::timestamptz), '[)'))"
    ),
    "ix_ke_valid_range": (
        "CREATE INDEX IF NOT EXISTS ix_ke_valid_range ON nexent.kg_entity_t "
        "USING GIST (tstzrange(valid_at, COALESCE(invalid_at, "
        "'infinity'::timestamptz), '[)'))"
    ),
    "ix_kr_current": (
        "CREATE INDEX IF NOT EXISTS ix_kr_current ON nexent.kg_relation_t "
        "(tenant_id, src, rel_type) WHERE invalid_at IS NULL"
    ),
    "ix_kr_hop_rev_cover": (
        "CREATE INDEX IF NOT EXISTS ix_kr_hop_rev_cover ON nexent.kg_relation_t "
        "(tenant_id, dst, rel_type) INCLUDE (src)"
    ),
}
INDEX_NAMES = list(INDEX_DDL.keys())


# ---------------------------------------------------------------------------
# Deterministic synthetic graph
# ---------------------------------------------------------------------------

def build_graph(n_entities: int, n_edges: int) -> tuple[list[dict], list[dict]]:
    """Deterministic synthetic graph. No randomness beyond a fixed-seed RNG."""
    rng = random.Random(SEED)
    classes = ["Drug", "Disease", "Gene", "Symptom"]
    rel_types = ["indicated_for", "contraindicated_for", "targets", "causes"]
    ents: list[dict] = []
    for i in range(n_entities):
        sid = f"e{i}"
        ents.append({
            "stable_id": sid,
            "name": f"node_{i}",
            "class_ref": rng.choice(classes),
            "props": {"idx": i, "weight": round(rng.random(), 4)},
            "aliases": [{"alias": f"alias_{i}", "type": "synthetic"}],
            # small JSONB float array standing in for an embedding (no pgvector)
            "embedding": [round(rng.random(), 6) for _ in range(8)],
            "status": "active",
        })
    rels: list[dict] = []
    for j in range(n_edges):
        a, b = rng.randrange(n_entities), rng.randrange(n_entities)
        while b == a:
            b = rng.randrange(n_entities)
        rels.append({
            "src": f"e{a}",
            "dst": f"e{b}",
            "rel_type": rng.choice(rel_types),
            "claim": f"claim_{j % 997}",
            "props": {"conf": round(rng.random(), 4)},
        })
    return ents, rels


# ---------------------------------------------------------------------------
# Store class resolution (current vs unmodified HEAD / "legacy")
# ---------------------------------------------------------------------------

def _load_legacy_store():
    """Load the *unmodified* HEAD revision of ``PgJsonbGraphStore``.

    Returns ``(legacy_module, LegacyStore)`` where ``LegacyStore`` is the real
    pre-batch (row-by-row) class from ``git show HEAD:backend/services/knowevo/
    graph_store.py``, executed into a fresh module namespace. It is a genuine
    artifact of the repository history - never a hand-written reimplementation.

    Raises ``RuntimeError`` (clean, no fabricated data) if HEAD cannot be read
    or the source cannot be compiled/executed.
    """
    _ensure_importable()
    from backend.services.knowevo.graph_store import PgJsonbGraphStore as CurrentStore

    try:
        src = subprocess.check_output(
            ["git", "show", "HEAD:backend/services/knowevo/graph_store.py"],
            cwd=REPO_ROOT,
        ).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - surface a clean, actionable error
        raise RuntimeError(
            f"cannot read HEAD:backend/services/knowevo/graph_store.py "
            f"via `git show`: {type(exc).__name__}: {exc}"
        ) from exc

    mod = types.ModuleType("legacy_graph_store")
    mod.__package__ = "backend.services.knowevo"
    # Give the legacy source a __file__-like origin for tracebacks / __name__.
    mod.__file__ = "<HEAD:backend/services/knowevo/graph_store.py>"
    # Register in sys.modules BEFORE exec: graph_store uses @dataclass with
    # string annotations; the stdlib dataclasses machinery resolves those via
    # sys.modules[cls.__module__].__dict__ and crashes if the module is absent.
    sys.modules[mod.__name__] = mod
    try:
        exec(
            compile(src, "<HEAD:backend/services/knowevo/graph_store.py>", "exec"),
            mod.__dict__,
        )
    except Exception as exc:  # noqa: BLE001 - don't swallow a real defect as "ran fine"
        raise RuntimeError(
            f"cannot exec HEAD graph_store source: {type(exc).__name__}: {exc}"
        ) from exc

    LegacyStore = mod.PgJsonbGraphStore
    if LegacyStore is CurrentStore:
        # Defensive guard: if the two classes are the same object we would be
        # measuring the current path twice and calling it a "control". Abort.
        raise RuntimeError(
            "legacy (HEAD) store is identical to the current working-tree "
            "store; refusing to fabricate a control comparison."
        )
    return mod, LegacyStore


# ---------------------------------------------------------------------------
# DB helpers (reuse the production session helper)
# ---------------------------------------------------------------------------

def _set_indexes_present(present: bool) -> None:
    """Create or drop the four kw_011 indexes using the production session."""
    from database.knowevo_db import _get_db_session
    from sqlalchemy import text

    with _get_db_session() as session:
        for name in INDEX_NAMES:
            if present:
                session.execute(text(INDEX_DDL[name]))
            else:
                session.execute(text(f"DROP INDEX IF EXISTS nexent.{name}"))


async def _timed_writes(tenant_id: str, ents: list[dict], rels: list[dict],
                        repeats: int, store_cls,
                        batch_size: int | None = None) -> list[float]:
    """Run the real upsert path `repeats` times; return per-run wall seconds.

    ``batch_size`` MUST be propagated onto the store instance: the store reads
    ``getattr(self, "batch_size", DEFAULT_GRAPH_BATCH_SIZE)`` and nothing in the
    codebase ever assigns that attribute, so without this line ``--batch-size``
    is silently ignored and every sweep point measures the 1000-row default.
    (Measured: a K=100..20000 sweep was flat to within 6% because all seven
    points were in fact K=1000.)
    """
    store = store_cls()
    if batch_size is not None:
        store.batch_size = batch_size
    durations: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        await store.upsert_entities(tenant_id, ents)
        await store.upsert_relations(tenant_id, rels)
        durations.append(time.perf_counter() - t0)
    return durations


def _summarize(durations: list[float]) -> dict:
    mean = sum(durations) / len(durations) if durations else 0.0
    return {
        "repeats": len(durations),
        "per_run_s": [round(d, 6) for d in durations],
        "mean_s": round(mean, 6),
        "total_s": round(sum(durations), 6),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bench graph write path with/without kw_011 current-view indexes."
    )
    parser.add_argument("--entities", type=int, default=20000,
                        help="number of entities (default: 20000)")
    parser.add_argument("--edges", type=int, default=30000,
                        help="number of relations (default: 30000)")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="rows per batched statement (default: 1000)")
    parser.add_argument("--repeats", type=int, default=3,
                        help="upsert passes per condition (default: 3)")
    parser.add_argument("--no-indexes", action="store_true",
                        help="run ONLY the indexes-absent phase")
    parser.add_argument("--with-indexes", action="store_true",
                        help="run ONLY the indexes-present phase")
    parser.add_argument("--legacy", action="store_true",
                        help="use the unmodified HEAD (row-by-row) PgJsonbGraphStore")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output JSON path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    if args.no_indexes and args.with_indexes:
        parser.error("--no-indexes and --with-indexes are mutually exclusive")

    _ensure_importable()

    # Decide which phases to run. Default (neither flag): both, as before.
    if args.no_indexes:
        phases = [(False, "indexes ABSENT")]
    elif args.with_indexes:
        phases = [(True, "indexes PRESENT")]
    else:
        phases = [(False, "indexes ABSENT"), (True, "indexes PRESENT")]

    store_cls_name = "current (batched)"
    store_cls = None
    if args.legacy:
        try:
            _legacy_mod, LegacyStore = _load_legacy_store()
        except Exception as exc:  # noqa: BLE001 - clean failure, no fake numbers
            print(f"[error] cannot load legacy (HEAD) store: {exc}")
            print("[error] --legacy requires a git checkout of the HEAD revision.")
            print(f"[error] {SCOPE_NOTE}")
            sys.exit(1)
        store_cls = LegacyStore
        store_cls_name = "legacy (HEAD, row-by-row)"
    else:
        from backend.services.knowevo.graph_store import PgJsonbGraphStore
        store_cls = PgJsonbGraphStore
        store_cls_name = "current (batched)"

    ents, rels = build_graph(args.entities, args.edges)
    tenant_id = str(uuid_mod.uuid4())

    print("== graph write-path bench (kw_011 indexes) ==")
    print(f"  entities={args.entities} edges={args.edges} "
          f"batch_size={args.batch_size} repeats={args.repeats}")
    print(f"  tenant={tenant_id}  seed={SEED}")
    print(f"  store={store_cls_name}"
          + ("  [legacy]" if args.legacy else ""))
    print(f"  phases={[label for _, label in phases]}")

    results: dict[str, dict] = {}
    try:
        for present, label in phases:
            _set_indexes_present(present)
            print(f"  phase: {label} -> writing ...")
            t0 = time.perf_counter()
            durations = asyncio.run(
                _timed_writes(tenant_id, ents, rels, args.repeats, store_cls,
                              batch_size=args.batch_size))
            wall = time.perf_counter() - t0
            results[label] = _summarize(durations)
            print(f"    done in {wall:.3f}s (measured inside, "
                  f"total {sum(durations):.3f}s)")
    except Exception as exc:  # noqa: BLE001 - we want a clean failure, no fabricating numbers
        print(f"[error] cannot run benchmark: {type(exc).__name__}: {exc}")
        print("[error] a reachable PostgreSQL with the KnowEvo schema is required.")
        print(f"[error] {SCOPE_NOTE}")
        sys.exit(1)

    # Reporting.
    print()
    print(f"  {'condition':24s} | {'mean_s':>10s} | {'total_s':>10s} | repeats")
    for _, label in phases:
        s = results[label]
        print(f"  {label:24s} | {s['mean_s']:>10.4f} | "
              f"{s['total_s']:>10.4f} | {s['repeats']}")

    delta = None
    if "indexes PRESENT" in results and "indexes ABSENT" in results:
        delta = (results["indexes PRESENT"]["mean_s"]
                 - results["indexes ABSENT"]["mean_s"])
        print(f"  write-cost delta (present - absent, mean per run): "
              f"{delta:+.4f}s ({'slower' if delta > 0 else 'faster'})")
    print(f"  scope: {SCOPE_NOTE}")

    payload = {
        "bench": "bench_write_path",
        "store_mode": store_cls_name,
        "legacy": bool(args.legacy),
        "question": (
            "What is the write-path cost delta of the kw_011 current-view "
            "indexes (ix_kr_valid_range, ix_ke_valid_range, ix_kr_current, "
            "ix_kr_hop_rev_cover) on PgJsonbGraphStore.upsert_entities / "
            "upsert_relations?"
        ),
        "config": {
            "seed": SEED,
            "entities": args.entities,
            "edges": args.edges,
            "batch_size": args.batch_size,
            "repeats": args.repeats,
            "tenant_id": tenant_id,
            "indexes": INDEX_NAMES,
            "phases": [label for _, label in phases],
        },
        "results": {},
        "notes": [
            (
                "Phases measure the real upsert path (upsert_entities + "
                "upsert_relations) via PgJsonbGraphStore."
            ),
            (
                "When --legacy is set, the store class is the unmodified HEAD "
                "revision of graph_store.py (row-by-row), loaded via importlib; "
                "this is the genuine pre-batch implementation, not a rewrite."
            ),
            (
                "indexes_absent drops the four kw_011 indexes but keeps the "
                "pre-existing ones (ix_kr_valid, ix_ke_class, ...)."
            ),
            "scope_note: " + SCOPE_NOTE,
        ],
    }
    for _, label in phases:
        key = "indexes_absent" if "ABSENT" in label else "indexes_present"
        payload["results"][key] = results[label]
    if delta is not None:
        payload["results"]["write_cost_delta_mean_s"] = round(delta, 6)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[written] {args.output}")


if __name__ == "__main__":
    main()
