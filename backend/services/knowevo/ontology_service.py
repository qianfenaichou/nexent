"""
KnowEvo ontology semi-automatic construction pipeline (K1 core, T-04).

Business logic only: seed extraction, two-level proposals, rule-based
validation (V1-V5/V9 auto, V6/V7/V8 report), ranking with the auto_accept
gate, versioned commits, K0 quality metrics, and the ontology-summary
truncation guard. HTTP parsing/auth lives in the future
knowledge_graph_app.py; LLM calls are injected as an async callable so
tests (and any model tier swap) never patch module state.

Interface contract frozen in knowevo/backend/services/knowevo/
ontology_service.py.md; algorithm source: memo 02-K1 (competition tree).
"""
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from consts.const import KW_AUTO_ACCEPT_LINE

logger = logging.getLogger(__name__)

# K1 §2: first-level nomination uses the mid tier (signal is enough, 5x
# cheaper); schema assembly uses the large tier. Tier routing is the
# caller's concern - this module only tags which tier each call wants.
TIER_MID = "mid"
TIER_LARGE = "large"

# K1 §4 V3: allowed property types.
ALLOWED_PROP_TYPES = {"string", "float", "int", "bool", "enum[]", "date", "ref"}

# K1 §5: auto-accept line, three-condition AND gate.
AUTO_ACCEPT_LINE = KW_AUTO_ACCEPT_LINE

# K1 §5: 15k token injection cap for ontology summaries (L4 carried value).
ONTOLOGY_SUMMARY_TOKEN_LIMIT = 15000

# K1 §5: proposal batch ceiling per expert session (30-minute constraint).
MAX_PROPOSALS_PER_SESSION = 40

W_DEFAULT = {"w_c": 0.5, "w_n": 0.2, "w_i": 0.3}

# V5 depth cap (Dep constraint).
MAX_DEPTH = 5


# ---------------------------------------------------------------------------
# Proposal dataclasses (K1 data contract, framework-package .md frozen)
# ---------------------------------------------------------------------------

@dataclass
class ConceptProposal:
    """First-level proposal: a class candidate with evidence anchors."""
    name: str
    aliases: list[str] = field(default_factory=list)
    parent_stable_id: str | None = None
    evidence_spans: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    novelty: float = 0.0
    impact: float = 0.0
    rationale: str = ""
    status: str = "pending"  # pending | confirmed | rejected | auto_accepted
    duplicate_of: str | None = None  # V6 flag
    granularity_hint: str | None = None  # V7 flag

    @property
    def target(self) -> str:
        return f"cls:{self.name}"


@dataclass
class SchemaProposal:
    """Second-level proposal: property / relation-type for a confirmed class."""
    class_name: str
    prop_name: str = ""
    prop_type: str = ""
    rel_type: str = ""
    rel_domain: str = ""
    rel_range: str = ""
    evidence_spans: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    novelty: float = 0.0
    impact: float = 0.0
    rationale: str = ""
    status: str = "pending"

    @property
    def target(self) -> str:
        if self.prop_name:
            return f"prop:{self.class_name}.{self.prop_name}"
        return f"rel:{self.rel_type}"


@dataclass
class SeedSkeleton:
    """Stage-0 output: section tree (S1) + per-chapter top-K terms (S2)."""
    section_tree: list[dict[str, Any]] = field(default_factory=list)
    concepts: list[dict[str, Any]] = field(default_factory=list)
    chapter_hashes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested directly)
# ---------------------------------------------------------------------------

def score_formula(conf: float, novelty: float, impact: float,
                   weights: dict[str, float] = W_DEFAULT) -> float:
    """K1 §3: score = w_c*conf + w_n*novelty + w_i*impact."""
    return (weights["w_c"] * conf + weights["w_n"] * novelty
            + weights["w_i"] * impact)


def build_ev_rich(evidence_spans: list[dict[str, Any]]) -> float:
    """K1 §3: >=2 distinct documents -> 1.0, single document -> 0.6."""
    docs = {span.get("doc") for span in evidence_spans}
    return 1.0 if len(docs) >= 2 else 0.6


def normalize_name(raw: str, *, kind: str) -> str:
    """V4: class UpperCamelCase, property lower_snake. Chinese aliases live
    in the separate aliases field, so strip CJK from the name here."""
    # Drop CJK chars (they belong to `aliases`, never the class identifier)
    ascii_part = "".join(ch for ch in raw if not ("\u4e00" <= ch <= "\u9fff"))
    if kind == "class":
        words = [w for w in re.split(r"[\s\-_]+", ascii_part.strip()) if w]
        return "".join(w[:1].upper() + w[1:] for w in words)
    if kind == "prop":
        words = [w.lower() for w in re.split(r"[\s\-_]+", ascii_part.strip()) if w]
        return "_".join(words)
    raise ValueError(f"unknown kind: {kind}")


def estimate_tokens_cjk(obj: Any) -> int:
    """Rough token estimate: 1 token per CJK char, 1 per 2 ASCII chars.
    Only used for the 15k injection guard, never for billing."""
    s = str(obj)
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(s) - cjk) // 2


def truncate_ontology_summary(classes: list[dict[str, Any]],
                              token_limit: int = ONTOLOGY_SUMMARY_TOKEN_LIMIT
                              ) -> list[dict[str, Any]]:
    """L4 guard: when the serialized ontology exceeds the injection budget,
    keep active classes first (by 'freq'), then trim their property lists
    until the estimate fits."""
    def cls_tokens(c: dict[str, Any]) -> int:
        return estimate_tokens_cjk(c)

    if estimate_tokens_cjk(classes) <= token_limit:
        return classes
    # Active + high-frequency first (K1: "active classes + high-freq props").
    ranked = sorted(classes,
                    key=lambda c: (c.get("active", True) is False,
                                   -(c.get("freq", 0))))
    kept: list[dict[str, Any]] = []
    for c in ranked:
        candidate = kept + [c]
        if estimate_tokens_cjk(candidate) > token_limit:
            # try trimming this class's props
            trimmed = dict(c, props=c.get("props", [])[:2])
            if estimate_tokens_cjk(kept + [trimmed]) <= token_limit:
                kept.append(trimmed)
            continue
        kept.append(candidate[-1])
    return kept


# ---------------------------------------------------------------------------
# Persistence seam: the service never imports the DB session directly in
# business methods; PgStore (below) adapts knowevo_db helpers, tests use
# FakeStore. This is the pattern a2a_agent_db.py established (T-03).
# ---------------------------------------------------------------------------

class PgStore:
    """Real-Postgres adapter over database.knowevo_db helpers."""

    async def save_proposals(self, tenant_id, proposals, round_id, trigger):
        from database.knowevo_db import OntologyChangeProposal, create_row
        count = 0
        for p in proposals:
            create_row(
                OntologyChangeProposal,
                tenant_id=tenant_id,
                round_id=round_id,
                target=_proposal_target(p),
                op=_proposal_op(p),
                payload=_proposal_payload(p),
                confidence=getattr(p, "confidence", None),
                impact=int(getattr(p, "impact", 0) * 10),
                novelty=getattr(p, "novelty", 0.0),
                trigger_source=trigger,
            )
            count += 1
        return count

    async def load_active_snapshot(self, tenant_id):
        from database.knowevo_db import OntologyVersion, _get_db_session
        with _get_db_session() as session:
            row = (
                session.query(OntologyVersion)
                .filter(OntologyVersion.tenant_id == tenant_id,
                        OntologyVersion.status == "published")
                .order_by(OntologyVersion.created_at.desc())
                .first()
            )
            if row is None:
                return {"classes": [], "rel_types": []}
            return row.snapshot

    async def load_active_version_row(self, tenant_id):
        """Latest published version row, or None when nothing is published.

        Additive companion to ``load_active_snapshot``: the workbench tree
        panel needs the version label, status and metrics alongside the
        snapshot, while the T-04 snapshot-only contract stays frozen. The
        ordering matches ``load_active_snapshot`` (newest created_at first)
        so both read the same active version.
        """
        from database.knowevo_db import OntologyVersion, _get_db_session
        with _get_db_session() as session:
            row = (
                session.query(OntologyVersion)
                .filter(OntologyVersion.tenant_id == tenant_id,
                        OntologyVersion.status == "published")
                .order_by(OntologyVersion.created_at.desc())
                .first()
            )
            if row is None:
                return None
            return {
                "version": row.version,
                "status": row.status,
                "snapshot": row.snapshot,
                "applied_ops": row.applied_ops,
                "metrics": row.metrics,
                "created_at": (row.created_at.isoformat()
                               if row.created_at is not None else None),
            }

    async def save_version(self, tenant_id, version_row):
        from database.knowevo_db import OntologyVersion, create_row
        create_row(OntologyVersion, tenant_id=tenant_id, **version_row)
        return version_row

    async def save_round(self, tenant_id, round_row):
        from database.knowevo_db import EvolutionRound, create_row
        create_row(EvolutionRound, tenant_id=tenant_id, **round_row)
        return round_row

    async def list_proposal_targets(self, tenant_id, round_id):
        from database.knowevo_db import OntologyChangeProposal, _get_db_session
        with _get_db_session() as session:
            rows = session.query(OntologyChangeProposal.target).filter(
                OntologyChangeProposal.tenant_id == tenant_id,
                OntologyChangeProposal.round_id == round_id,
            ).all()
            return [r.target for r in rows]

    async def list_versions(self, tenant_id):
        """Version rows (with applied_ops) for diff; latest last.

        ``created_at`` is included so version-clock resolution (version_pin)
        can fall back to a version's creation instant when no explicit
        ``fact_cutoff`` is recorded - without it the fallback would silently
        degrade to ``now()`` and the pin would stop discriminating.
        """
        from database.knowevo_db import OntologyVersion, _get_db_session
        with _get_db_session() as session:
            rows = (
                session.query(OntologyVersion)
                .filter(OntologyVersion.tenant_id == tenant_id)
                .order_by(OntologyVersion.created_at.asc())
                .all()
            )
            return [
                {"version": r.version, "status": r.status,
                 "applied_ops": r.applied_ops, "snapshot": r.snapshot,
                 "created_at": r.created_at}
                for r in rows
            ]

    # ── T-05 additions (appended only; T-04 methods above are frozen) ──

    async def list_queue_rows(self, tenant_id, round_id=None, status="pending"):
        """Review-queue rows for one tenant, newest first, with the
        ranking features the workbench sorts on."""
        from database.knowevo_db import OntologyChangeProposal, _get_db_session
        with _get_db_session() as session:
            q = session.query(OntologyChangeProposal).filter(
                OntologyChangeProposal.tenant_id == tenant_id)
            if status:
                q = q.filter(OntologyChangeProposal.status == status)
            if round_id is not None:
                q = q.filter(OntologyChangeProposal.round_id == round_id)
            rows = q.order_by(OntologyChangeProposal.created_at.desc()).all()
            return [
                {"id": str(r.id), "round_id": str(r.round_id), "target": r.target,
                 "op": r.op, "payload": r.payload,
                 "confidence": r.confidence, "impact": r.impact,
                 "novelty": r.novelty, "status": r.status,
                 "trigger_source": r.trigger_source}
                for r in rows
            ]

    async def get_proposal_rows(self, tenant_id, ids):
        """Queue rows by id under tenant isolation. Returns [] rows plus the
        missing-id list so the caller can 404 precisely."""
        from database.knowevo_db import OntologyChangeProposal, _get_db_session
        with _get_db_session() as session:
            rows = (
                session.query(OntologyChangeProposal)
                .filter(OntologyChangeProposal.tenant_id == tenant_id,
                        OntologyChangeProposal.id.in_(ids))
                .all()
            )
            found = {
                str(r.id): {"id": str(r.id), "target": r.target, "op": r.op,
                            "payload": r.payload, "status": r.status}
                for r in rows
            }
        missing = [i for i in ids if i not in found]
        return found, missing

    async def set_proposal_status(self, tenant_id, ids, status,
                                  reviewed_by=None, reject_reason=None,
                                  new_parent=None):
        """Batch status transition for reviewed proposals (pending ->
        confirmed/rejected/reparented)."""
        from database.knowevo_db import OntologyChangeProposal, _get_db_session
        with _get_db_session() as session:
            rows = (
                session.query(OntologyChangeProposal)
                .filter(OntologyChangeProposal.tenant_id == tenant_id,
                        OntologyChangeProposal.id.in_(ids))
                .all()
            )
            for r in rows:
                r.status = status
                r.reviewed_by = reviewed_by
                r.reject_reason = reject_reason
                if new_parent and r.op == "CLS_ADD":
                    payload = dict(r.payload or {})
                    payload["parent"] = new_parent
                    r.payload = payload
            return len(rows)

    async def get_version_row(self, tenant_id, version):
        from database.knowevo_db import OntologyVersion, _get_db_session
        with _get_db_session() as session:
            row = (
                session.query(OntologyVersion)
                .filter(OntologyVersion.tenant_id == tenant_id,
                        OntologyVersion.version == version)
                .first()
            )
            if row is None:
                return None
            return {"version": row.version, "metrics": row.metrics,
                    "snapshot": row.snapshot, "applied_ops": row.applied_ops}


def _proposal_target(p: Any) -> str:
    if isinstance(p, dict):
        return p.get("target", "")
    return getattr(p, "target", "")


def _proposal_op(p: Any) -> str:
    if isinstance(p, dict):
        return p.get("op", "CLS_ADD")
    if isinstance(p, SchemaProposal):
        return "PROP_ADD" if p.prop_name else "REL_ADD"
    return "CLS_ADD"


def _proposal_payload(p: Any) -> dict[str, Any]:
    if isinstance(p, dict):
        return p
    if isinstance(p, SchemaProposal):
        return {
            "class": p.class_name, "prop_name": p.prop_name,
            "prop_type": p.prop_type, "rel_type": p.rel_type,
            "rel_domain": p.rel_domain, "rel_range": p.rel_range,
            "evidence_spans": p.evidence_spans,
            "confidence": p.confidence, "rationale": p.rationale,
            "status": p.status,
        }
    d = {k: getattr(p, k) for k in (
        "name", "aliases", "parent_stable_id", "evidence_spans",
        "confidence", "rationale", "status") if hasattr(p, k)}
    return d


def _row_to_op(row: dict[str, Any]) -> dict[str, Any]:
    """Queue row (from list_queue_rows / get_proposal_rows) -> commit op.
    CLS_ADD/PROP_ADD/REL_ADD payloads already carry the fields _apply_ops
    consumes; reparented rows have their payload['parent'] rewritten in
    place by PgStore.set_proposal_status."""
    payload = dict(row.get("payload") or {})
    if row.get("op") == "CLS_ADD":
        payload.setdefault("name", row.get("target", "").removeprefix("cls:"))
    return {"op": row.get("op", "CLS_ADD"), "target": row.get("target", ""),
            "payload": payload}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class OntologyService:
    """K1 pipeline: seed -> two-level proposals -> validate -> rank ->
    auto_accept -> confirm -> versioned commit -> quality metrics."""

    def __init__(self, store=None, llm: Any | None = None):
        # store: FakeStore (unit) or PgStore (integration); llm: async
        # callable(prompt, kind=..., tier=...) returning parsed proposals.
        self.store = store
        self.llm = llm

    # ── Stage 0: seed ────────────────────────────────────────────────

    async def extract_seed(self, doc_ids: list[str],
                           parsed_docs: list[dict[str, Any]] | None = None
                           ) -> SeedSkeleton:
        """S1 section tree (depth<=3) + S2 per-chapter top-K term
        nomination with aliases and expected parent; dedupes across docs."""
        skeleton = SeedSkeleton()
        seen_terms: dict[str, dict[str, Any]] = {}
        for doc in parsed_docs or []:
            skeleton.section_tree.extend(_build_section_tree(doc.get("toc", [])))
            for term in doc.get("terms", []):
                name = term["name"]
                if name not in seen_terms:
                    seen_terms[name] = {
                        "name": name,
                        "aliases": term.get("aliases", []),
                        "section": term.get("section", ""),
                        "parent": term.get("parent", ""),
                        "doc": doc.get("title", ""),
                    }
            skeleton.chapter_hashes.append(
                hashlib.sha1(
                    (doc.get("title", "") + "|".join(doc.get("toc", [])))
                    .encode("utf-8")
                ).hexdigest()
            )
        skeleton.concepts = list(seen_terms.values())
        return skeleton

    # ── Stage 1: concept nomination (first level) ────────────────────

    async def propose_concepts(self, seed: SeedSkeleton,
                               ontology_summary: str | None,
                               ) -> list[ConceptProposal]:
        """Batch per-chapter, temp 0.2, structured output via the injected
        mid-tier LLM. The prompt carries few-shot examples (3, cross-domain
        generic) and the current skeleton summary (truncated to 15k)."""
        summary = ontology_summary or ""
        if estimate_tokens_cjk(summary) > ONTOLOGY_SUMMARY_TOKEN_LIMIT:
            summary = summary[:ONTOLOGY_SUMMARY_TOKEN_LIMIT]
        raw = await self.llm(
            _concepts_prompt(seed, summary), kind="concepts", tier=TIER_MID,
            temperature=0.2,
        )
        return [_to_concept(r) for r in raw]

    # ── Stage 4: schema assembly (second level) ──────────────────────

    async def propose_schema(self, confirmed_classes: list[dict[str, Any]],
                             evidence: list[dict[str, Any]],
                             ) -> list[SchemaProposal]:
        """Props / rel-types per confirmed class via the large tier."""
        raw = await self.llm(
            _schema_prompt(confirmed_classes, evidence),
            kind="schema", tier=TIER_LARGE, temperature=0.2,
        )
        return [_to_schema(r) for r in raw]

    # ── Stage 2/5: validation (V1-V5, V9 auto; V6/V7/V8 flag) ────────

    def autofix(self, proposals: list[Any]) -> tuple[list[Any], list[str]]:
        """Auto-repair/keep what the machine can (V1-V5, V9); reject with a
        reason string per dropped proposal. The semantic rules (V6 duplicate
        via embedding similarity, V7 granularity, V8 orphan) need the
        embedding channel that T-06 wires - v0 carries their flag fields
        (duplicate_of / granularity_hint) but performs no detection; the
        expert sees those flags once T-06 lands, not silently here."""
        kept: list[Any] = []
        rejected: list[str] = []
        for p in proposals:
            if isinstance(p, SchemaProposal):
                # V3: prop type must be in the whitelist
                if p.prop_name and p.prop_type not in ALLOWED_PROP_TYPES:
                    rejected.append(f"V3:illegal_prop_type:{p.prop_type}")
                    continue
                # V9: relation domain/range must be non-empty when rel
                if p.rel_type and not (p.rel_domain and p.rel_range):
                    rejected.append(f"V9:rel_missing_domain_or_range:{p.rel_type}")
                    continue
                # V4 normalization
                if p.prop_name:
                    p.prop_name = normalize_name(p.prop_name, kind="prop")
                kept.append(p)
                continue
            # ConceptProposal path
            p.name = normalize_name(p.name, kind="class")
            if not p.name:
                rejected.append("V4:empty_name")
                continue
            if p.parent_stable_id == p.name:  # V1 self-loop
                rejected.append(f"V1:self_parent:{p.name}")
                continue
            kept.append(p)
        return kept, rejected

    def validate_cycle(self, parent_map: dict[str, str | None]) -> bool:
        """V1: no cycles in the proposed parent chain (DFS)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {node: WHITE for node in parent_map}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            parent = parent_map.get(node)
            if parent is not None and parent in color:
                if color[parent] == GRAY:
                    return False
                if color[parent] == WHITE and not dfs(parent):
                    return False
            color[node] = BLACK
            return True

        for node in parent_map:
            if color[node] == WHITE and not dfs(node):
                return False
        return True

    def check_depth(self, new_class: str, parent: str,
                    parent_map: dict[str, str | None]) -> bool:
        """V5: mounting new_class under parent must not exceed depth 5."""
        depth = 1
        current = parent
        while current is not None and current in parent_map:
            depth += 1
            if depth > MAX_DEPTH:
                return False
            current = parent_map.get(current)
        return depth <= MAX_DEPTH

    # ── Ranking + auto-accept (K1 §3) ─────────────────────────────────

    async def rank_proposals(self, proposals: list[Any],
                             weights: dict[str, float] = W_DEFAULT
                             ) -> list[Any]:
        """Score each proposal, sort desc, break ties with ev_rich.

        No truncation here: dropping low-ranked proposals would lose them
        entirely (they are still queue rows). The 40-per-session batch
        ceiling (K1 §3, MAX_PROPOSALS_PER_SESSION) is a confirm-loop
        concern - T-05 slices the ranked queue into sessions; persistence
        and ranking keep everything.
        """

        def key(p):
            ev = build_ev_rich(getattr(p, "evidence_spans", []))
            s = score_formula(p.confidence, p.novelty, p.impact, weights)
            return (s, ev)

        return sorted(proposals, key=key, reverse=True)

    async def auto_accept(self, queue: list[Any], threshold: float
                          ) -> tuple[list[Any], list[Any]]:
        """Three-condition AND gate: conf >= threshold AND validator-pass
        AND ev_rich == 1.0 (evidence crosses >=2 documents)."""
        auto, manual = [], []
        for p in queue:
            if (getattr(p, "confidence", 0.0) >= threshold
                    and getattr(p, "status", "pending") != "rejected"
                    and build_ev_rich(getattr(p, "evidence_spans", [])) == 1.0):
                p.status = "auto_accepted"
                auto.append(p)
            else:
                manual.append(p)
        return auto, manual

    # ── Stage 6: versioning ──────────────────────────────────────────

    async def commit_version(self, round_id, applied_ops: list[dict[str, Any]],
                             base_version: str | None = None,
                             tenant_id: str | None = None,
                             ) -> dict[str, Any]:
        """Apply the op log onto the parent snapshot, persist a new row in
        ontology_version_t, bump semver (minor for additions, major for
        deprecations), and compute K0 metrics into the row."""
        base = await self.store.load_active_snapshot(tenant_id or "")
        snapshot = _apply_ops(base, applied_ops)
        version = _bump_semver(base_version, applied_ops)
        metrics = _k0_metrics_from_snapshot(snapshot)
        row = {
            "id": uuid.uuid4(),
            "version": version,
            "parent_id": None,
            "status": "published",
            "snapshot": snapshot,
            "applied_ops": applied_ops,
            "metrics": metrics,
            "created_by": "ontology_service",
        }
        if self.store is not None:
            await self.store.save_version(tenant_id or "", row)
        return row

    async def deprecate(self, class_stable_id: str, reason: str,
                        ) -> dict[str, Any]:
        return {"op": "CLS_DEPRECATE", "target": f"cls:{class_stable_id}",
                "payload": {"reason": reason}}

    async def diff(self, from_v: str, to_v: str,
                   tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Ops between two versions = the ops journaled by every version
        committed after from_v, up to and including to_v (snapshots are
        cumulative; each row's applied_ops is that row's own delta)."""
        rows = getattr(self.store, "versions", None)
        if rows is None and hasattr(self.store, "list_versions"):
            rows = await self.store.list_versions(tenant_id or "")
        if rows is None:
            return []
        ordered = []
        seen = set()
        for v in rows:
            if v["version"] not in seen:
                seen.add(v["version"])
                ordered.append(v)
        versions = [v["version"] for v in ordered]
        if from_v not in versions or to_v not in versions:
            return []
        start, end = versions.index(from_v) + 1, versions.index(to_v)
        ops: list[dict[str, Any]] = []
        for v in ordered[start:end + 1]:
            ops.extend(v.get("applied_ops", []))
        return ops

    # ── Incremental entries (K1 backflow / K5 trigger) ───────────────

    async def propose_from_pending(self, pending: list[dict[str, Any]]
                                   ) -> list[ConceptProposal]:
        """High-frequency unmapped entities -> new-class proposals through
        the same proposal-confirm-version channel (K1 unmappable backflow)."""
        proposals = []
        for e in pending:
            if e.get("mention_count", 0) < 3:  # high-frequency threshold
                continue
            proposals.append(ConceptProposal(
                name=e["name"], confidence=0.5,
                impact=min(1.0, e.get("mention_count", 0) / 10),
                evidence_spans=e.get("evidence_spans", []),
                rationale="pending-pool backflow"))
        return proposals

    # ── Query surface ────────────────────────────────────────────────

    async def get_active(self, tenant_id) -> dict[str, Any]:
        return await self.store.load_active_snapshot(tenant_id)

    async def get_active_row(self, tenant_id) -> dict[str, Any] | None:
        """Active version row (label + snapshot + metrics), or None.

        The workbench's ``GET /ontology/versions/active`` serves this; None
        maps to HTTP 404 so the client can tell "no version committed yet"
        from "here is an empty ontology". Falls back to None when the store
        has no row-level surface (unit fakes), keeping the app layer free
        of store-capability probing.
        """
        if not hasattr(self.store, "load_active_version_row"):
            return None
        return await self.store.load_active_version_row(tenant_id)

    async def quality_metrics(self, snapshot: dict[str, Any],
                              seed_terms: list[str] | None = None,
                              ) -> dict[str, Any]:
        """K0 four metrics on a snapshot: cov/red/dep/align (hand-computed
        fixture in test_ontology_service.py locks the semantics)."""
        return _k0_metrics_from_snapshot(snapshot, seed_terms)

    # ── T-05 confirm loop (appended; T-04 signatures above are frozen) ──

    async def list_review_queue(self, tenant_id: str, page: int = 1,
                                page_size: int = 40, round_id: str | None = None,
                                status: str | None = "pending",
                                ) -> dict[str, Any]:
        """One session slice of the ranked review queue. Rows come back
        score-ordered (rank formula re-computed from the stored features);
        page_size is capped at MAX_PROPOSALS_PER_SESSION (K1 ss3)."""
        page_size = min(page_size, MAX_PROPOSALS_PER_SESSION)
        if not hasattr(self.store, "list_queue_rows"):
            return {"items": [], "total": 0, "page": page,
                    "page_size": page_size}
        rows = await self.store.list_queue_rows(
            tenant_id, round_id=round_id, status=status)
        for r in rows:
            conf = r.get("confidence") or 0.0
            novelty = r.get("novelty") or 0.0
            impact = (r.get("impact") or 0) / 10.0
            spans = (r.get("payload") or {}).get("evidence_spans", [])
            r["score"] = round(score_formula(conf, novelty, impact), 4)
            r["ev_rich"] = build_ev_rich(spans)
        rows.sort(key=lambda r: (r["score"], r["ev_rich"]), reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        return {"items": rows[start:start + page_size], "total": total,
                "page": page, "page_size": page_size}

    async def review_proposals(self, tenant_id: str, ids: list[str],
                               action: str, new_parent: str | None = None,
                               reviewed_by: str | None = None,
                               reject_reason: str | None = None,
                               ) -> dict[str, Any]:
        """Batch review (keyboard flow A/X/P -> confirm/reject/reparent).

        confirm:  status pending -> confirmed (auto_accepted rows stay put)
        reject:   status pending -> rejected with reason
        reparent: rewrite the CLS_ADD payload's parent, then confirm; the
                  V1 cycle check runs over the resulting parent map and a
                  would-be cycle refuses the whole batch (atomic).
        """
        if action not in ("confirm", "reject", "reparent"):
            raise ValueError("action must be confirm|reject|reparent")
        found, missing = await self.store.get_proposal_rows(tenant_id, ids)
        if missing:
            raise ValueError(f"proposals not found: {', '.join(missing)}")
        pending = {i: r for i, r in found.items()
                   if r.get("status") == "pending"}
        not_pending = [i for i in ids if i in found and i not in pending]
        if not_pending:
            raise ValueError(f"proposals not in pending state: "
                             f"{', '.join(sorted(not_pending))}")

        if action == "reparent":
            parent_map = await self._parent_map_for(tenant_id)
            for r in pending.values():
                if r["op"] != "CLS_ADD":
                    raise ValueError("reparent applies only to CLS_ADD proposals")
                name = (r.get("payload") or {}).get("name", "")
                parent_map[name] = new_parent
            if not self.validate_cycle(parent_map):
                raise PermissionError(
                    "reparent would create a parent cycle (V1)")

        status = {"confirm": "confirmed", "reject": "rejected",
                  "reparent": "confirmed"}[action]
        updated = await self.store.set_proposal_status(
            tenant_id, list(pending), status,
            reviewed_by=reviewed_by,
            reject_reason=reject_reason if action == "reject" else None,
            new_parent=new_parent if action == "reparent" else None)
        return {"action": action, "updated": updated,
                "new_parent": new_parent, "status": status}

    async def _parent_map_for(self, tenant_id: str) -> dict[str, str | None]:
        """name -> parent over active snapshot classes plus pending CLS_ADD
        payloads (the world the reparent decision is made against)."""
        snapshot = await self.store.load_active_snapshot(tenant_id)
        parent_map: dict[str, str | None] = {
            c.get("name"): c.get("parent")
            for c in snapshot.get("classes", [])
        }
        if hasattr(self.store, "list_queue_rows"):
            for r in await self.store.list_queue_rows(
                    tenant_id, round_id=None, status="pending"):
                if r.get("op") == "CLS_ADD":
                    payload = r.get("payload") or {}
                    if payload.get("name"):
                        parent_map[payload["name"]] = payload.get("parent")
        return parent_map

    async def commit_from_queue(self, tenant_id: str,
                                round_id: str | None = None,
                                confirmed_ids: list[str] | None = None,
                                base_version: str | None = None,
                                ) -> dict[str, Any]:
        """Fold confirmed proposals into ops and commit a new version.

        Without confirmed_ids every confirmed row of the tenant (optionally
        narrowed by round) is folded - the workbench's commit button after
        a full session.
        """
        if hasattr(self.store, "list_queue_rows"):
            rows = await self.store.list_queue_rows(
                tenant_id, round_id=round_id, status="confirmed")
        else:
            rows = []
        if confirmed_ids is not None:
            wanted = set(confirmed_ids)
            rows = [r for r in rows if r["id"] in wanted]
        ops = [_row_to_op(r) for r in rows]
        if not ops and confirmed_ids:
            raise ValueError("none of the given proposals are confirmed")
        # Deprecations would bump major; the confirm loop only produces
        # additions, so commit_version's semver logic applies unchanged.
        # base_version=None -> first commit lands on v1.0.0 (semver anchor).
        row = await self.commit_version(
            round_id, ops, base_version=base_version, tenant_id=tenant_id)
        # The freshly published version shadows whatever was active before.
        row = dict(row)
        row["folded"] = len(ops)
        return row

    async def version_metrics(self, tenant_id: str, version: str
                             ) -> dict[str, Any] | None:
        """K0 metrics of a committed version row (None when unknown)."""
        if not hasattr(self.store, "get_version_row"):
            return None
        row = await self.store.get_version_row(tenant_id, version)
        if row is None:
            return None
        if row.get("metrics"):
            return row["metrics"]
        return _k0_metrics_from_snapshot(row.get("snapshot") or {})

    # ── Full round (build_ontology pipeline entry) ────────────────────

    async def build_ontology_round(self, tenant_id: str, doc_ids: list[str],
                                   parsed_docs: list[dict[str, Any]],
                                   trigger: str = "seed_bootstrap",
                                   dry_run: bool = False) -> dict[str, Any]:
        """Seed -> concepts -> autofix -> rank -> persist (the T-04 CLI
        wraps exactly this; T-05 confirm loop and stage-4/6 continue from
        the queue). Idempotent per chapter hash: rerunning the same docs
        does not duplicate proposals for the same round."""
        seed = await self.extract_seed(doc_ids, parsed_docs=parsed_docs)
        raw_concepts = await self.propose_concepts(seed, ontology_summary=None)
        concepts, rejected = self.autofix(raw_concepts)
        ranked = await self.rank_proposals(concepts)
        # Round identity derives from the chapter hashes (not a fresh uuid):
        # rerunning the same parsed docs resolves to the same round, which
        # is what makes the persistence idempotent (pipeline README).
        round_id = uuid.uuid5(uuid.NAMESPACE_URL,
                              "kw-round:" + "|".join(seed.chapter_hashes))
        report = {
            "round_id": str(round_id),
            "chapter_hashes": seed.chapter_hashes,
            "proposals_total": len(ranked),
            "proposals_rejected": len(rejected),
            "proposals_saved": 0,
            "dry_run": dry_run,
        }
        if dry_run:
            report["top5_preview"] = [
                {"name": p.name,
                 "score": round(score_formula(
                     p.confidence, p.novelty, p.impact), 4),
                 "status": p.status}
                for p in ranked[:5]
            ]
            return report
        if self.store is None:
            return report
        # Idempotency: skip targets already proposed for this round.
        existing = set()
        if hasattr(self.store, "list_proposal_targets"):
            existing = set(await self.store.list_proposal_targets(
                tenant_id, round_id))
        fresh = [p for p in ranked
                 if (p.target if hasattr(p, "target") else "") not in existing]
        saved = await self.store.save_proposals(
            tenant_id, fresh, round_id, trigger)
        report["proposals_saved"] = saved
        return report


# ---------------------------------------------------------------------------
# Snapshot op application (shared by commit_version and diff)
# ---------------------------------------------------------------------------

def _apply_ops(base: dict[str, Any], ops: list[dict[str, Any]]
               ) -> dict[str, Any]:
    snapshot = {"classes": [dict(c) for c in base.get("classes", [])],
                "rel_types": [dict(r) for r in base.get("rel_types", [])]}
    for op in ops:
        code = op["op"]
        payload = op.get("payload", {})
        if code == "CLS_ADD":
            snapshot["classes"].append(dict(payload, stable_id=payload["name"]))
        elif code == "CLS_DEPRECATE":
            for c in snapshot["classes"]:
                if c.get("name") == payload.get("name") or \
                        f"cls:{c.get('name')}" == op.get("target"):
                    c["deprecated"] = True
        elif code == "PROP_ADD":
            for c in snapshot["classes"]:
                if c.get("name") == payload.get("class"):
                    c.setdefault("props", []).append(
                        {"name": payload["name"], "type": payload.get("type")})
        elif code == "REL_ADD":
            snapshot["rel_types"].append(payload)
        # CLS_UPD/CLS_DEL/PROP_UPD/... are T-05/T-11 extensions; unknown
        # codes are ignored here, not invented (anti-hallucination rule).
    return snapshot


def _bump_semver(base_version: str | None,
                 ops: list[dict[str, Any]]) -> str:
    if not base_version:
        return "v1.0.0"
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", base_version)
    if not m:
        return "v1.0.0"
    major, minor, _patch = (int(x) for x in m.groups())
    if any(o["op"].endswith("DEPRECATE") for o in ops):
        return f"v{major + 1}.0.0"
    return f"v{major}.{minor + 1}.0"


def _k0_metrics_from_snapshot(snapshot: dict[str, Any],
                              seed_terms: list[str] | None = None
                              ) -> dict[str, Any]:
    """K0 §1.3 four metrics:
    cov  - share of classes the seed terms cover
    red  - redundant-parent edges / class count
    dep  - max inheritance depth (capped view: levels, root=1)
    align - share of classes anchored to a standard TOC section
    """
    classes = [c for c in snapshot.get("classes", [])]
    n = len(classes)
    if n == 0:
        return {"cov": 0.0, "red": 0.0, "dep": 0, "align": 0.0}

    by_name = {c["name"]: c for c in classes}
    # cov: seed terms map to classes (fixture passes both the Chinese term
    # and the English class name in one string).
    if seed_terms:
        covered = 0
        for c in classes:
            if any(c["name"] in t for t in seed_terms):
                covered += 1
        cov = covered / n
    else:
        cov = 0.0
    # red: extra parents beyond the first count as redundant edges.
    extra = 0
    for c in classes:
        parents = [c.get("parent")] if c.get("parent") else []
        if c.get("also_parent"):
            parents.append(c["also_parent"])
        extra += max(0, len(parents) - 1)
    red = extra / n
    # dep: longest parent chain.
    dep = 0
    for c in classes:
        depth, cur, seen = 1, c.get("parent"), set()
        while cur is not None and cur in by_name and cur not in seen:
            seen.add(cur)
            depth += 1
            cur = by_name[cur].get("parent")
        dep = max(dep, depth)
    # align: anchored classes share.
    anchored = sum(1 for c in classes if c.get("anchor"))
    align = anchored / n
    return {"cov": round(cov, 4), "red": round(red, 4), "dep": dep,
            "align": round(align, 4)}


def _build_section_tree(toc: list[str]) -> list[dict[str, Any]]:
    """S1: turn '1'/'1.1'/'1.1.2' numbering into a depth<=3 tree."""
    roots: list[dict[str, Any]] = []
    stack: list[tuple[str, dict[str, Any]]] = []  # (number_prefix, node)
    for entry in toc:
        parts = entry.split()
        if not parts:
            continue
        number, title = parts[0], " ".join(parts[1:]) or entry
        depth = number.count(".")
        node = {"title": title, "number": number, "children": []}
        while stack and stack[-1][0] and not number.startswith(stack[-1][0]):
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        if depth < 3:
            stack.append((number, node))
    return roots


def _concepts_prompt(seed: SeedSkeleton, summary: str) -> str:
    lines = ["Nominate domain concepts per chapter from this seed skeleton.",
             "Return JSON: [{name, aliases, parent, confidence, rationale}].",
             "Few-shot examples (3, cross-domain generic) omitted in v0.",
             f"Current ontology summary (<=15k tokens): {summary[:200]}..."]
    for c in seed.concepts[:20]:
        lines.append(f"- term: {c['name']} (parent: {c.get('parent')})")
    return "\n".join(lines)


def _schema_prompt(confirmed_classes, evidence) -> str:
    names = ", ".join(c.get("name", str(c)) for c in confirmed_classes)
    return (f"For each confirmed class propose properties and relation "
            f"types. Classes: {names}. Evidence: {len(evidence)} spans. "
            "Return JSON: [{class, prop_name, prop_type, rel_type, "
            "rel_domain, rel_range, confidence}].")


def _to_concept(raw: dict[str, Any]) -> ConceptProposal:
    return ConceptProposal(
        name=raw.get("name", ""),
        aliases=raw.get("aliases", []),
        parent_stable_id=raw.get("parent") or raw.get("parent_stable_id"),
        evidence_spans=raw.get("evidence_spans", []),
        confidence=float(raw.get("confidence", 0.0)),
        novelty=float(raw.get("novelty", 0.0)),
        impact=float(raw.get("impact", 0.0)),
        rationale=raw.get("rationale", ""),
    )


def _to_schema(raw: dict[str, Any]) -> SchemaProposal:
    return SchemaProposal(
        class_name=raw.get("class", raw.get("class_name", "")),
        prop_name=raw.get("prop_name", ""),
        prop_type=raw.get("prop_type", ""),
        rel_type=raw.get("rel_type", ""),
        rel_domain=raw.get("rel_domain", ""),
        rel_range=raw.get("rel_range", ""),
        evidence_spans=raw.get("evidence_spans", []),
        confidence=float(raw.get("confidence", 0.0)),
        rationale=raw.get("rationale", ""),
    )
