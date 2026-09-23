"""
KnowEvo A1 graph-store seam (T-07a): the single access surface for graph
queries - neighbors, multi-hop, supersede, impact scope, lookup and stats.

Callers (kg_service, decision_service) import only this module and never
write SQL/CTE directly; that discipline is what makes the adapter swappable
(PG JSONB default, Kuzu read-only PoC). Persistence goes through the
``database.knowevo_db`` helpers (same session/tenant rules as the rest of
the knowevo layer), and every current-view query reuses ``valid_now`` so
the bi-temporal semantics live in one place.

Contract: knowevo/backend/services/knowevo/graph_store.py.md (frozen);
algorithm source: memo 09-A1 (02-technical-plan 2.6). The PoC benchmark
probes (P1 multi-hop p95 < 1.5s @ 20k/30k, P2 supersede p95 < 200ms) run
against the synthetic graph in pipeline/gen_synthetic_graph.py.

T-09 seam extension (recorded here because the frozen contract says seam
changes are documented in one place): ``neighbors`` and ``multi_hop`` take
an optional ``as_of`` keyword, and ``EdgeCard`` carries ``valid_at`` /
``invalid_at``. Together they are what version-pinned traversal walks on -
every hop is evaluated at a knowledge version's cutoff t_v instead of
now() (02-tech-plan 3.2, literature gap B2). Both extensions are additive:
the parameters default to None and reproduce the previous behaviour
exactly, so no existing caller changes, and the T-07 suite passing
unchanged is the compatibility evidence. An adapter that cannot honour a
cutoff may ignore it; callers detect support via ``inspect`` and fall back
to post-filtering (see DecisionService._store_supports_as_of).

Design inspired by: graphiti's bi-temporal edges and neighborhood walks
(attribution per 03-development-plan 4.2).
"""
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from database.knowevo_db import (
    KgEntity,
    KgEvidence,
    KgPendingEntity,
    KgRelation,
    _get_db_session,
    valid_now,
    valid_range_contains,
)
from sqlalchemy import insert, select
from sqlalchemy import tuple_ as sa_tuple

# Default number of rows per batched statement (K below). 1000 is a balance:
# large enough to amortise per-statement overhead, small enough that a single
# multi-row INSERT / IN-list stays well inside PostgreSQL's planner and memory
# sweet spot. Unbounded unnest on 20k+ rows tends to regress because the
# planner materialises the whole array and the bound payload balloons.
DEFAULT_GRAPH_BATCH_SIZE = 1000


def _batch_size_for(store) -> int:
    """Validated batch size for the batched upsert paths.

    Guards a silent-data-loss landmine found during the 2026-09-24 real-PG
    closure review: a non-positive ``K`` makes ``range(0, n, K)`` yield nothing,
    so **every existence lookup is skipped and every row is treated as new**.
    For ``kg_relation_t`` (no unique constraint) that inserts duplicates with no
    error; for ``kg_entity_t`` it would raise an IntegrityError at commit. A
    loud ``ValueError`` here is strictly better than either outcome.
    """
    k = getattr(store, "batch_size", DEFAULT_GRAPH_BATCH_SIZE)
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(
            f"batch_size must be a positive int, got {k!r} - a non-positive "
            f"value would silently skip every existence lookup"
        )
    return k


def current_view_predicate(model, as_of, *, range_form: bool):
    """Pick the bi-temporal current-view predicate for a read query.

    ``valid_now`` is the original two-conjunct boolean form. ``valid_range_contains``
    is the logically identical half-open-range containment that a GiST index on
    ``tstzrange(valid_at, COALESCE(invalid_at,'infinity'),'[)')`` (migration
    ``v2.5.5_kw_011``) can answer in one probe. They are equivalent row for row
    -- see the equivalence argument in ``valid_range_contains`` -- so switching
    is a performance decision, not a semantic one.

    Kept behind a switch because the switch is only worth flipping once
    ``EXPLAIN`` has confirmed the planner actually picks ``ix_kr_valid_range``,
    and because the range form *raises* on a degenerate row
    (``invalid_at < valid_at``) where the boolean form returned false. The
    default is therefore the original boolean predicate: byte-for-byte
    behaviour preservation.
    """
    if range_form:
        return valid_range_contains(model, as_of=as_of)
    return valid_now(model, as_of=as_of)

# ---------------------------------------------------------------------------
# Result shapes (contract graph_store.py.md: Subgraph / Path / HopPlan).
# Frozen here so both adapters and the service layer share one vocabulary.
# ---------------------------------------------------------------------------

@dataclass
class EntityCard:
    """One graph node in query output (same shape MCP kg_search emits)."""
    stable_id: str
    name: str
    class_ref: str
    props: dict[str, Any] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)


@dataclass
class EdgeCard:
    """One graph edge in query output; evidence_id rides in props.

    ``valid_at``/``invalid_at`` are the bi-temporal business-time window.
    They are carried into query output because version-pinned traversal
    (T-09, 02-tech-plan 3.2) must test each edge against a version cutoff
    t_v; without the window on the card the predicate would have to issue
    a second query per edge. Both default to None to stay backward
    compatible with hand-built fixtures (the columns are NOT NULL
    server-side, so real rows always carry valid_at).
    """
    id: Any
    src: str
    dst: str
    rel_type: str
    claim: str
    props: dict[str, Any] = field(default_factory=dict)
    contested: bool = False
    valid_at: datetime | None = None
    invalid_at: datetime | None = None


@dataclass
class Subgraph:
    """Neighborhood result: the requested entity cards plus the edges
    between/around them (current view unless valid_view=False)."""
    entities: list[EntityCard] = field(default_factory=list)
    edges: list[EdgeCard] = field(default_factory=list)


@dataclass
class HopPlan:
    """Per-hop expansion description for multi_hop: which rel_types to
    follow and whether to traverse reversed edges."""
    rel_types: list[str] | None = None
    reverse: bool = False
    min_depth: int = 1
    max_depth: int = 3


@dataclass
class Path:
    """One beam walk from a seed: alternating entity/edge ids plus the
    aggregated claims, for evidence-chain assembly (T-09)."""
    entities: list[str] = field(default_factory=list)
    edges: list[Any] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The seam (contract frozen, do not add methods without a memo update)
# ---------------------------------------------------------------------------

class GraphStore(ABC):
    @abstractmethod
    async def upsert_entities(self, tenant_id: str,
                              ents: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    async def upsert_relations(self, tenant_id: str,
                               rels: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    async def neighbors(self, tenant_id: str,
                        entity_ids: list[str],
                        rel_types: list[str] | None = None,
                        hop: int = 1,
                        valid_view: bool = True,
                        as_of: datetime | None = None) -> Subgraph:
        """Neighborhood of the given entities.

        ``valid_view=True`` returns the current view; ``as_of`` (T-09)
        evaluates that view at a knowledge-version cutoff t_v instead of
        now(), which is what version-pinned traversal walks on. Adapters
        that cannot honour a cutoff may ignore it, but the parameter is
        part of the seam so callers can rely on it existing.
        """
        ...

    @abstractmethod
    async def multi_hop(self, tenant_id: str, seeds: list[str],
                        hop_plan: HopPlan,
                        beam: int = 3, depth: int = 3,
                        as_of: datetime | None = None) -> list[Path]:
        """Beam walk from the seeds; ``as_of`` pins it to a knowledge
        version (T-09), None means the current view."""
        ...

    @abstractmethod
    async def supersede(self, tenant_id: str, edge_ids: list[uuid.UUID],
                        invalid_at: datetime, reason: str) -> None: ...

    @abstractmethod
    async def reachable_decisions(self, tenant_id: str,
                                  entity_ids: list[str]) -> list[uuid.UUID]:
        """Impact-scope query via kg_evidence_t refs (NOT traversal)."""
        ...

    @abstractmethod
    async def entity_lookup(self, tenant_id: str, query: str,
                            top_k: int = 5) -> list[EntityCard]: ...

    @abstractmethod
    async def stats(self, tenant_id: str, scope: str) -> dict: ...


# ---------------------------------------------------------------------------
# PG JSONB adapter (default backend)
# ---------------------------------------------------------------------------

def _entity_to_card(row) -> EntityCard:
    return EntityCard(
        stable_id=row.stable_id,
        name=row.name,
        class_ref=row.class_ref,
        props=dict(row.props or {}),
        aliases=[a.get("alias", "") for a in (row.aliases or [])
                 if isinstance(a, dict) and a.get("alias")],
    )


def _relation_to_card(row) -> EdgeCard:
    return EdgeCard(
        id=row.id,
        src=row.src,
        dst=row.dst,
        rel_type=row.rel_type,
        claim=row.claim,
        props=dict(row.props or {}),
        contested=bool(row.contested),
        valid_at=getattr(row, "valid_at", None),
        invalid_at=getattr(row, "invalid_at", None),
    )


class PgJsonbGraphStore(GraphStore):
    """Default adapter: PG JSONB tables with GIN alias index and the
    (valid_at, invalid_at) b-tree. One hop is one query - the service
    layer loops for multi-hop so beam scoring can prune between hops."""

    #: When True, the read path filters the current view with the half-open
    #: range-containment predicate instead of the boolean one, so the GiST
    #: index from migration ``v2.5.5_kw_011`` can be used. **Default False**:
    #: flipping it is gated on (1) the ``kw_011`` preflight returning zero
    #: degenerate rows (``invalid_at < valid_at``) and (2) ``EXPLAIN``
    #: confirming the planner picks ``ix_kr_valid_range``. Registered as a
    #: wiring item in the session-B receipt rather than flipped blind.
    use_range_predicate: bool = False

    # ── upserts (idempotent reruns) ────────────────────────────────────

    async def upsert_entities(self, tenant_id: str,
                              ents: list[dict[str, Any]]) -> None:
        """Insert-or-update by (tenant_id, stable_id). Existing rows get
        props merged (incoming keys win) and aliases appended.

        T-18b D1: an explicit ``valid_at`` in the entity dict is honoured on
        insert (business time); the bi-temporal window of an existing row is
        still never touched here (supersede is the service layer's job).

        Batched rewrite (was one DB session + one query + one commit PER ROW):
        N rows previously cost ~2N round-trips + N commits. Now the cost is
        ceil(S/K) existence lookups (S = distinct stable_ids, one lookup per
        K-key chunk) + ceil(inserts/K) INSERTs + ceil(updates/K) UPDATEs,
        where K = ``self.batch_size`` (default ``DEFAULT_GRAPH_BATCH_SIZE``).
        For the common all-new case that is ceil(N/K) + ceil(N/K) round-trips
        in a SINGLE commit - e.g. 20000 rows at K=1000 is ~40 round-trips, not
        40000.
        The lookup is chunked, not one giant ``IN`` list: a single IN clause
        carrying every candidate key makes the PostgreSQL parser recurse once
        per element and abort the whole write with ``StatementTooComplex:
        stack depth limit exceeded`` (measured: 30000 relation keys on PG
        16.15 with the default 2MB max_stack_depth).
        Semantics are preserved exactly: the lookup reuses the unique
        (tenant_id, stable_id) constraint; updates merge props (incoming wins)
        and append+dedup aliases without touching the bi-temporal window; an
        explicit ``valid_at`` is honoured on insert only. A working copy of
        the existing state tracks in-batch duplicates so repeated stable_ids
        in one call behave like the original sequential loop (deterministic
        and idempotent rerun)."""
        if not ents:
            return
        K = _batch_size_for(self)
        with _get_db_session() as session:
            # 1) Existence lookup for all candidate keys, CHUNKED by K
            #    (reuses the unique (tenant_id, stable_id) lookup). One
            #    statement per chunk: a single IN carrying every key blows the
            #    PostgreSQL parser's recursion budget (StatementTooComplex).
            #    Result: sid -> {id, props, aliases}.
            existing: dict[str, dict[str, Any]] = {}
            sids = list({e["stable_id"] for e in ents})
            for i in range(0, len(sids), K):
                rows = session.execute(
                    select(KgEntity.id, KgEntity.stable_id,
                           KgEntity.props, KgEntity.aliases).where(
                        KgEntity.tenant_id == tenant_id,
                        KgEntity.stable_id.in_(sids[i:i + K]),
                    )
                ).all()
                for r in rows:
                    existing[r.stable_id] = {
                        "id": r.id,
                        "props": dict(r.props or {}),
                        "aliases": list(r.aliases or []),
                    }
            # 2) Decide insert vs update, exactly mirroring the old per-row
            #    semantics (incoming props win, aliases appended + deduped).
            to_insert: list[dict[str, Any]] = []
            to_update: list[dict[str, Any]] = []
            for e in ents:
                sid = e["stable_id"]
                if sid in existing:
                    cur = existing[sid]
                    merged = dict(cur["props"])
                    merged.update(e.get("props") or {})
                    new_aliases = list(cur["aliases"])
                    for a in (e.get("aliases") or []):
                        if a not in new_aliases:
                            new_aliases.append(a)
                    to_update.append({
                        "id": cur["id"],
                        "props": merged,
                        "aliases": new_aliases,
                    })
                    existing[sid] = {"id": cur["id"],
                                     "props": merged, "aliases": new_aliases}
                else:
                    row: dict[str, Any] = {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant_id, "stable_id": sid,
                        "name": e.get("name", sid),
                        "aliases": e.get("aliases") or [],
                        "class_ref": e.get("class_ref") or "Unknown",
                        "props": e.get("props") or {},
                        "embedding": e.get("embedding"),
                        "status": e.get("status") or "active",
                    }
                    if e.get("valid_at") is not None:
                        row["valid_at"] = e["valid_at"]
                    to_insert.append(row)
                    existing[sid] = {"id": row["id"],
                                     "props": row["props"],
                                     "aliases": row["aliases"]}
            # 3) Bulk writes, chunked by K. executemany => one round-trip per
            #    statement; the session __exit__ commits once for the whole call.
            for i in range(0, len(to_insert), K):
                session.execute(insert(KgEntity), to_insert[i:i + K])
            for i in range(0, len(to_update), K):
                session.bulk_update_mappings(
                    KgEntity, to_update[i:i + K])

    async def upsert_relations(self, tenant_id: str,
                               rels: list[dict[str, Any]]) -> None:
        """Insert-or-update by (tenant_id, src, dst, rel_type). Same claim
        is a no-op (idempotent rerun); a different claim inserts a new row
        (conflict resolution is the service layer's job, not the store's).

        T-18b D1: an explicit ``valid_at`` in the relation dict is honoured
        on insert, so a fact's business time is the source document's
        publication date rather than the ingest wall clock.

        Batched rewrite (was one DB session + one query + one commit PER ROW):
        N rows previously cost ~2N round-trips + N commits. Now the cost is
        ceil(Kc/K) existence lookups (Kc = distinct candidate keys, one lookup
        per K-key chunk, under valid_now) + ceil(inserts/K) INSERTs, where
        K = self.batch_size (default ``DEFAULT_GRAPH_BATCH_SIZE``). Relations
        have NO unique constraint (unlike entities), so we cannot use
        ON CONFLICT; instead we do ``valid_now``-filtered lookups of the
        current rows for the candidate keys (the same predicate the old
        per-row query used - reused from ``knowevo_db.valid_now``, not
        re-derived), then INSERT only the genuinely-new rows plus rows whose
        claim differs from the current one.
        A working copy of the current-view claim tracks in-batch duplicates so
        repeated keys in one call reproduce the original sequential loop
        (deterministic, idempotent rerun)."""
        if not rels:
            return
        K = _batch_size_for(self)
        with _get_db_session() as session:
            # 1) Current-view existence lookup for all candidate keys, CHUNKED
            #    by K. Reuses valid_now() so the temporal semantics stay in
            #    exactly one place. Chunking is load-bearing, not cosmetic: one
            #    IN clause holding every candidate triple makes the PostgreSQL
            #    parser recurse per tuple and the write dies with
            #    ``StatementTooComplex: stack depth limit exceeded`` (measured
            #    on PG 16.15 at 30000 relation keys).
            existing: dict[tuple[str, str, str], str] = {}
            keys = list({(r["src"], r["dst"], r["rel_type"]) for r in rels})
            for i in range(0, len(keys), K):
                rows = session.execute(
                    select(KgRelation.src, KgRelation.dst,
                           KgRelation.rel_type, KgRelation.claim).where(
                        KgRelation.tenant_id == tenant_id,
                        valid_now(KgRelation),
                        sa_tuple(KgRelation.src, KgRelation.dst,
                                 KgRelation.rel_type).in_(keys[i:i + K]),
                    )
                ).all()
                for r in rows:
                    existing[(r.src, r.dst, r.rel_type)] = r.claim
            # 2) Decide inserts, exactly mirroring the old per-row semantics.
            to_insert: list[dict[str, Any]] = []
            for r in rels:
                key = (r["src"], r["dst"], r["rel_type"])
                claim = r.get("claim", "")
                if key in existing and existing[key] == claim:
                    continue  # same claim under current view -> no-op
                row: dict[str, Any] = {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "src": r["src"], "dst": r["dst"],
                    "rel_type": r["rel_type"], "claim": claim,
                    "props": r.get("props") or {},
                }
                if r.get("valid_at") is not None:
                    row["valid_at"] = r["valid_at"]
                to_insert.append(row)
                # Update working view so a later duplicate key in this call
                # sees this claim (matches the original sequential loop).
                existing[key] = claim
            # 3) Bulk INSERT, chunked by K. executemany => one round-trip per
            #    statement; the session __exit__ commits once for the whole call.
            for i in range(0, len(to_insert), K):
                session.execute(insert(KgRelation), to_insert[i:i + K])

    # ── queries ────────────────────────────────────────────────────────

    async def neighbors(self, tenant_id: str, entity_ids: list[str],
                        rel_types: list[str] | None = None,
                        hop: int = 1,
                        valid_view: bool = True,
                        as_of: datetime | None = None) -> Subgraph:
        """Neighborhood as of a knowledge version, or the current view.

        ``as_of`` (T-09 version pinning) is an optional keyword extension:
        when given, both the edge and entity predicates are evaluated at
        that instant instead of now(), so the walk runs inside G_v - the
        subgraph of facts valid under that knowledge version
        (02-tech-plan 3.2). Passing ``as_of`` keeps ``valid_view=True``
        semantics ("currently valid" becomes "valid at t_v"); it is not a
        switch to the historical view. The ABC in this module intentionally
        keeps the shorter signature: only PG needs the instant, and a
        default of None preserves every existing caller.
        """
        if not entity_ids:
            return Subgraph()
        seen: set[str] = set(entity_ids)
        seen_edges: set[Any] = set()
        edges: list[EdgeCard] = []
        frontier: list[str] = list(entity_ids)
        for _ in range(max(1, hop)):
            if not frontier:
                break
            with _get_db_session() as session:
                q = session.query(KgRelation).filter(
                    KgRelation.tenant_id == tenant_id,
                )
                if rel_types:
                    q = q.filter(KgRelation.rel_type.in_(rel_types))
                if valid_view:
                    q = q.filter(current_view_predicate(
                        KgRelation, as_of, range_form=self.use_range_predicate))
                else:
                    q = q.filter(KgRelation.invalid_at.isnot(None))
                q = q.filter(
                    (KgRelation.src.in_(frontier)) |
                    (KgRelation.dst.in_(frontier))
                )
                # convert to cards inside the session (commit expires ORM
                # rows; touching attributes afterwards raises
                # DetachedInstanceError)
                rows = [_relation_to_card(r) for r in q.all()]
            next_frontier: list[str] = []
            for card in rows:
                # Novelty is tracked per edge, not per endpoint: an edge
                # whose two endpoints are both seeds (a-b when the caller
                # seeded {a, b}) is incident to the frontier and belongs in
                # the neighborhood. Judging it by "is an endpoint unseen"
                # dropped exactly those edges - pitfall #32.
                if card.id not in seen_edges:
                    seen_edges.add(card.id)
                    edges.append(card)
                for sid in (card.src, card.dst):
                    if sid not in seen:
                        seen.add(sid)
                        next_frontier.append(sid)
            frontier = next_frontier
        with _get_db_session() as session:
            q = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id,
                KgEntity.stable_id.in_(seen),
            )
            if valid_view:
                q = q.filter(KgEntity.status == "active")
                if as_of is not None:
                    q = q.filter(current_view_predicate(
                        KgEntity, as_of, range_form=self.use_range_predicate))
            entities = [_entity_to_card(r) for r in q.all()]
        return Subgraph(entities=entities, edges=edges)

    async def multi_hop(self, tenant_id: str, seeds: list[str],
                        hop_plan: HopPlan,
                        beam: int = 3, depth: int = 3,
                        as_of: datetime | None = None) -> list[Path]:
        """Greedy beam walk: at each depth expand the beam frontier one hop
        and keep the beam-most paths by length (no cycles). Ranking is
        lexical in v0 - T-09 layers PPR/embedding scoring on top.

        ``as_of`` (T-09) pins every expansion step to a knowledge-version
        cutoff, so the walk only ever traverses facts valid under that
        version (02-tech-plan 3.2).
        """
        if not seeds:
            return []
        depth = min(depth, 3)  # guardrail: 3 is the max hop (memo 10)
        beam = max(1, min(beam, 5))
        paths: list[Path] = [Path(entities=[s]) for s in seeds]
        for _ in range(depth):
            tails = [p.entities[-1] for p in paths]
            sub = await self.neighbors(
                tenant_id, tails,
                rel_types=hop_plan.rel_types,
                hop=1, valid_view=True, as_of=as_of)
            by_entity: dict[str, list[EdgeCard]] = {}
            for e in sub.edges:
                by_entity.setdefault(e.src, []).append(e)
                by_entity.setdefault(e.dst, []).append(e)
            new_paths: list[Path] = []
            expanded = False
            for p in paths:
                tail = p.entities[-1]
                candidates = by_entity.get(tail, [])
                hop_edges = [e for e in candidates[:beam]
                             if (e.dst if e.src == tail else e.src)
                             not in p.entities]
                if not hop_edges:
                    # Dead end: the path survives unchanged so callers can
                    # tell "stopped here" from "never started".
                    new_paths.append(p)
                    continue
                expanded = True
                for edge in hop_edges[:beam]:
                    nxt = edge.dst if edge.src == tail else edge.src
                    new_paths.append(Path(
                        entities=p.entities + [nxt],
                        edges=p.edges + [edge.id],
                        claims=p.claims + [edge.claim],
                    ))
            paths = sorted(new_paths, key=lambda p: len(p.entities),
                           reverse=True)[:beam]
            if not expanded:
                break  # nothing anywhere could take another hop
        return paths

    async def supersede(self, tenant_id: str, edge_ids: list[uuid.UUID],
                        invalid_at: datetime, reason: str) -> None:
        """Batch bi-temporal invalidation: stamp invalid_at + superseded_at
        and record the reason in props (never delete the row)."""
        with _get_db_session() as session:
            for eid in edge_ids:
                row = session.query(KgRelation).filter(
                    KgRelation.tenant_id == tenant_id,
                    KgRelation.id == eid,
                    KgRelation.invalid_at.is_(None),
                ).first()
                if row is None:
                    continue
                row.invalid_at = invalid_at
                row.superseded_at = invalid_at
                props = dict(row.props or {})
                props["supersede_reason"] = reason
                row.props = props
            session.flush()

    async def reachable_decisions(self, tenant_id: str,
                                  entity_ids: list[str]) -> list[uuid.UUID]:
        """Decisions whose evidence references any of these entities.
        Uses the GIN index on kg_evidence_t.entity_refs - no traversal."""
        if not entity_ids:
            return []
        with _get_db_session() as session:
            rows = (
                session.query(KgEvidence.id)
                .filter(KgEvidence.tenant_id == tenant_id,
                        KgEvidence.entity_refs.overlap(entity_ids))
                .all()
            )
        return [r[0] for r in rows]

    async def entity_lookup(self, tenant_id: str, query: str,
                            top_k: int = 5) -> list[EntityCard]:
        """Lexical name lookup with alias fallback (PG GIN).

        ES redundancy index (name+summary in Elasticsearch) is NOT wired
        yet - T-08 owns the ES write path and the switch to the ES-first
        lookup; until then this is the slow-but-complete PG path. The seam
        shape (method + return type) is final, matching graph_store.py.md."""
        q = query.strip()
        if not q:
            return []
        cards: list[EntityCard] = []
        with _get_db_session() as session:
            rows = (
                session.query(KgEntity)
                .filter(KgEntity.tenant_id == tenant_id,
                        KgEntity.status == "active",
                        KgEntity.name.ilike(f"%{q}%"))
                .limit(top_k)
                .all()
            )
            cards = [_entity_to_card(r) for r in rows]
            if len(cards) < top_k:
                for r in session.query(KgEntity).filter(
                        KgEntity.tenant_id == tenant_id,
                        KgEntity.status == "active"):
                    if _entity_to_card(r).stable_id in {c.stable_id
                                                        for c in cards}:
                        continue
                    for a in (r.aliases or []):
                        if isinstance(a, dict) and q in a.get("alias", ""):
                            cards.append(_entity_to_card(r))
                            break
                    if len(cards) >= top_k:
                        break
        return cards[:top_k]

    async def stats(self, tenant_id: str, scope: str) -> dict:
        """Scale numbers for dashboards / cost ledger. scope in
        {graph, full}; full adds the pending pool count."""
        with _get_db_session() as session:
            n_entities = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id).count()
            n_edges = session.query(KgRelation).filter(
                KgRelation.tenant_id == tenant_id).count()
            n_edges_valid = session.query(KgRelation).filter(
                KgRelation.tenant_id == tenant_id,
                valid_now(KgRelation)).count()
        out = {"tenant_id": str(tenant_id), "scope": scope,
               "entities": n_entities,
               "edges_total": n_edges, "edges_valid": n_edges_valid}
        if scope == "full":
            with _get_db_session() as session:
                out["pending"] = session.query(KgPendingEntity).filter(
                    KgPendingEntity.tenant_id == tenant_id).count()
        return out