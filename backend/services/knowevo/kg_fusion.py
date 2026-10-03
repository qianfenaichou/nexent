"""kg_search three-way fusion factory (wiring, 2026-09-30).

The first production consumer of the frozen RRF kernel
(``rrf_fusion.fuse``). One async entry point organises the three kg_search
routes in the shared graph-entity id space (``stable_id`` - the frozen id
space for the kg_search context) and returns the fused seed order plus the
audit trail:

- bm25  - ``EsRawListClient.entity_bm25_hits``: a *synchronous* SDK call
  (the platform ``ElasticSearchCore`` is sync), so it runs in a worker
  thread via ``asyncio.to_thread`` and never blocks the event loop;
- dense - ``EsRawListClient.dense_entity_hits``: honestly empty this
  round (the entity index carries no embedding field); the frozen audit
  reason rides on the outcome instead of a fabricated score;
- graph - ``graph_route_cards``: entity_lookup seeds -> per-hop BFS ->
  ``(hop, -degree, stable_id)`` order, no PPR / random walk.

Failures inside any route propagate to the caller: per the frozen kernel
contract, silent fallback belongs at the call site (the kg_search MCP
handler), not here. A ``None`` client is a programming error and raises -
the caller decides whether ES is configured before asking for fusion.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from services.knowevo.es_raw_list import DENSE_ENTITY_DISABLED_REASON
from services.knowevo.graph_retrieve import (
    DEFAULT_HOP,
    DEFAULT_SEED_TOP_K,
    GraphRoute,
    graph_route_cards,
)
from services.knowevo.rrf_fusion import DEFAULT_RRF_K, FusedHit, fuse


@dataclass(frozen=True)
class FusionOutcome:
    """Fused seed order + audit (recon §6: the caller marks the why).

    ``ids`` is the full fused order in the stable_id space (the caller
    slices its seed page off the front); ``hits`` carries the kernel's
    ``FusedHit`` audit (per-route ranks / best_rank / first_seen);
    ``*_count`` records what each route contributed; ``dense_reason`` is
    the honest emptiness reason for the dense slot; ``graph_route`` is
    the BFS metadata so the handler can order its neighborhood tail with
    the same deterministic key.
    """

    ids: tuple[str, ...]
    hits: tuple[FusedHit, ...]
    bm25_count: int
    dense_count: int
    graph_count: int
    dense_reason: str = DENSE_ENTITY_DISABLED_REASON
    graph_route: GraphRoute | None = None


async def fused_entity_cards(
    client: Any,
    store: Any,
    tenant_id: str,
    query: str,
    *,
    seed_top_k: int = DEFAULT_SEED_TOP_K,
    hop: int = DEFAULT_HOP,
    k: Any = DEFAULT_RRF_K,
    dense_reason: str = DENSE_ENTITY_DISABLED_REASON,
) -> FusionOutcome:
    """Pull the three routes and RRF-fuse them (k=60 by default).

    ``client`` is an ``EsRawListClient`` (or duck-typed fake), ``store``
    any GraphStore-seam object. Returns a ``FusionOutcome``; raises on
    invalid arguments or route failure - never swallows.
    """
    if client is None:
        raise ValueError(
            "client is required: the caller decides whether ES is "
            "configured; None here would silently fuse nothing")
    bm25 = await asyncio.to_thread(
        client.entity_bm25_hits, tenant_id, query, seed_top_k)
    bm25 = list(bm25 or [])
    dense = list(client.dense_entity_hits(tenant_id, query, seed_top_k) or [])
    route = await graph_route_cards(
        store, tenant_id, query, seed_top_k=seed_top_k, hop=hop)
    hits = fuse([bm25, dense, list(route.cards)], k=k)
    return FusionOutcome(
        ids=tuple(h.id for h in hits),
        hits=tuple(hits),
        bm25_count=len(bm25),
        dense_count=len(dense),
        graph_count=len(route.cards),
        dense_reason=dense_reason,
        graph_route=route,
    )
