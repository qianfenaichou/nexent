"""L6 three-way RRF rank fusion kernel (2026-09-29).

Reciprocal Rank Fusion (Cormack et al., SIGIR 2009) over N ranked lists
(production use is three: BM25 + dense + graph):

    score(d) = sum over lists of 1 / (k + rank(d in that list))

with 1-based ranks and the classic default k = 60. RRF consumes *ranks*
only - it never reads relevance scores - so it can fuse heterogeneous
retrievers (BM25, dense, graph) whose scores are not on a common scale.
That is exactly why it replaces the platform's weighted-normalised hybrid
score for the L6 three-way route (see the L6 design doc).

Why not the platform formula: ``ElasticSearchCore.hybrid_search`` combines
two routes as ``w * norm_accurate + (1-w) * norm_semantic`` (max-normalised
per query). Max-normalisation is outlier-sensitive, the weight is a picked
constant, and there is no third (graph) route at all. RRF is rank-only,
parameter-light (one k), and extends to N lists.

This module is stdlib-only and touches no database, no ES, no LLM.
Wiring a production call site is out of scope (T-08 / follow-up); nothing
in the running system imports this module yet, so default retrieval
behaviour is unchanged.

Determinism contract (tested):
- score accumulation uses exact ``fractions.Fraction`` arithmetic and is
  then cast to ``float`` for the public field, so mathematically tied
  scores tie exactly (no float-order surprises) and sort stably;
- sort key is ``(-score, best_rank, id)``: higher score first, then the
  better (lower) best rank, then id lexicographic ascending;
- empty outer list or all-empty lists -> empty result;
- a single non-empty list returns that list's order;
- a duplicate id *within* one list keeps only its first (best) rank;
  a duplicate id *across* lists sums the per-list contributions;
- list permutation moves the ``ranks`` tuple slots but never the fused
  order or the scores (RRF is symmetric in the lists).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

DEFAULT_RRF_K = 60


@dataclass(frozen=True)
class FusedHit:
    """One fused result. ``ranks[i]`` is the 1-based rank in input list ``i``
    (None when the id is absent from that list). ``first_seen`` is the
    original item object from the earliest occurrence."""

    id: str
    score: float
    ranks: tuple[int | None, ...]
    best_rank: int
    first_seen: Any


def _coerce_k(k: Any) -> Fraction:
    """Validate k and return it as an exact Fraction (>= 0, finite)."""
    if isinstance(k, bool):
        raise TypeError(f"k must be a real number, not bool: {k!r}")
    if isinstance(k, int):
        if k < 0:
            raise ValueError(f"k must be >= 0, got {k!r}")
        return Fraction(k)
    if isinstance(k, float):
        if math.isnan(k) or math.isinf(k):
            raise ValueError(f"k must be finite, got {k!r}")
        if k < 0:
            raise ValueError(f"k must be >= 0, got {k!r}")
        return Fraction(k)
    raise TypeError(f"k must be int or float, got {type(k).__name__}")


def _extract_id(item: Any) -> str:
    """Pull a string id out of a hit item (bare str / mapping / attribute)."""
    if isinstance(item, str):
        raw: Any = item
    else:
        # Mapping and object paths share one rule: treat None as "unset"
        # and fall back to stable_id; any other non-str stays non-str and
        # raises TypeError below (contract: id 优先、否则 stable_id).
        if isinstance(item, Mapping):
            raw = item.get("id")
        else:
            raw = getattr(item, "id", None)
        if raw is None:
            raw = (item.get("stable_id") if isinstance(item, Mapping)
                   else getattr(item, "stable_id", None))
    if not isinstance(raw, str):
        raise TypeError(
            f"hit item has no usable string id (need str / dict[id|stable_id]"
            f" / .id / .stable_id): {item!r}"
        )
    if raw == "":
        raise ValueError("hit id must be a non-empty string")
    return raw


def fuse(three_lists: Sequence[Sequence[Any]], k: Any = DEFAULT_RRF_K) -> list[FusedHit]:
    """Fuse ranked lists with reciprocal rank fusion.

    ``three_lists`` is a sequence of ranked hit lists (production: the
    BM25, dense and graph routes; any count >= 0 is accepted so a missing
    route is just an empty list). Each hit item must expose a string id
    (bare ``str``, mapping with ``id``/``stable_id``, or object with
    ``.id``/``.stable_id``). Within one list the first occurrence of an id
    sets its rank; across lists contributions are summed.

    Returns hits sorted by ``(-score, best_rank, id)``. ``score`` is the
    RRF sum as a float; ranking itself uses exact Fraction arithmetic.
    """
    k_val = _coerce_k(k)
    try:
        lists = list(three_lists)
    except TypeError as exc:
        raise TypeError("three_lists must be a sequence of ranked lists") from exc

    acc: dict[str, dict[str, Any]] = {}
    n = len(lists)
    for i, lst in enumerate(lists):
        if isinstance(lst, (str, bytes)):
            raise TypeError(
                f"three_lists[{i}] must be a ranked list of hits, not a string"
            )
        try:
            iterator = iter(lst)
        except TypeError as exc:
            raise TypeError(
                f"three_lists[{i}] must be iterable, got {type(lst).__name__}"
            ) from exc
        seen_in_list: set[str] = set()
        for rank, item in enumerate(iterator, start=1):
            hid = _extract_id(item)
            if hid in seen_in_list:
                continue
            seen_in_list.add(hid)
            term = Fraction(1) / (k_val + rank)
            rec = acc.get(hid)
            if rec is None:
                acc[hid] = {
                    "score": term,
                    "ranks": [None] * n,
                    "first_seen": item,
                }
                acc[hid]["ranks"][i] = rank
            else:
                rec["score"] += term
                rec["ranks"][i] = rank

    scored: list[tuple[Fraction, int, str, FusedHit]] = []
    for hid, rec in acc.items():
        ranks = tuple(rec["ranks"])
        present = [r for r in ranks if r is not None]
        best = min(present)
        scored.append(
            (
                rec["score"],
                best,
                hid,
                FusedHit(
                    id=hid,
                    score=float(rec["score"]),
                    ranks=ranks,
                    best_rank=best,
                    first_seen=rec["first_seen"],
                ),
            )
        )
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [t[3] for t in scored]


def retrieve_three_way(
    query: str,
    *,
    bm25: Callable[[str], Sequence[Any]],
    dense: Callable[[str], Sequence[Any]],
    graph: Callable[[str], Sequence[Any]],
    k: Any = DEFAULT_RRF_K,
) -> list[FusedHit]:
    """Production seam: pull three raw ranked lists, then RRF-fuse them.

    The three retrievers are injected callables returning ranked hit lists
    (same item shape as :func:`fuse`). This module never opens a database
    or an ES connection; the call site supplies the retrievers. Failures
    inside a retriever propagate to the caller - silent fallback belongs
    at the call site (ES-first + PG ilike style), not in the fusion kernel.
    A retriever returning ``None`` is treated as an empty list.
    """

    def _pull(fn: Callable[[str], Sequence[Any]]) -> list[Any]:
        out = fn(query)
        return [] if out is None else out

    return fuse([_pull(bm25), _pull(dense), _pull(graph)], k=k)
