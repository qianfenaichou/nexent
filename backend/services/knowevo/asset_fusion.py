"""asset_search two-route fusion factory (T-08 phase 2, 2026-09-30).

The asset-context consumer of the frozen RRF kernel
(``rrf_fusion.fuse``). Id space is strictly ``AssetHit.id`` (str) - the
frozen asset space from ``l6-m2-id-space-decision-2026-09-29.md`` §2.
``document.id`` (knowledge-base document space) must never join this fuse.

Routes this round (mirroring the kg_search factory shape):

- bm25  - ``AssetRawListClient.asset_bm25_hits``: a *synchronous* SDK
  call, so it runs in a worker thread via ``asyncio.to_thread`` and never
  blocks the event loop;
- dense - ``AssetRawListClient.asset_dense_hits``: honestly empty this
  round (the asset index carries no embedding field); the frozen audit
  reason rides on the outcome instead of a fabricated score.

Ignition gate (the anti-theatre rule from L6-M2 verify §6-③): fuse output
is handed to the caller only when **at least two routes are non-empty**.
A single-route ranked list is not fusion; it is that route's own order
with a RRF costume. The gate therefore stays closed while dense is empty
and only BM25 can contribute - the handler keeps today's
``search_assets`` path bit for bit. Cross-route duplicates (same
``AssetHit.id`` in both lists) are exactly the consensus RRF rewards.

Failures inside any route propagate to the caller: per the frozen kernel
contract, silent fallback belongs at the call site (the asset_search MCP
handler), not here. A ``None`` client is a programming error and raises.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from services.knowevo.asset_raw_list import ASSET_INDEX_HAS_NO_EMBEDDING_FIELD
from services.knowevo.rrf_fusion import DEFAULT_RRF_K, FusedHit, fuse

# Optional marker some adapters may set; only the asset space is legal here.
_ASSET_ID_SPACE = "asset"


@dataclass(frozen=True)
class AssetFusionOutcome:
    """Fused asset order + audit, or a closed-gate record.

    ``fired`` is True only when the ignition gate saw >= 2 non-empty
    routes and ``ids``/``hits`` carry the real fusion. When ``fired`` is
    False the caller must keep today's path; counts and ``dense_reason``
    still record what the routes contributed (audit, not a fused score).
    """

    ids: tuple[str, ...]
    hits: tuple[FusedHit, ...]
    bm25_count: int
    dense_count: int
    fired: bool
    dense_reason: str = ASSET_INDEX_HAS_NO_EMBEDDING_FIELD


def _reject_foreign_id_space(items: list[Any], route: str) -> None:
    """Refuse hits that explicitly claim a non-asset id space.

    The adapter is the only mapper into ``AssetHit.id``. A hit that
    stamps ``id_space`` with anything other than ``"asset"`` / missing is
    a cross-space fuse attempt and must raise rather than silently
    concatenate document-space ids into this list.
    """
    for item in items:
        if isinstance(item, dict):
            space = item.get("id_space")
            if space is not None and space != _ASSET_ID_SPACE:
                raise ValueError(
                    f"{route} hit claims id_space={space!r}; asset fusion "
                    f"only accepts the AssetHit.id space")


async def fused_asset_hits(client: Any, tenant_id: str, query: str, *,
                           top_k: int = 5,
                           k: Any = DEFAULT_RRF_K) -> AssetFusionOutcome:
    """Pull the two asset routes and RRF-fuse them behind the ignition gate.

    Returns an ``AssetFusionOutcome`` always. ``fired`` is True only when
    both routes contributed at least one hit; otherwise ``ids``/``hits``
    are empty and the caller keeps today's path. Raises on invalid
    arguments or route failure - never swallows.
    """
    if client is None:
        raise ValueError(
            "client is required: the caller decides whether ES is "
            "configured; None here would silently fuse nothing")
    bm25 = await asyncio.to_thread(
        client.asset_bm25_hits, tenant_id, query, top_k)
    bm25 = list(bm25 or [])
    dense = list(client.asset_dense_hits(tenant_id, query, top_k) or [])
    _reject_foreign_id_space(bm25, "bm25")
    _reject_foreign_id_space(dense, "dense")

    non_empty = sum(1 for route in (bm25, dense) if route)
    if non_empty < 2:
        return AssetFusionOutcome(
            ids=(), hits=(), bm25_count=len(bm25), dense_count=len(dense),
            fired=False, dense_reason=ASSET_INDEX_HAS_NO_EMBEDDING_FIELD)

    hits = fuse([bm25, dense], k=k)
    return AssetFusionOutcome(
        ids=tuple(h.id for h in hits),
        hits=tuple(hits),
        bm25_count=len(bm25),
        dense_count=len(dense),
        fired=True,
        dense_reason=ASSET_INDEX_HAS_NO_EMBEDDING_FIELD,
    )
