"""Deterministic graph-route composition for the kg_search fusion.

The third fusion route has no ES analogue: it must answer "which graph
entities relate to this query" as ONE ordered candidate list. The frozen
seams already provide the pieces - ``entity_lookup`` for seeds (ES-first
with ilike fallback, order preserved) and ``neighbors`` for the
neighborhood - but nothing composed them into an ordered list, and the
raw ``Subgraph`` carries no relevance order (``entities`` come back in PG
row order, which is non-contractual).

This module is that composition, and it is deliberately boring:

- BFS over the frozen ``neighbors`` seam one layer per call, so every
  entity gets an exact ``hop_distance`` (seeds = 0) without touching the
  frozen method signatures or writing a recursive CTE;
- degree = incident-edge count over every edge the BFS saw;
- final order is the frozen key ``(hop_distance, -degree, stable_id)``
  via ``ordering_key`` - near entities first, well-connected entities
  next, stable_id as the total-order tie-break. No PPR, no random walk,
  no dependence on PG row order (auditability > elegance, L1 verdict).

Every failure inside the store seams propagates to the caller; the silent
fallback belongs at the call site (kg_search handler), not here.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from services.knowevo.graph_store import EntityCard

DEFAULT_SEED_TOP_K = 5
DEFAULT_HOP = 1

_MAX_HOP = 3  # matches kg_multi_hop depth cap; a defensive ceiling, not a seam


@dataclass(frozen=True)
class GraphRoute:
    """Ordered graph-route candidates plus their deterministic metadata.

    ``cards`` is sorted by ``(hop_distance, -degree, stable_id)``;
    ``hop_by_id`` / ``degree_by_id`` are the metadata that order implies,
    exposed so the kg_search handler can order its neighborhood tail with
    the exact same key instead of inventing a second one.
    """

    cards: tuple[EntityCard, ...] = ()
    hop_by_id: Mapping[str, int] = field(default_factory=dict)
    degree_by_id: Mapping[str, int] = field(default_factory=dict)


def ordering_key(stable_id: str, hop_by_id: Mapping[str, int],
                 degree_by_id: Mapping[str, int]) -> tuple[float, int, str]:
    """The frozen route-order key; a total order over ANY card set.

    ``(hop, -degree, stable_id)`` for ids the BFS metadata knows; ids
    absent from it sort after every known id (hop = +inf, degree 0) and
    among themselves by stable_id - so a handler mixing fused seeds with
    fresh neighborhood cards still gets one deterministic order.
    """
    hop = hop_by_id.get(stable_id)
    if hop is None:
        return (math.inf, 0, stable_id)
    return (hop, -degree_by_id.get(stable_id, 0), stable_id)


def _validate(seed_top_k: Any, hop: Any) -> None:
    if isinstance(seed_top_k, bool) or not isinstance(seed_top_k, int):
        raise TypeError(
            f"seed_top_k must be int, got {type(seed_top_k).__name__}")
    if seed_top_k < 1:
        raise ValueError(f"seed_top_k must be >= 1, got {seed_top_k!r}")
    if isinstance(hop, bool) or not isinstance(hop, int):
        raise TypeError(f"hop must be int, got {type(hop).__name__}")
    if hop < 1:
        raise ValueError(f"hop must be >= 1, got {hop!r}")
    if hop > _MAX_HOP:
        raise ValueError(f"hop must be <= {_MAX_HOP}, got {hop!r}")


async def graph_route_cards(store: Any, tenant_id: str, query: str, *,
                            seed_top_k: int = DEFAULT_SEED_TOP_K,
                            hop: int = DEFAULT_HOP) -> GraphRoute:
    """Query -> seeds -> per-hop BFS -> deterministically ordered cards.

    ``store`` is any GraphStore-seam object (``entity_lookup`` +
    ``neighbors``); the real adapter and offline fakes are both fine.
    Empty lookup or blank query yields an empty route with zero
    ``neighbors`` calls. Duplicate stable_ids (a seed that is also a
    neighbor, an entity reachable over several edges) collapse to one
    card at their smallest hop distance.
    """
    _validate(seed_top_k, hop)
    if not isinstance(query, str):
        raise TypeError(f"query must be str, got {type(query).__name__}")
    if not query.strip():
        return GraphRoute()

    seeds = await store.entity_lookup(tenant_id, query, seed_top_k)
    seed_ids = [c.stable_id for c in seeds]
    if not seed_ids:
        return GraphRoute()

    hop_by_id: dict[str, int] = {}
    degree_by_id: dict[str, int] = {}
    cards_by_id: dict[str, EntityCard] = {}
    for card in seeds:
        hop_by_id.setdefault(card.stable_id, 0)
        cards_by_id.setdefault(card.stable_id, card)

    seen_edge_ids: set[Any] = set()
    frontier = list(dict.fromkeys(seed_ids))
    for depth in range(1, hop + 1):
        if not frontier:
            break
        sub = await store.neighbors(tenant_id, frontier, hop=1)
        next_frontier: list[str] = []
        for edge in sub.edges:
            if edge.id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge.id)
            for sid in (edge.src, edge.dst):
                degree_by_id[sid] = degree_by_id.get(sid, 0) + 1
        for card in sub.entities:
            cards_by_id.setdefault(card.stable_id, card)
            if card.stable_id not in hop_by_id:
                hop_by_id[card.stable_id] = depth
                next_frontier.append(card.stable_id)
        frontier = next_frontier

    # Degree is total over the returned cards: an isolated seed is a real
    # observation (degree 0), not an absent key.
    for sid in cards_by_id:
        degree_by_id.setdefault(sid, 0)

    ordered = sorted(
        cards_by_id.values(),
        key=lambda c: ordering_key(c.stable_id, hop_by_id, degree_by_id))
    return GraphRoute(cards=tuple(ordered), hop_by_id=hop_by_id,
                      degree_by_id=degree_by_id)
