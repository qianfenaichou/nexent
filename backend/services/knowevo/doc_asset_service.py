"""
KnowEvo asset-domain retrieval service:
text search over doc_asset_t - the capability the frozen MCP tool
``asset_search`` wraps.

Before this module the asset domain had point reads only (by id/asset_no,
serving ingest/kg/decision); the 2026-09-28 audit found no retrieval
capability, which is why ``asset_search`` was the one frozen tool that
stayed unregistered (pinned by a test). This service closes that gap: it
adds ``search_assets`` and deliberately does NOT migrate the existing
point reads (a settled design ruling - migrating them would touch three
production call sites and re-open their verification).

Contract and spec source: the frozen asset-search interface notes in
``knowevo/backend/services/knowevo/doc_asset_service.py.md``.

Implementation principles are the same as GraphStore.entity_lookup
(same ES-first ordering, deterministic PG fallback, stable signature):
ES scores participate in ranking but stay explainable (the raw score is
kept in ``why``), and never any PPR / random-walk score.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any

from database.knowevo_db import DocAsset, _get_db_session
from services.knowevo.schemas import AssetHit
from sqlalchemy.orm import aliased

logger = logging.getLogger(__name__)

# The single parse gate (return parse-complete rows only;
# 2026-09-29 real-data audit: parse_status takes exactly two values,
# "processed" and "no_index_chunk", so "parse complete" = processed). No
# no quality threshold is defined, so parse_quality is
# surfaced for audit only.
PARSE_GATE = "processed"


async def _maybe_await(value):
    """Await a coroutine, pass a plain value through.

    The injected ES client may be either a synchronous HTTP wrapper or an
    async one (same seam discipline as GraphStore.entity_lookup, L1 step 3).
    """
    if inspect.isawaitable(value):
        return await value
    return value


def _escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards so user query is matched literally."""
    return (text.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_"))


def _filters_applied(modality: str | None, doc_type: str | None,
                     authority_min: int | None,
                     include_superseded: bool) -> dict[str, Any]:
    """The caller's requested filter set, as recorded in every ``why``.

    Absent filters are recorded as None/False (not omitted) so the audit
    trail states what was asked, not merely what matched."""
    return {"modality": modality, "doc_type": doc_type,
            "authority_min": authority_min,
            "include_superseded": include_superseded}


def _hit_from_es(d: Any, filters: dict[str, Any]) -> AssetHit | None:
    """Best-effort coercion of one ES seam dict to an AssetHit.

    The seam contract is
    ``es_client.asset_search(tenant_id, query, limit) -> list[dict]`` with
    the field table frozen in doc_asset_service.py.md; a non-dict, or a
    dict without ``id``/``title``, is malformed and skipped (same
    defensiveness as GraphStore._as_entity_card). ``score`` keeps the RAW
    ES score here; normalization happens later, and ``why.es_score_raw``
    preserves this value so the ranking stays reversible on paper.
    """
    if not isinstance(d, dict):
        return None
    hit_id = d.get("id")
    title = d.get("title")
    if not hit_id or not title:
        return None
    raw = d.get("score")
    raw_score = float(raw) if isinstance(raw, (int, float)) else 0.0
    level = d.get("authority_level")
    parse_quality = d.get("parse_quality")
    # Per-hit skip on dirty types (review): one malformed field must
    # not throw into the caller's broad except and drop the whole ES page.
    try:
        authority_level = int(level) if level is not None else 3
        quality = (float(parse_quality)
                   if parse_quality is not None else None)
    except (TypeError, ValueError):
        return None
    return AssetHit(
        id=str(hit_id),
        asset_no=str(d.get("asset_no") or ""),
        title=str(title),
        modality=str(d.get("modality") or ""),
        doc_type=str(d.get("doc_type") or ""),
        # 3 is the column's server_default ("3 label"); a missing level
        # must not become 0, which would win the authority-asc tie-break.
        authority_level=authority_level,
        score=raw_score,
        why={"es_score_raw": raw_score, "matched": "title+metadata",
             "filters_applied": dict(filters), "parse_gate": PARSE_GATE},
        parse_status=(str(d["parse_status"])
                      if d.get("parse_status") is not None else None),
        parse_quality=quality,
        superseded=bool(d.get("superseded") or False),
    )


def _pg_why(filters: dict[str, Any]) -> dict[str, Any]:
    """The auditable ``why`` for a PG-fallback hit (no relevance signal:
    the rule that ranked it is named instead of a fabricated score)."""
    return {"matched": "title ilike",
            "ranked_by": "authority_level asc, created_at desc",
            "es_score_raw": None,
            "filters_applied": dict(filters)}


def _row_to_hit(row: Any, filters: dict[str, Any],
                superseded: bool = False) -> AssetHit:
    """doc_asset_t row -> AssetHit with the PG fallback score semantics."""
    return AssetHit(
        id=str(row.id), asset_no=str(row.asset_no), title=str(row.title),
        modality=str(row.modality), doc_type=str(row.doc_type),
        authority_level=int(row.authority_level), score=0.0,
        why=_pg_why(filters),
        parse_status=(str(row.parse_status)
                      if row.parse_status is not None else None),
        parse_quality=(float(row.parse_quality)
                       if row.parse_quality is not None else None),
        superseded=superseded,
    )


def _postfilter(hits: list[AssetHit], *, modality: str | None,
                doc_type: str | None, authority_min: int | None,
                include_superseded: bool) -> list[AssetHit]:
    """Uniform post-filter over seam hits (the ES path; the PG path mirrors
    the same semantics in SQL, per the frozen contract). Kept in the service
    - not the ES client and not the MCP layer - so the filter semantics have
    a single owner on the ES side: parse gate, exact modality/doc_type
    equality, authority floor, and supersede folding (default drops rows
    another version has replaced)."""
    kept: list[AssetHit] = []
    for h in hits:
        if h.parse_status != PARSE_GATE:
            continue
        if modality is not None and h.modality != modality:
            continue
        if doc_type is not None and h.doc_type != doc_type:
            continue
        if authority_min is not None and h.authority_level < authority_min:
            continue
        if not include_superseded and h.superseded:
            continue
        kept.append(h)
    return kept


class DocAssetService:
    """Asset search over doc_asset_t: ES-first hybrid (title+metadata),
    PG ilike (title) fallback - read path only.

    Contract (doc_asset_service.py.md): the seam shape (method + return
    type) is final. When an ``es_client`` is injected, its
    ``asset_search(tenant_id, query, fetch_n) -> list[dict]`` supplies ES
    hybrid hits (sync or async, both adapted); the service post-filters
    them uniformly, normalizes the ES score against the filtered set
    (top = 1.0; raw score kept in ``why``), ranks by normalized score with
    an authority-ascending tie-break, and tops the page up from the PG
    fallback (dedup by id) when ES is short. If no client is injected, the
    client errors, or it yields nothing usable after filtering, the method
    degrades to the deterministic PG ilike lookup. No PPR score is ever
    introduced - ordering stays per-hit explainable
    (auditability > elegance).

    ``session_factory`` is injectable for offline tests (mirrors
    ingest_service); the default resolves the shared session context
    manager exactly like the rest of the knowevo layer.
    """

    def __init__(self, es_client: Any = None, session_factory: Any = None):
        self.es_client = es_client
        self._session_factory = session_factory

    async def search_assets(self, tenant_id: str, query: str,
                            modality: str | None = None,
                            doc_type: str | None = None,
                            authority_min: int | None = None,
                            include_superseded: bool = False,
                            limit: int = 5) -> list[AssetHit]:
        """Search the tenant's registered assets (ES-first hybrid over
        title+metadata, PG ilike fallback).

        Returns hits ranked by normalized relevance (ES path, top = 1.0)
        or the deterministic authority/recency order (PG path, score 0.0),
        filtered uniformly: parse_status == "processed" (the only parse
        gate - no quality threshold is invented), exact modality/doc_type,
        authority_min floor, and supersede folding unless
        ``include_superseded``. An empty (whitespace-only) query answers
        [] without touching either backend.
        """
        q = query.strip()
        if not q:
            return []
        filters = _filters_applied(modality, doc_type, authority_min,
                                   include_superseded)
        if self.es_client is not None:
            try:
                # Over-fetch min(limit*3, 30): the uniform post-filter runs
                # AFTER ES ranking and may drop rows, so the seam must fetch
                # more than ``limit`` candidates for the filtered page to
                # still reach the requested size.
                fetch_n = min(limit * 3, 30)
                raw = await _maybe_await(
                    self.es_client.asset_search(tenant_id, q, fetch_n))
                es_hits = _postfilter(
                    [h for h in (_hit_from_es(d, filters)
                                 for d in (raw or [])) if h is not None],
                    modality=modality, doc_type=doc_type,
                    authority_min=authority_min,
                    include_superseded=include_superseded)
                if es_hits:
                    return await self._rank_and_fill(
                        es_hits, tenant_id, q, modality, doc_type,
                        authority_min, include_superseded, limit)
            except Exception:  # silent ES -> PG fallback by contract (debug-logged for triage)
                logger.debug("search_assets ES-first failed; falling back "
                             "to PG ilike", exc_info=True)
        return await self._search_assets_pg(
            tenant_id, q, modality, doc_type, authority_min,
            include_superseded, limit)

    async def _rank_and_fill(self, es_hits: list[AssetHit],
                             tenant_id: str, q: str,
                             modality: str | None, doc_type: str | None,
                             authority_min: int | None,
                             include_superseded: bool,
                             limit: int) -> list[AssetHit]:
        """Normalize ES scores against the filtered set, top the page up
        from PG (dedup by id), rank by score desc / authority asc, truncate."""
        max_raw = max(h.score for h in es_hits)
        if max_raw > 0:
            for h in es_hits:
                h.score = h.score / max_raw
        else:
            # contract: max<=0 -> all 0.0 (never keep negative raw scores)
            for h in es_hits:
                h.score = 0.0
        filled: list[AssetHit] = list(es_hits)
        if len(filled) < limit:
            seen = {h.id for h in filled}
            for h in await self._search_assets_pg(
                    tenant_id, q, modality, doc_type, authority_min,
                    include_superseded, limit):
                if h.id in seen:
                    continue
                seen.add(h.id)
                filled.append(h)
                if len(filled) >= limit:
                    break
        filled.sort(key=lambda h: (-h.score, h.authority_level))
        return filled[:limit]

    async def _search_assets_pg(self, tenant_id: str, q: str,
                                modality: str | None, doc_type: str | None,
                                authority_min: int | None,
                                include_superseded: bool,
                                limit: int) -> list[AssetHit]:
        """Deterministic PG fallback (title ilike) - the ES-free floor.

        Extracted from ``search_assets`` so the ES-first branch can use it
        as the fallback and page filler, and offline tests can stub it
        without a database (same pattern as
        GraphStore._entity_lookup_ilike). Filter semantics match the ES
        post-filter exactly, expressed in SQL; supersede folding is a
        NOT EXISTS over rows of the same tenant whose supersede_of points
        at this row (never a full-table scan-and-judge).

        Ordering is authority-first, then newest-first: this path has no
        relevance signal, so the only honest ranking is the asset's own
        governance metadata - most authoritative source first
        (authority_level ascending, 1 national std .. 4 popular science),
        ties broken by recency (created_at descending).
        """
        session_factory = self._session_factory or _get_db_session
        filters = _filters_applied(modality, doc_type, authority_min,
                                   include_superseded)
        hits: list[AssetHit] = []
        with session_factory() as session:
            successor = aliased(DocAsset)
            has_successor = (
                session.query(successor.id)
                .filter(successor.tenant_id == DocAsset.tenant_id,
                        successor.supersede_of == DocAsset.id)
                .exists()
            )
            query = (
                session.query(DocAsset)
                .filter(DocAsset.tenant_id == tenant_id,
                        DocAsset.parse_status == PARSE_GATE,
                        DocAsset.title.ilike(
                            f"%{_escape_like(q)}%", escape="\\"))
            )
            if modality is not None:
                query = query.filter(DocAsset.modality == modality)
            if doc_type is not None:
                query = query.filter(DocAsset.doc_type == doc_type)
            if authority_min is not None:
                query = query.filter(
                    DocAsset.authority_level >= authority_min)
            if not include_superseded:
                query = query.filter(~has_successor)
            rows = (
                query.order_by(DocAsset.authority_level.asc(),
                               DocAsset.created_at.desc())
                .limit(limit)
                .all()
            )
            # include_superseded=True keeps replaced rows, so each hit must
            # say honestly whether it WAS replaced (ids are UUID PKs, so a
            # single bounded IN lookup marks exactly the returned page).
            superseded_ids: set[Any] = set()
            if include_superseded and rows:
                sup_rows = (
                    session.query(DocAsset.supersede_of)
                    .filter(DocAsset.tenant_id == tenant_id,
                            DocAsset.supersede_of.in_([r.id for r in rows]))
                    .all()
                )
                superseded_ids = {sid for (sid,) in sup_rows
                                  if sid is not None}
            hits = [_row_to_hit(r, filters, r.id in superseded_ids)
                    for r in rows]
        return hits
