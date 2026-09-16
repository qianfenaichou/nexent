"""
Synthetic graph generator (T-07a PoC) - deterministic 20k-entity / 30k-edge
graph for the A1 benchmark probes (memo 09 3.1/3.2):

    P1  multi-hop p95 < 1.5s @ 20k entities / 30k edges
    P2  batch supersede p95 < 200ms

The generator is deterministic (fixed seed -> same graph every run), so
baseline numbers are reproducible and diffs between adapter backends are
meaningful. Dry-run prints a plan; with PG reachable it upserts through
PgJsonbGraphStore and prints scale stats.

Usage (from backend/):
    python -m services.knowevo.pipeline.gen_synthetic_graph \\
        [--entities 20000 --edges 30000] [--tenant <uuid>] [--dry-run]
"""
import argparse
import asyncio
import random
import sys
import time

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000002"
SEED = 42  # fixed: reproducible graph

_CLASSES = ("Drug", "Biguanide", "AlphaGlucosidaseInhibitor", "Disease",
            "Type2Diabetes", "Guideline", "Insulin", "Sulfonylurea",
            "DPP4Inhibitor", "SGLT2Inhibitor", "Complication",
            "LabMarker", "VitalSign", "Procedure", "Lifestyle")

_REL_TYPES = ("indicated_for", "contraindicated_with", "drug_used_in",
              "risk_factor_for", "diagnosed_by", "monitored_by",
              "treated_with", "prevents")


def _gen_graph(n_entities: int, n_edges: int, rng: random.Random) -> tuple[
        list[dict], list[dict]]:
    """Deterministic graph: entities named drg-<i>/dis-<i> etc so names are
    unique and the graph is an expandable random network."""
    entities: list[dict] = []
    for i in range(n_entities):
        cls = _CLASSES[i % len(_CLASSES)]
        entities.append({
            "stable_id": f"{cls}:e{i:06d}",
            "name": f"syn-{cls}-{i:05d}",
            "class_ref": cls,
            "props": {"seed": SEED, "idx": i},
            "aliases": [{"alias": f"a{i:05d}", "type": "abbr"}],
            "status": "active",
        })
    # scale-free-ish: attach to a growing pool so the graph stays connected
    edges: list[dict] = []
    pool = list(range(min(n_entities, 200)))
    for j in range(n_edges):
        a = rng.choice(range(n_entities))
        b = rng.choice(pool)
        if a == b:
            b = (a + rng.randint(1, 5)) % n_entities
        edges.append({
            "src": entities[a]["stable_id"],
            "dst": entities[b]["stable_id"],
            "rel_type": _REL_TYPES[j % len(_REL_TYPES)],
            "claim": f"synthetic edge {j}",
            "props": {},
        })
        pool.append(a)
        if len(pool) > 500:
            pool = pool[-500:]
    return entities, edges


def _print_stats(entities: list[dict], edges: list[dict]) -> None:
    from collections import Counter
    cls = Counter(e["class_ref"] for e in entities)
    rel = Counter(e["rel_type"] for e in edges)
    print(f"entities={len(entities)} edges={len(edges)}")
    print("by class:", dict(cls.most_common(5)))
    print("by rel_type:", dict(rel.most_common(5)))


async def _run(args: argparse.Namespace) -> int:
    rng = random.Random(SEED)
    t0 = time.monotonic()
    entities, edges = _gen_graph(args.entities, args.edges, rng)
    gen_s = round(time.monotonic() - t0, 3)

    print(f"seed={SEED} gen_seconds={gen_s}")
    _print_stats(entities, edges)

    if args.dry_run:
        print(f"dry-run: would upsert {len(entities)} entities "
              f"/ {len(edges)} edges to tenant {args.tenant}")
        return 0

    from services.knowevo.graph_store import PgJsonbGraphStore
    store = PgJsonbGraphStore()
    tenant = args.tenant or DEFAULT_TENANT

    t1 = time.monotonic()
    await store.upsert_entities(tenant, entities)
    upsert_e = round(time.monotonic() - t1, 3)

    t2 = time.monotonic()
    await store.upsert_relations(tenant, edges)
    upsert_r = round(time.monotonic() - t2, 3)

    t3 = time.monotonic()
    stats = await store.stats(tenant, "graph")
    stats_s = round(time.monotonic() - t3, 3)

    print(f"tenant={tenant} upsert_entities_s={upsert_e} "
          f"upsert_relations_s={upsert_r} stats_s={stats_s} "
          f"entities={stats['entities']} edges_total={stats['edges_total']} "
          f"edges_valid={stats['edges_valid']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic graph generator for A1 PoC benchmarks")
    parser.add_argument("--entities", type=int, default=20000)
    parser.add_argument("--edges", type=int, default=30000)
    parser.add_argument("--tenant", default=None, help="tenant UUID")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate + print stats, do not touch PG")
    try:
        return asyncio.run(_run(parser.parse_args(argv)))
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    sys.exit(main())