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
)

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
    """One graph edge in query output; evidence_id rides in props."""
    id: Any
    src: str
    dst: str
    rel_type: str
    claim: str
    props: dict[str, Any] = field(default_factory=dict)
    contested: bool = False


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
                        valid_view: bool = True) -> Subgraph: ...

    @abstractmethod
    async def multi_hop(self, tenant_id: str, seeds: list[str],
                        hop_plan: HopPlan,
                        beam: int = 3, depth: int = 3) -> list[Path]: ...

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
    )


class PgJsonbGraphStore(GraphStore):
    """Default adapter: PG JSONB tables with GIN alias index and the
    (valid_at, invalid_at) b-tree. One hop is one query - the service
    layer loops for multi-hop so beam scoring can prune between hops."""

    # ── upserts (idempotent reruns) ────────────────────────────────────

    async def upsert_entities(self, tenant_id: str,
                              ents: list[dict[str, Any]]) -> None:
        """Insert-or-update by (tenant_id, stable_id). Existing rows get
        props merged (incoming keys win) and aliases appended; the
        bi-temporal window is never touched here."""
        for e in ents:
            sid = e["stable_id"]
            with _get_db_session() as session:
                existing = session.query(KgEntity).filter(
                    KgEntity.tenant_id == tenant_id,
                    KgEntity.stable_id == sid,
                ).first()
                if existing is None:
                    session.add(KgEntity(
                        tenant_id=tenant_id, stable_id=sid,
                        name=e.get("name", sid),
                        aliases=e.get("aliases") or [],
                        class_ref=e.get("class_ref") or "Unknown",
                        props=e.get("props") or {},
                        embedding=e.get("embedding"),
                        status=e.get("status") or "active",
                    ))
                else:
                    merged = dict(existing.props or {})
                    merged.update(e.get("props") or {})
                    existing.props = merged
                    existing_aliases = list(existing.aliases or [])
                    for a in (e.get("aliases") or []):
                        if a not in existing_aliases:
                            existing_aliases.append(a)
                    existing.aliases = existing_aliases
                    session.flush()

    async def upsert_relations(self, tenant_id: str,
                               rels: list[dict[str, Any]]) -> None:
        """Insert-or-update by (tenant_id, src, dst, rel_type). Same claim
        is a no-op (idempotent rerun); a different claim inserts a new row
        (conflict resolution is the service layer's job, not the store's)."""
        for r in rels:
            with _get_db_session() as session:
                existing = session.query(KgRelation).filter(
                    KgRelation.tenant_id == tenant_id,
                    KgRelation.src == r["src"],
                    KgRelation.dst == r["dst"],
                    KgRelation.rel_type == r["rel_type"],
                    valid_now(KgRelation),
                ).first()
                if existing is not None and existing.claim == r.get("claim"):
                    continue
                session.add(KgRelation(
                    tenant_id=tenant_id, src=r["src"], dst=r["dst"],
                    rel_type=r["rel_type"], claim=r.get("claim", ""),
                    props=r.get("props") or {},
                ))
                session.flush()

    # ── queries ────────────────────────────────────────────────────────

    async def neighbors(self, tenant_id: str, entity_ids: list[str],
                        rel_types: list[str] | None = None,
                        hop: int = 1,
                        valid_view: bool = True) -> Subgraph:
        """Current (or historical) view of the 1..hop neighborhood. Each
        hop expands from the previous hop's frontier, so callers can prune
        between hops."""
        if not entity_ids:
            return Subgraph()
        seen: set[str] = set(entity_ids)
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
                    q = q.filter(valid_now(KgRelation))
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
                # an edge is new to the subgraph when either endpoint is
                # outside the already-seen set (revisit rows once);
                if card.src not in seen or card.dst not in seen:
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
            entities = [_entity_to_card(r) for r in q.all()]
        return Subgraph(entities=entities, edges=edges)

    async def multi_hop(self, tenant_id: str, seeds: list[str],
                        hop_plan: HopPlan,
                        beam: int = 3, depth: int = 3) -> list[Path]:
        """Greedy beam walk: at each depth expand the beam frontier one hop
        and keep the beam-most paths by length (no cycles). Ranking is
        lexical in v0 - T-09 swaps in PPR/embedding scoring."""
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
                hop=1, valid_view=True)
            by_entity: dict[str, list[EdgeCard]] = {}
            for e in sub.edges:
                by_entity.setdefault(e.src, []).append(e)
                by_entity.setdefault(e.dst, []).append(e)
            new_paths: list[Path] = []
            for p in paths:
                tail = p.entities[-1]
                candidates = by_entity.get(tail, [])
                if not candidates:
                    new_paths.append(p)
                    continue
                hop_edges = [e for e in candidates[:beam]
                             if (e.dst if e.src == tail else e.src)
                             not in p.entities]
                for edge in hop_edges[:beam]:
                    nxt = edge.dst if edge.src == tail else edge.src
                    new_paths.append(Path(
                        entities=p.entities + [nxt],
                        edges=p.edges + [edge.id],
                        claims=p.claims + [edge.claim],
                    ))
            paths = sorted(new_paths, key=lambda p: len(p.entities),
                           reverse=True)[:beam]
            if not any(len(p.entities) > 1 for p in paths):
                break  # no expansion happened anywhere
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