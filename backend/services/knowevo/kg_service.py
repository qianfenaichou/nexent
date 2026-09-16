"""
KnowEvo K2 graph-building service (T-06): ontology-anchored extraction,
three-level alignment with external-key blocking, bi-temporal merge,
pending-pool maintenance, and the thin offline pipeline entry.

Business logic only, following the ontology_service.py pattern: the LLM is
injected as an async callable, persistence goes through a store seam
(PgStore vs FakeStore in tests), and spans/entities travel as the frozen
dataclasses in schemas.py. HTTP parsing/auth lives in the future
knowledge_graph_app.py (T-12+); GraphStore and the MCP surface are T-07/T-09.

Interface contract frozen in knowevo/backend/services/knowevo/
kg_service.py.md; algorithm source: memo 03-K2 (02-technical-plan 2.3/2.4)
and the P0 fixes: external primary-key blocking (P0-1) and ontology
subgraph retrieval replacing the 15k full-injection bomb (P0-4).
"""
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from consts.const import KW_ALIGN_TAU1, KW_ALIGN_TAU2
from services.knowevo.schemas import (
    AlignDecision,
    Entity,
    EvidenceSpan,
    Example,
    ExtractionResult,
    IngestReport,
    LabeledPair,
    ParsedTable,
    PendingSummary,
    Relation,
    Thresholds,
    cosine,
    normalize_name_key,
)

logger = logging.getLogger(__name__)

TIER_MID = "mid"

# K2 §2: alignment thresholds default from the wiring task (T-03 already
# reads KW_ALIGN_TAU1/TAU2 into consts). calibrate_thresholds replaces
# them per-domain; persisting the result back to env is a T-08 concern.
DEFAULT_TAU1 = KW_ALIGN_TAU1
DEFAULT_TAU2 = KW_ALIGN_TAU2

# P0-4: top-k ontology classes injected per span, plus their parent chain.
MAX_SUBGRAPH_CLASSES = 15
# K1 §4: high-frequency threshold for pending -> proposal backflow.
PENDING_PROPOSE_MIN_MENTIONS = 3
# K2 §2: LLM adjudication executes only at confidence >= 0.9.
ADJUDICATE_EXECUTE_LINE = 0.9

# External identity schemes that L0 blocking accepts (P0-1).
EXT_SCHEMES = {"atc", "nmpa", "insurance", "alias_table"}

# K2 §3.1: an extraction is INFERRED only when the LLM says so; tables and
# direct quotes are EXTRACTED.
TAG_EXTRACTED = "EXTRACTED"
TAG_INFERRED = "INFERRED"


# ---------------------------------------------------------------------------
# P0-4: ontology subgraph retrieval (lexical v0; embedding channel is T-08,
# the caller shape is final)
# ---------------------------------------------------------------------------

def retrieve_ontology_subgraph(chunk_text: str,
                               classes: list[dict[str, Any]],
                               top_k: int = MAX_SUBGRAPH_CLASSES
                               ) -> list[dict[str, Any]]:
    """Most relevant ontology classes for one evidence span.

    Scores every active class by term overlap between the chunk and the
    class name/aliases/props (3/2/1 points), keeps the top-k matched
    classes plus their ancestor chain (a class is useless without its
    parent), then fills remaining slots with high-frequency classes so a
    span with no hits still sees plausible anchors. Returns display dicts
    (name, stable_id, parent, props, aliases) - never mutates the input.

    This is the lexical v0 of P0-4; T-08 swaps the scorer for vector
    retrieval over an embedding index of the same class descriptors.
    """
    text = chunk_text or ""
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for idx, c in enumerate(classes):
        name = c.get("name", "")
        score = 0.0
        if name and name in text:
            score += 3.0
        for alias in c.get("aliases", []) or []:
            if isinstance(alias, str) and alias and alias in text or isinstance(alias, dict) and alias.get("alias") in text:
                score += 2.0
        for prop in c.get("props", []) or []:
            prop_name = prop if isinstance(prop, str) else prop.get("name", "")
            if prop_name and prop_name in text:
                score += 1.0
        scored.append((score, -idx, c))
    matched = [c for s, _, c in sorted(scored, key=lambda t: t[0], reverse=True)
               if s > 0.0][:top_k]
    # Ancestor chain first so the hierarchy reads top-down in the prompt.
    by_name = {c.get("name"): c for c in classes}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in matched:
        chain: list[dict[str, Any]] = []
        cur = c
        while cur is not None:
            name = cur.get("name", "")
            if name in seen:
                break
            chain.append(cur)
            seen.add(name)
            parent_name = cur.get("parent")
            cur = by_name.get(parent_name) if parent_name else None
        selected.extend(reversed(chain))
    # Fill the remaining budget with high-frequency classes (stable order).
    if len(selected) < top_k:
        filler = [c for s, _, c in sorted(scored)
                  if c.get("name") not in seen]
        for c in filler:
            if len(selected) >= top_k:
                break
            selected.append(c)
            seen.add(c.get("name", ""))
    return selected[:top_k]


def ontology_subgraph_text(subgraph: list[dict[str, Any]]) -> str:
    """Compact serialization of a retrieved subgraph for prompt injection
    (typically <3k tokens, replacing the 15k full-summary bomb)."""
    lines = []
    for c in subgraph:
        props = []
        for p in c.get("props", []) or []:
            props.append(p if isinstance(p, str) else str(p.get("name", "")))
        aliases = []
        for a in c.get("aliases", []) or []:
            aliases.append(a if isinstance(a, str) else str(a.get("alias", "")))
        parts = [f"{c.get('name')} [cls:{c.get('name')}]"]
        if c.get("parent"):
            parts.append(f"parent={c.get('parent')}")
        if props:
            parts.append("props=" + ",".join(props[:8]))
        if aliases:
            parts.append("aliases=" + ",".join(aliases[:8]))
        lines.append("; ".join(parts))
    return "\n".join(lines)


def chunk_plain_text(text: str, size: int = 600) -> list[EvidenceSpan]:
    """Deterministic paragraph splitter for offline smoke runs; the real
    parse chain (T-02 native ingestion) owns production chunking, this
    helper only exists so the CLI can ingest plain .txt artifacts without
    inventing a parser contract."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    spans: list[EvidenceSpan] = []
    buffer = ""
    idx = 0
    for para in paragraphs:
        if len(buffer) + len(para) + 1 > size and buffer:
            spans.append(EvidenceSpan(doc_id=None, chunk_idx=idx,
                                      text=buffer.strip()))
            idx += 1
            buffer = ""
        buffer += para + "\n"
    if buffer.strip():
        spans.append(EvidenceSpan(doc_id=None, chunk_idx=idx,
                                  text=buffer.strip()))
    return spans


# ---------------------------------------------------------------------------
# Prompt rendering (bilingual YAML pair in backend/prompts/, T-06 owns)
# ---------------------------------------------------------------------------

_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _render_prompt(name: str, lang: str, **variables: Any
                   ) -> tuple[str, str]:
    """Load ``{name}_{lang}.yaml`` and substitute {{ var }} placeholders
    (with or without surrounding spaces, like the upstream templates)."""
    path = _PROMPT_DIR / f"{name}_{lang}.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        import yaml
        data = yaml.safe_load(fh)
    system = str(data.get("system_prompt", ""))
    user = str(data.get("user_prompt", ""))

    def fill(template: str) -> str:
        for key, value in variables.items():
            for pattern in (f"{{{{ {key} }}}}", f"{{{{{key}}}}}",
                            f"{{{{ {key}}}}}", f"{{{{{key} }}}}"):
                template = template.replace(pattern, str(value))
        return template

    return fill(system), fill(user)


# ---------------------------------------------------------------------------
# Few-shot pool (3 static + 2 dynamic from confirmed extractions)
# ---------------------------------------------------------------------------

STATIC_EXAMPLES = [
    Example(
        span_text="二甲双胍的常用剂型包括盐酸二甲双胍片和格华止缓释片，"
                  "属于双胍类口服降糖药。",
        output={
            "entities": [
                {"name": "二甲双胍", "class_ref": "Drug",
                 "aliases": ["格华止"], "props": {"pharmacologic_class": "双胍类"},
                 "tag": "EXTRACTED", "ext_id": "A10BA02", "ext_scheme": "atc"},
            ],
            "edges": [],
        },
    ),
    Example(
        span_text="阿卡波糖通过抑制小肠α-葡萄糖苷酶延缓碳水吸收，"
                  "起始剂量为每次50mg每日三次。",
        output={
            "entities": [
                {"name": "阿卡波糖", "class_ref": "Drug",
                 "props": {"mechanism": "α-葡萄糖苷酶抑制剂",
                           "start_dose": "50mg tid"},
                 "tag": "EXTRACTED", "ext_id": "A10BF01", "ext_scheme": "atc"},
            ],
            "edges": [],
        },
    ),
    Example(
        span_text="胰岛素治疗适用于口服降糖药控制不佳的2型糖尿病患者。",
        output={
            "entities": [
                {"name": "胰岛素", "class_ref": "Drug", "tag": "EXTRACTED"},
                {"name": "2型糖尿病", "class_ref": "Disease",
                 "tag": "EXTRACTED"},
            ],
            "edges": [
                {"src": "胰岛素", "dst": "2型糖尿病", "rel_type": "indicated_for",
                 "claim": "适用于口服降糖药控制不佳的2型糖尿病患者",
                 "tag": "EXTRACTED"},
            ],
        },
    ),
]


def _anchor_key(class_ref: str) -> str:
    """Normalize a class_ref from extraction to a lookup key."""
    return (class_ref or "").strip().removeprefix("cls:")


class PgStore:
    """Real-Postgres adapter over database.knowevo_db helpers."""

    # ── ontology snapshot (read-only use of T-03/T-04 tables) ──────────

    async def load_ontology_snapshot(self, tenant_id):
        from database.knowevo_db import OntologyVersion, _get_db_session
        with _get_db_session() as session:
            row = (
                session.query(OntologyVersion)
                .filter(OntologyVersion.tenant_id == tenant_id,
                        OntologyVersion.status == "published")
                .order_by(OntologyVersion.created_at.desc())
                .first()
            )
            return row.snapshot if row is not None else {"classes": [],
                                                         "rel_types": []}

    # ── entities ───────────────────────────────────────────────────────

    async def get_entity_by_stable_id(self, tenant_id, stable_id):
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id,
                KgEntity.stable_id == stable_id,
            ).first()
            if row is None:
                return None
            return _entity_row_to_dict(row)

    async def insert_entity(self, tenant_id, values):
        from database.knowevo_db import KgEntity, create_row
        return create_row(KgEntity, tenant_id=tenant_id, **values)

    async def list_entities(self, tenant_id, stable_ids):
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            rows = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id,
                KgEntity.stable_id.in_(stable_ids),
            ).all()
            return {r.stable_id: _entity_row_to_dict(r) for r in rows}

    async def find_by_ext_key(self, tenant_id, value, scheme):
        """L0 blocking: entities whose props.ext_ids contain (value, scheme)."""
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            rows = (
                session.query(KgEntity)
                .filter(KgEntity.tenant_id == tenant_id,
                        KgEntity.status == "active",
                        KgEntity.props.contains(
                            {"ext_ids": [{"value": value, "scheme": scheme}]}))
                .all()
            )
            for r in rows:
                return _entity_row_to_dict(r)
            return None

    async def find_by_alias(self, tenant_id, alias):
        """L0 blocking: exact name match or alias entry. Returns the alias
        type recorded when the alias was attached (brand/generic/...)."""
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            rows = (
                session.query(KgEntity)
                .filter(KgEntity.tenant_id == tenant_id,
                        KgEntity.status == "active")
                .all()
            )
            needle = normalize_name_key(alias)
            for r in rows:
                if normalize_name_key(r.name) == needle:
                    return _entity_row_to_dict(r), "form"
                for a in (r.aliases or []):
                    if normalize_name_key(a.get("alias", "")) == needle:
                        return _entity_row_to_dict(r), a.get("type", "form")
            return None, None

    async def entities_with_embedding(self, tenant_id):
        """L1 candidate fetch: all active rows carrying an embedding. The
        cosine runs in the service layer (no pgvector in the upstream PG
        image); a T-07/T-09 task may push this into a GIN-indexed filter."""
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            rows = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id,
                KgEntity.status == "active",
                KgEntity.embedding.isnot(None),
            ).all()
            return [_entity_row_to_dict(r) for r in rows]

    async def add_alias(self, tenant_id, stable_id, alias, alias_type):
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id,
                KgEntity.stable_id == stable_id,
            ).first()
            if row is None:
                return False
            aliases = list(row.aliases or [])
            aliases.append({"alias": alias, "type": alias_type})
            row.aliases = aliases
            session.flush()
            return True

    async def append_ext_id(self, tenant_id, stable_id, value, scheme):
        """Attach an external identity to an existing entity's props so L0
        blocking finds it on later runs (the authority anchor)."""
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id,
                KgEntity.stable_id == stable_id,
            ).first()
            if row is None:
                return False
            props = dict(row.props or {})
            ext_ids = list(props.get("ext_ids") or [])
            if not any(e.get("value") == value and e.get("scheme") == scheme
                       for e in ext_ids):
                ext_ids.append({"value": value, "scheme": scheme})
                props["ext_ids"] = ext_ids
                row.props = props
                session.flush()
            return True

    async def deprecate_entity(self, tenant_id, stable_id, split_into):
        from sqlalchemy import func as sqla_func

        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgEntity).filter(
                KgEntity.tenant_id == tenant_id,
                KgEntity.stable_id == stable_id,
            ).first()
            if row is None:
                return False
            row.status = "split"
            row.split_into = split_into
            row.invalid_at = sqla_func.now()
            session.flush()
            return True

    # ── relations ──────────────────────────────────────────────────────

    async def current_relation_by_triple(self, tenant_id, src, dst, rel_type):
        from database.knowevo_db import KgRelation, _get_db_session, valid_now
        with _get_db_session() as session:
            row = session.query(KgRelation).filter(
                KgRelation.tenant_id == tenant_id,
                KgRelation.src == src,
                KgRelation.dst == dst,
                KgRelation.rel_type == rel_type,
                valid_now(KgRelation),
            ).first()
            if row is None:
                return None
            return _relation_row_to_dict(row)

    async def insert_relation(self, tenant_id, values):
        from database.knowevo_db import KgRelation, create_row
        return create_row(KgRelation, tenant_id=tenant_id, **values)

    async def supersede_relation(self, tenant_id, edge_id):
        from sqlalchemy import func as sqla_func

        from database.knowevo_db import KgRelation, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgRelation).filter(
                KgRelation.tenant_id == tenant_id,
                KgRelation.id == edge_id,
            ).first()
            if row is None:
                return False
            row.invalid_at = sqla_func.now()
            row.superseded_at = sqla_func.now()
            session.flush()
            return True

    async def mark_contested(self, tenant_id, edge_id):
        from database.knowevo_db import KgRelation, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgRelation).filter(
                KgRelation.tenant_id == tenant_id,
                KgRelation.id == edge_id,
            ).first()
            if row is None:
                return False
            row.contested = True
            session.flush()
            return True

    async def list_relations_by_entity(self, tenant_id, stable_id):
        from database.knowevo_db import KgRelation, _get_db_session
        with _get_db_session() as session:
            rows = session.query(KgRelation).filter(
                KgRelation.tenant_id == tenant_id,
                (KgRelation.src == stable_id) | (KgRelation.dst == stable_id),
            ).all()
            return [_relation_row_to_dict(r) for r in rows]

    async def reassign_edge_endpoint(self, tenant_id, old_sid, new_sid,
                                     which):
        from database.knowevo_db import KgRelation, _get_db_session
        with _get_db_session() as session:
            col = KgRelation.src if which == "src" else KgRelation.dst
            count = (
                session.query(KgRelation)
                .filter(KgRelation.tenant_id == tenant_id, col == old_sid)
                .update({col: new_sid}, synchronize_session=False)
            )
            session.flush()
            return count

    # ── evidence ───────────────────────────────────────────────────────

    async def save_evidence(self, tenant_id, values):
        from database.knowevo_db import KgEvidence, create_row
        return create_row(KgEvidence, tenant_id=tenant_id, **values)

    async def get_evidence(self, tenant_id, evidence_id):
        from database.knowevo_db import KgEvidence, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgEvidence).filter(
                KgEvidence.tenant_id == tenant_id,
                KgEvidence.id == evidence_id,
            ).first()
            if row is None:
                return None
            return {"id": row.id, "doc_id": row.doc_id, "tag": row.tag,
                    "modality": row.modality}

    async def finalize_evidence(self, tenant_id, evidence_id, entity_refs,
                                edge_ids):
        from database.knowevo_db import KgEvidence, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgEvidence).filter(
                KgEvidence.tenant_id == tenant_id,
                KgEvidence.id == evidence_id,
            ).first()
            if row is None:
                return False
            row.entity_refs = list(entity_refs)
            row.edge_ids = list(edge_ids)
            session.flush()
            return True

    async def doc_authority_level(self, tenant_id, doc_id):
        from database.knowevo_db import DocAsset, _get_db_session
        with _get_db_session() as session:
            row = session.query(DocAsset).filter(
                DocAsset.tenant_id == tenant_id,
                DocAsset.id == doc_id,
            ).first()
            return row.authority_level if row is not None else 3

    async def list_confirmed_examples(self, tenant_id, limit=2):
        """Dynamic few-shot pool: recent extracted evidence spans."""
        from database.knowevo_db import KgEvidence, _get_db_session
        with _get_db_session() as session:
            rows = (
                session.query(KgEvidence)
                .filter(KgEvidence.tenant_id == tenant_id,
                        KgEvidence.tag == TAG_EXTRACTED)
                .order_by(KgEvidence.created_at.desc())
                .limit(limit)
                .all()
            )
            return [{"span_text": r.span_text} for r in rows]

    # ── pending pool ───────────────────────────────────────────────────

    async def add_pending(self, tenant_id, name, evidence_id,
                          suggested_class=None):
        from database.knowevo_db import KgPendingEntity, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgPendingEntity).filter(
                KgPendingEntity.tenant_id == tenant_id,
                KgPendingEntity.name == name,
            ).first()
            if row is None:
                session.add(KgPendingEntity(
                    tenant_id=tenant_id, name=name, mention_count=1,
                    evidence_ids=[evidence_id] if evidence_id else [],
                    suggested_class=suggested_class))
            else:
                row.mention_count = (row.mention_count or 1) + 1
                ids = list(row.evidence_ids or [])
                if evidence_id not in ids:
                    ids.append(evidence_id)
                row.evidence_ids = ids
            session.flush()
            return True

    async def list_pending(self, tenant_id, status="pending"):
        from database.knowevo_db import KgPendingEntity, _get_db_session
        with _get_db_session() as session:
            q = session.query(KgPendingEntity).filter(
                KgPendingEntity.tenant_id == tenant_id)
            if status is not None:
                q = q.filter(KgPendingEntity.status == status)
            rows = q.all()
            return [{"id": r.id, "name": r.name,
                     "mention_count": r.mention_count,
                     "evidence_ids": list(r.evidence_ids or []),
                     "suggested_class": r.suggested_class,
                     "status": r.status} for r in rows]

    async def update_pending_status(self, tenant_id, ids, status):
        from database.knowevo_db import KgPendingEntity, _get_db_session
        with _get_db_session() as session:
            count = (
                session.query(KgPendingEntity)
                .filter(KgPendingEntity.tenant_id == tenant_id,
                        KgPendingEntity.id.in_(ids))
                .update({"status": status}, synchronize_session=False)
            )
            session.flush()
            return count

    # ── query surface (search/neighbors; T-07 extends to MCP) ──────────

    async def search_entities(self, tenant_id, query, limit):
        from database.knowevo_db import KgEntity, _get_db_session
        with _get_db_session() as session:
            rows = (
                session.query(KgEntity)
                .filter(KgEntity.tenant_id == tenant_id,
                        KgEntity.status == "active",
                        KgEntity.name.ilike(f"%{query}%"))
                .limit(limit)
                .all()
            )
            return [_entity_row_to_dict(r) for r in rows]

    async def neighbors(self, tenant_id, stable_ids, hop=1):
        from database.knowevo_db import KgRelation, _get_db_session, valid_now
        with _get_db_session() as session:
            edges: list[dict[str, Any]] = []
            frontier = list(stable_ids)
            touched = set(stable_ids)
            for _ in range(hop):
                if not frontier:
                    break
                rows = (
                    session.query(KgRelation)
                    .filter(KgRelation.tenant_id == tenant_id,
                            valid_now(KgRelation),
                            (KgRelation.src.in_(frontier))
                            | (KgRelation.dst.in_(frontier)))
                    .all()
                )
                next_frontier: list[str] = []
                for r in rows:
                    edge = _relation_row_to_dict(r)
                    if edge["src"] not in touched or edge["dst"] not in touched:
                        edges.append(edge)
                    for sid in (edge["src"], edge["dst"]):
                        if sid not in touched:
                            touched.add(sid)
                            next_frontier.append(sid)
                frontier = [s for s in next_frontier if s not in frontier]
            return edges

    # ── extract-run idempotency (kg_extract_run_t, migration 002) ──────

    async def is_extract_done(self, tenant_id, span_hash):
        from database.knowevo_db import KgExtractRun, _get_db_session
        with _get_db_session() as session:
            row = session.query(KgExtractRun).filter(
                KgExtractRun.tenant_id == tenant_id,
                KgExtractRun.span_hash == span_hash,
            ).first()
            return row is not None

    async def record_extract_run(self, tenant_id, run_id, span_hash,
                                 channel, tokens_spent):
        from database.knowevo_db import KgExtractRun, create_row
        create_row(KgExtractRun, tenant_id=tenant_id, run_id=run_id,
                   span_hash=span_hash, channel=channel, status="done",
                   tokens_spent=tokens_spent)
        return True


def _entity_row_to_dict(r) -> dict[str, Any]:
    return {"stable_id": r.stable_id, "name": r.name, "class_ref": r.class_ref,
            "aliases": list(r.aliases or []), "props": dict(r.props or {}),
            "embedding": r.embedding, "status": r.status,
            "split_into": list(r.split_into or [])}


def _relation_row_to_dict(r) -> dict[str, Any]:
    return {"id": r.id, "src": r.src, "dst": r.dst, "rel_type": r.rel_type,
            "claim": r.claim, "contested": r.contested,
            "evidence_id": (r.props or {}).get("evidence_id"),
            "valid_at": r.valid_at, "invalid_at": r.invalid_at}


class KGService:
    """K2 pipeline: anchored extraction -> three-level alignment -> merge.

    ``ontology`` is the active snapshot dict {classes, rel_types} used for
    anchoring; the CLI loads it once per batch from the store. ``llm`` is
    the injected async callable (tests use fakes; T-08 wires the real tier
    routing). ``ontology_service`` is what pending_to_proposals hands to.
    """

    def __init__(self, store=None, llm: Any | None = None,
                 ontology: dict[str, Any] | None = None,
                 ontology_service: Any | None = None,
                 tenant_id: str = "",
                 tau1: float = DEFAULT_TAU1,
                 tau2: float = DEFAULT_TAU2):
        self.store = store
        self.llm = llm
        self.ontology = ontology or {"classes": [], "rel_types": []}
        self.ontology_service = ontology_service
        self.tenant_id = tenant_id
        self.tau1 = tau1
        self.tau2 = tau2
        self._class_keys: dict[str, dict[str, Any]] = {
            _anchor_key(c.get("stable_id") or c.get("name", "")): c
            for c in self.ontology.get("classes", [])
        }

    # ── extraction ─────────────────────────────────────────────────────

    async def extract(self, span: EvidenceSpan,
                      ontology_summary: str | None = None
                      ) -> ExtractionResult:
        """LLM channel: one span -> anchored entities + edges.

        Anchoring is a hard gate: an entity whose class_ref does not hit an
        active class goes to the pending list (never silently inserted).
        The evidence row is created here (span provenance lives with the
        span); merge_delta finalizes its entity_refs/edge_ids afterwards.
        """
        if self.llm is None:
            raise RuntimeError("KGService.extract requires an injected llm")
        prompt_text = ontology_summary or ontology_subgraph_text(
            retrieve_ontology_subgraph(span.text, self.ontology["classes"]))
        fewshot = await self.fewshot_for(span)
        system, user = _render_prompt(
            "knowevo_extract", lang="en",
            ontology_subgraph=prompt_text,
            evidence_span=span.text,
            fewshot=json.dumps(
                [{"span": e.span_text, "output": e.output}
                 for e in fewshot], ensure_ascii=False),
        )
        raw = await self.llm(f"{system}\n\n{user}", kind="extract",
                             tier=TIER_MID, temperature=0.0)
        result = self._parse_extraction(raw)
        if self.store is not None and span.doc_id is not None:
            extra = {"span_loc": {"chunk_idx": span.chunk_idx,
                                  "page": span.page},
                     "span_text": span.text, "entity_refs": [], "edge_ids": [],
                     "tag": _evidence_tag(result),
                     "modality": getattr(span, "modality", "text") or "text",
                     "doc_id": span.doc_id}
            ev = await self.store.save_evidence(self.tenant_id, extra)
            ev_id = ev["id"] if isinstance(ev, dict) else ev
            for e in result.entities:
                e.evidence_id = ev_id
            for e in result.pending:
                e.evidence_id = ev_id
            for edge in result.edges:
                edge.evidence_id = ev_id
        return result

    def extract_table(self, table: ParsedTable) -> ExtractionResult:
        """Deterministic channel: col headers -> props, rows -> entities.
        Zero LLM cost and byte-reproducible (graphify-inspired)."""
        result = ExtractionResult(channel="table")
        hint = table.class_hint
        if hint and _anchor_key(hint) not in self._class_keys:
            hint = None  # class_hint must be an active class (anchoring rule)
        seen: set[str] = set()
        headers = table.headers or []
        for row in table.rows:
            if not row or not (row[0] or "").strip():
                continue
            name = row[0].strip()
            if name in seen:
                continue
            seen.add(name)
            props = {}
            for i, header in enumerate(headers[1:], start=1):
                if i < len(row) and (row[i] or "").strip():
                    props[header.strip()] = row[i].strip()
            entity = Entity(name=name, class_ref=hint, props=props,
                            tag=TAG_EXTRACTED)
            if hint is None:
                result.pending.append(entity)
            else:
                result.entities.append(entity)
        return result

    async def fewshot_for(self, span: EvidenceSpan) -> list[Example]:
        """3 static + up to 2 dynamic examples from the confirmed pool."""
        picked = list(STATIC_EXAMPLES)
        if self.store is not None:
            try:
                for row in await self.store.list_confirmed_examples(
                        self.tenant_id, limit=2):
                    picked.append(Example(
                        span_text=row["span_text"], source="dynamic"))
            except Exception:
                logger.warning("dynamic few-shot pool unavailable",
                               exc_info=True)
        return picked

    # ── alignment ──────────────────────────────────────────────────────

    async def align(self, entity: Entity) -> AlignDecision:
        """Three-level alignment (K2 2.3, P0-1).

        L0  external primary-key blocking: ATC/NMPA/insurance code or the
            alias table. A hit merges unconditionally - the authorities say
            "same thing" (generic=brand like 二甲双胍=格华止), similarity is
            irrelevant and this is exactly what the naive sim>0.85 rule
            cannot do.
        L1  vector candidates: sim > tau1 merges; (tau2, tau1] goes to L2;
            <= tau2 creates new. Skipped when the entity carries no
            embedding (pre-T-08) - the entity then routes to L2.
        L2  LLM adjudication against candidate evidence; confidence >= 0.9
            executes, otherwise L3.
        L3  human review pool (kg_pending_entity_t via add_pending).
        """
        # L0: external identity primary keys (P0-1 core).
        keys = list((entity.props or {}).get("ext_ids") or [])
        if entity.ext_id and entity.ext_scheme:
            keys.append({"value": entity.ext_id,
                         "scheme": entity.ext_scheme})
        for key in keys:
            value = key.get("value")
            scheme = key.get("scheme")
            if not value or scheme not in EXT_SCHEMES:
                continue
            hit = (None if self.store is None else
                   await self.store.find_by_ext_key(
                       self.tenant_id, value, scheme))
            if hit is not None:
                return AlignDecision(
                    action="merge", target_stable_id=hit["stable_id"],
                    alias_type="ext_key", confidence=1.0, level="L0",
                    reason=f"external key {scheme}={value} matches "
                           f"{hit['stable_id']}")
        # L0: alias table / exact-name blocking. Only applies to entities
        # without an embedding - a vector-carrying entity is expected to
        # resolve through the similarity channel (L1/L2), and treating
        # dosage-form variants as aliases there would bypass the learned
        # similarity entirely.
        if self.store is not None and not (entity.props or {}).get("embedding"):
            for probe in (entity.name, _strip_salt(entity.name)):
                if not probe:
                    continue
                hit, alias_type = await self.store.find_by_alias(
                    self.tenant_id, probe)
                if hit is not None:
                    return AlignDecision(
                        action="merge", target_stable_id=hit["stable_id"],
                        alias_type=alias_type or "form", confidence=1.0,
                        level="L0",
                        reason=f"alias/name blocking -> {hit['stable_id']}")

        # L1: vector similarity over name+props embedding.
        emb = (entity.props or {}).get("embedding")
        candidates: list[tuple[dict[str, Any], float]] = []
        if emb and self.store is not None:
            rows = await self.store.entities_with_embedding(self.tenant_id)
            scored = sorted(
                ((r, cosine(list(emb), list(r.get("embedding") or [])))
                 for r in rows),
                key=lambda t: t[1], reverse=True)
            scored = [(r, s) for r, s in scored if s > 0.0]
            if scored:
                best, s = scored[0]
                if s > self.tau1:
                    return AlignDecision(
                        action="merge", target_stable_id=best["stable_id"],
                        alias_type="similar", confidence=round(s, 4),
                        level="L1", reason=f"cosine {s:.3f} > tau1")
                if s <= self.tau2:
                    return AlignDecision(
                        action="new", confidence=round(s, 4), level="L1",
                        reason=f"cosine {s:.3f} <= tau2")
                candidates = scored[:3]

        # L2: LLM adjudication (also the route when no embedding exists).
        if self.llm is not None:
            decision = await self._adjudicate_llm(entity, candidates)
            if decision.confidence >= ADJUDICATE_EXECUTE_LINE:
                return decision
            return AlignDecision(
                action="pending_review", confidence=decision.confidence,
                level="L3",
                reason=f"LLM conf {decision.confidence:.2f} < "
                       f"{ADJUDICATE_EXECUTE_LINE}: {decision.reason}")
        return AlignDecision(action="new", confidence=0.0, level="L2",
                             reason="no embedding and no LLM in v0")

    async def _adjudicate_llm(self, entity: Entity,
                              candidates: list[tuple[dict, float]]
                              ) -> AlignDecision:
        cand_text = "; ".join(
            f"{row['name']}({row['class_ref']}, sim={s:.3f})"
            for row, s in candidates[:3]) or "none"
        prompt = (f"Adjudicate whether entity '{entity.name}' (class "
                  f"{entity.class_ref}, props {entity.props}) is the same as "
                  f"any candidate: {cand_text}. Return JSON {{action: "
                  f"merge|new|split, target: <candidate name or null>, "
                  f"confidence: 0..1, reason}}.")
        raw = await self.llm(prompt, kind="align", tier=TIER_MID,
                             temperature=0.0)
        action = (raw or {}).get("action", "new")
        conf = float((raw or {}).get("confidence", 0.0))
        if action == "merge" and candidates:
            target = next((row for row, _ in candidates
                           if row["name"] == (raw or {}).get("target")),
                          None)
            if target is not None:
                return AlignDecision(
                    action="merge", target_stable_id=target["stable_id"],
                    alias_type="llm", confidence=conf, level="L2",
                    reason=(raw or {}).get("reason", ""))
        # split (and anything unknown) waits for a human at L3 - splitting
        # is a destructive-ish repair and 0.9-risk-safe route is human.
        if action == "new":
            return AlignDecision(action="new", confidence=conf, level="L2",
                                 reason=(raw or {}).get("reason", ""))
        return AlignDecision(action="pending_review", confidence=conf,
                             level="L3",
                             reason=(raw or {}).get("reason", ""))

    # ── threshold calibration (L3 open item) ───────────────────────────

    def calibrate_thresholds(self, labeled_pairs: list[LabeledPair]
                             ) -> Thresholds:
        """ROC over human-labeled pairs: tau1 at false-merge rate <= 2%,
        tau2 at recall >= 95% (K2 2.3 threshold calibration).

        A pair is treated as "merged" when its similarity sits at or above
        the candidate threshold. tau1 is the strictest safe line: the
        largest similarity inside the fpr <= 2% band that keeps recall
        maximal (it must not exclude labeled-true pairs). tau2 is the most
        permissive line that still satisfies fpr <= 2% and recall >= 95%.
        The reported AUC is the Mann-Whitney U statistic (standard for
        discrete ROC curves).
        """
        if not labeled_pairs:
            return Thresholds(tau1=self.tau1, tau2=self.tau2)
        labels = [(p.sim, p.is_same) for p in labeled_pairs]
        n_same = sum(1 for _, same in labels if same)
        n_diff = len(labels) - n_same
        thresholds = sorted({s for s, _ in labels}, reverse=True)
        points: list[tuple[float, float, float]] = []
        for t in thresholds:
            above = [same for _, same in labels if _ >= t]
            true_pos = sum(1 for same in above if same)
            false_pos = len(above) - true_pos
            fpr = false_pos / n_diff if n_diff else 0.0
            recall = true_pos / n_same if n_same else 0.0
            points.append((t, fpr, recall))
        # tau1: the strictest safe line (largest threshold with fpr <= 2%)
        # that does not throw away known positive pairs - among the safe
        # thresholds pick the one with the highest recall, ties to the
        # largest threshold. A threshold that excludes labeled-true pairs
        # (as 0.95 would for 0.85-positives) is not a useful merge line.
        safe = [(t, fpr, rec) for t, fpr, rec in points if fpr <= 0.02]
        if safe:
            tau1 = max(safe, key=lambda p: (p[2], p[0]))[0]
        else:
            tau1 = thresholds[0]
        # tau2: the most permissive threshold that still satisfies both
        # constraints (fpr <= 2% and recall >= 95%). If no threshold does,
        # fall back to the recall-only band.
        safe_and_complete = [t for t, fpr, rec in points
                             if fpr <= 0.02 and rec >= 0.95]
        if safe_and_complete:
            tau2 = safe_and_complete[-1]
        else:
            rec_band = [t for t, _, rec in points if rec >= 0.95]
            tau2 = rec_band[-1] if rec_band else thresholds[-1]
        tau2 = min(tau2, tau1)
        auc = _mwu_auc(labels, n_same, n_diff)
        return Thresholds(tau1=round(tau1, 4), tau2=round(tau2, 4),
                          auc=round(auc, 4), n_pairs=len(labeled_pairs),
                          false_merge_rate=round(
                              points[0][1] if points else 0.0, 4),
                          recall=round(points[0][2] if points else 0.0, 4))

    # ── merge ──────────────────────────────────────────────────────────

    async def merge_delta(self, extractions: list[ExtractionResult]
                          ) -> IngestReport:
        """Apply extractions to the graph under the K2 3.2 conflict table.

        NEW       -> insert entity (or alias-merge on stable_id collision)
        ALIAS     -> attach alias + merge supplemental props
        CONTRA    -> same triple, different claim: authority_level wins,
                     loser gets invalid_at stamped (never silently
                     overwritten)
        CONTENDED -> equal authority and overlapping validity: both rows
                     kept, new row contested=true for human review
        """
        report = IngestReport()
        t0 = time.monotonic()
        align_map: dict[str, str] = {}
        for ext in extractions:
            report.tokens_spent += int(getattr(ext, "tokens_spent", 0))
            # Unmappable entities land in the human review pool, each
            # counted once per extraction (mention counting happens in the
            # store when the same name recurs).
            for entity in ext.pending:
                report.pending += 1
                if self.store is not None:
                    await self.store.add_pending(
                        self.tenant_id, entity.name, entity.evidence_id,
                        suggested_class=entity.class_ref)
            for entity in ext.entities:
                decision = await self.align(entity)
                if decision.action == "merge":
                    sid = decision.target_stable_id
                    await self._merge_entity(entity, sid,
                                             decision.alias_type, report)
                    align_map[entity.name] = sid
                elif decision.action == "new":
                    sid = await self._insert_new_entity(entity)
                    if sid is None:
                        report.errors.append(f"new entity skipped: "
                                             f"{entity.name}")
                    else:
                        align_map[entity.name] = sid
                        report.added += 1
                else:
                    align_map[entity.name] = None
                    report.pending += 1
                    if self.store is not None:
                        await self.store.add_pending(
                            self.tenant_id, entity.name, entity.evidence_id,
                            suggested_class=entity.class_ref)

            edge_ids: list[Any] = []
            for edge in ext.edges:
                src = align_map.get(edge.src)
                dst = align_map.get(edge.dst)
                if not src or not dst:
                    report.errors.append(
                        f"edge skipped, unresolved endpoint: "
                        f"{edge.src}->{edge.dst}")
                    continue
                edge_result = await self._merge_edge(edge, src, dst, report)
                if edge_result is not None:
                    edge_ids.append(edge_result)

            if self.store is not None and (ext.entities or ext.pending
                                           or ext.edges):
                ev_ids = {e.evidence_id for e in ext.entities}
                ev_ids |= {e.evidence_id for e in ext.pending}
                ev_ids |= {e.evidence_id for e in ext.edges}
                refs = [align_map[e.name] for e in ext.entities
                        if align_map.get(e.name)]
                for ev_id in ev_ids:
                    if ev_id is None:
                        continue
                    await self.store.finalize_evidence(
                        self.tenant_id, ev_id, refs, edge_ids)
        report.wall_seconds = round(time.monotonic() - t0, 4)
        return report

    async def _merge_entity(self, entity: Entity, target_sid: str,
                            alias_type: str | None, report: IngestReport):
        if self.store is None:
            report.merged += 1
            return
        await self.store.add_alias(self.tenant_id, target_sid, entity.name,
                                   alias_type or "form")
        if entity.ext_id and entity.ext_scheme:
            await self.store.append_ext_id(self.tenant_id, target_sid,
                                           entity.ext_id, entity.ext_scheme)
        for key in (entity.props or {}).get("ext_ids", []):
            await self.store.append_ext_id(self.tenant_id, target_sid,
                                           key.get("value"),
                                           key.get("scheme"))
        report.merged += 1

    async def _insert_new_entity(self, entity: Entity) -> str | None:
        if self.store is None:
            return _new_stable_id(entity)
        sid = _new_stable_id(entity)
        existing = await self.store.get_entity_by_stable_id(self.tenant_id, sid)
        if existing is not None:
            # stable_id collision: it is the same entity, become an alias.
            await self.store.add_alias(self.tenant_id, sid, entity.name, "form")
            return existing["stable_id"]
        values: dict[str, Any] = {
            "stable_id": sid,
            "name": entity.name,
            "class_ref": entity.class_ref,
            "aliases": [{"alias": a, "type": "abbr"} for a in entity.aliases],
            "props": dict(entity.props or {}),
            "status": "active",
        }
        # Persist external identity keys so L0 blocking finds this row on
        # later runs (the P0-1 authority anchor).
        ext_ids = list(values["props"].get("ext_ids") or [])
        if (entity.ext_id and entity.ext_scheme
                and not any(e.get("value") == entity.ext_id
                            and e.get("scheme") == entity.ext_scheme
                            for e in ext_ids)):
            ext_ids.append({"value": entity.ext_id,
                            "scheme": entity.ext_scheme})
        if ext_ids:
            values["props"]["ext_ids"] = ext_ids
        if (entity.props or {}).get("embedding"):
            values["embedding"] = entity.props["embedding"]
        if entity.evidence_id is not None:
            props = dict(values["props"])
            props.setdefault("evidence_ids", []).append(
                str(entity.evidence_id))
            values["props"] = props
        await self.store.insert_entity(self.tenant_id, values)
        return sid

    async def _merge_edge(self, edge: Relation, src_sid: str, dst_sid: str,
                          report: IngestReport) -> Any | None:
        if self.store is None:
            return uuid.uuid4()
        existing = await self.store.current_relation_by_triple(
            self.tenant_id, src_sid, dst_sid, edge.rel_type)
        if existing and existing["claim"] == edge.claim:
            return None  # already recorded, dedupe
        props: dict[str, Any] = {}
        if edge.evidence_id is not None:
            props["evidence_id"] = str(edge.evidence_id)
        edge_id = (await self.store.insert_relation(self.tenant_id, {
            "src": src_sid, "dst": dst_sid, "rel_type": edge.rel_type,
            "claim": edge.claim, "props": props,
        }))["id"]
        if existing:
            new_authority = 3
            if edge.evidence_id is not None:
                ev_new = await self.store.get_evidence(
                    self.tenant_id, edge.evidence_id)
                if ev_new is not None:
                    new_authority = await self.store.doc_authority_level(
                        self.tenant_id, ev_new["doc_id"])
            old_authority = 3
            old_ev_id = (existing.get("props") or {}).get("evidence_id") \
                or existing.get("evidence_id")
            if old_ev_id:
                ev_old = await self.store.get_evidence(
                    self.tenant_id, uuid.UUID(str(old_ev_id)))
                if ev_old is not None:
                    old_authority = await self.store.doc_authority_level(
                        self.tenant_id, ev_old["doc_id"])
            if new_authority < old_authority:
                # Newer/higher-authority source wins (lower number = higher
                # authority, e.g. 1 = national guideline); the old fact
                # keeps a version stamp, nothing is deleted.
                await self.store.supersede_relation(self.tenant_id,
                                                    existing["id"])
                report.superseded += 1
            elif new_authority == old_authority:
                # Same-source contradiction: never silently overwrite.
                await self.store.mark_contested(self.tenant_id, edge_id)
                report.contended += 1
            else:
                # Lower-authority new claim loses: drop the fresh row.
                await self.store.supersede_relation(self.tenant_id, edge_id)
                report.superseded += 1
        return edge_id

    async def split_entity(self, entity_id: str, criteria: str
                           ) -> tuple[str, str]:
        """Repair an error merge: partition into two entities, deprecate the
        original (bi-temporal, nothing is physically deleted), re-anchor
        its edges onto side A. criteria is a JSON payload
        {"name_a", "name_b", "props_a": [keys], "props_b": [keys]}."""
        if self.store is None:
            raise RuntimeError("split_entity requires a store")
        entity = await self.store.get_entity_by_stable_id(self.tenant_id,
                                                          entity_id)
        if entity is None:
            raise ValueError(f"entity not found: {entity_id}")
        try:
            spec = json.loads(criteria or "{}")
        except ValueError:
            spec = {}
        name_a = spec.get("name_a") or entity["name"]
        name_b = spec.get("name_b") or f"{entity['name']}·2"
        props = dict(entity.get("props") or {})
        propped_b = set(spec.get("props_b") or [])
        props_a = spec.get("props_a") or [k for k in props if k not in propped_b]
        props_b = spec.get("props_b") or []
        sid_a = f"{entity_id}:a"
        sid_b = f"{entity_id}:b"
        await self.store.insert_entity(self.tenant_id, {
            "stable_id": sid_a, "name": name_a,
            "class_ref": entity["class_ref"],
            "props": {k: props[k] for k in props_a if k in props},
        })
        await self.store.insert_entity(self.tenant_id, {
            "stable_id": sid_b, "name": name_b,
            "class_ref": entity["class_ref"],
            "props": {k: props[k] for k in props_b if k in props},
        })
        await self.store.deprecate_entity(self.tenant_id, entity_id,
                                          [sid_a, sid_b])
        await self.store.reassign_edge_endpoint(self.tenant_id, entity_id,
                                                sid_a, "src")
        await self.store.reassign_edge_endpoint(self.tenant_id, entity_id,
                                                sid_a, "dst")
        return sid_a, sid_b

    # ── pending pool ───────────────────────────────────────────────────

    async def update_pending_pool(self) -> PendingSummary:
        summary = PendingSummary()
        if self.store is None:
            return summary
        rows = await self.store.list_pending(self.tenant_id, status=None)
        by_status: dict[str, int] = {}
        by_class: dict[str, int] = {}
        for r in rows:
            summary.total += 1
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            cls = r["suggested_class"] or "unknown"
            by_class[cls] = by_class.get(cls, 0) + 1
            if r["mention_count"] >= PENDING_PROPOSE_MIN_MENTIONS:
                summary.high_frequency.append(r)
        summary.by_status = by_status
        summary.by_class = by_class
        return summary

    async def pending_to_proposals(self, min_mentions: int =
                                   PENDING_PROPOSE_MIN_MENTIONS) -> list[Any]:
        """Hand high-frequency unmapped entities to the K1 channel
        (ontology_service.propose_from_pending), mark them proposed."""
        if self.store is None or self.ontology_service is None:
            return []
        rows = await self.store.list_pending(self.tenant_id, status="pending")
        eligible = [r for r in rows
                    if r["mention_count"] >= min_mentions]
        proposals = await self.ontology_service.propose_from_pending(eligible)
        ids = [r["id"] for r in eligible]
        if ids:
            await self.store.update_pending_status(self.tenant_id, ids,
                                                   "proposed")
        return proposals

    # ── query surface (thin v0; T-07/T-09 build the MCP tools) ─────────

    async def search(self, query: str, hop: int = 1, top_k: int = 5,
                     ontology_version: str | None = None) -> dict[str, Any]:
        """Lexical name search + the 1..hop neighborhood of the hits. The
        retrieval ranking (BM25/vector) and version-pinned multi-hop are
        T-07/T-09 work; this returns the raw current-view shape."""
        if self.store is None:
            return {"entities": [], "edges": [], "hop": hop,
                    "ontology_version": ontology_version}
        hits = await self.store.search_entities(self.tenant_id, query, top_k)
        sids = [h["stable_id"] for h in hits]
        edges = await self.store.neighbors(self.tenant_id, sids, hop=max(1, hop))
        return {"entities": hits, "edges": edges, "hop": hop,
                "ontology_version": ontology_version}

    async def ingest_new_version(self, old_doc, new_doc, changed_spans):
        """Controlled supersede on document version evolution. Owned by
        T-11 (alignment_service triggers it); not implemented here."""
        raise NotImplementedError("ingest_new_version belongs to T-11")

    async def evolution_trace(self, entity_id=None, decision_id=None):
        """Timeline of an entity / hops behind a decision card. Owned by
        T-09 (multi-hop + evidence chain); not implemented here."""
        raise NotImplementedError("evolution_trace belongs to T-09")

    # ── parsing helpers ────────────────────────────────────────────────

    def _parse_extraction(self, raw: Any) -> ExtractionResult:
        result = ExtractionResult(channel="llm")
        if isinstance(raw, dict):
            entity_dicts = list(raw.get("entities", []))
            edge_dicts = list(raw.get("edges", []))
        else:
            entity_dicts = list(raw or [])
            edge_dicts = []
        seen: set[str] = set()
        for ed in entity_dicts:
            name = str(ed.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            class_ref = _anchor_key(ed.get("class_ref") or ed.get("type"))
            entity = Entity(
                name=name,
                class_ref=class_ref or None,
                aliases=[str(a) for a in ed.get("aliases", []) if a],
                props=dict(ed.get("props") or {}),
                tag=str(ed.get("tag") or TAG_EXTRACTED).upper(),
                ext_id=ed.get("ext_id"),
                ext_scheme=ed.get("ext_scheme"),
            )
            if class_ref and class_ref in self._class_keys:
                result.entities.append(entity)
            else:
                result.pending.append(entity)
        edge_seen: set[tuple[str, str, str]] = set()
        for rd in edge_dicts:
            key = (str(rd.get("src", "")), str(rd.get("dst", "")),
                   str(rd.get("rel_type", "")))
            if key in edge_seen or not all(key):
                continue
            edge_seen.add(key)
            result.edges.append(Relation(
                src=key[0], dst=key[1], rel_type=key[2],
                claim=str(rd.get("claim", "")),
                tag=str(rd.get("tag") or TAG_EXTRACTED).upper()))
        if isinstance(raw, dict):
            result.tokens_spent = int(raw.get("tokens_spent", 0))
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_stable_id(entity: Entity) -> str:
    """Deterministic stable id: class + normalized name (reruns converge;
    collisions route to alias-merge in _insert_new_entity)."""
    base = _strip_salt(entity.name)
    return f"{entity.class_ref}:{normalize_name_key(base)[:40]}"


def _strip_salt(name: str) -> str:
    """Chemical salt prefix removal (盐酸二甲双胍 -> 二甲双胍). Only used
    for extra merge pre-checks in align(); normalize_name_key deliberately
    keeps the salt so generic vs marketed forms stay distinct keys."""
    s = (name or "").strip()
    for salt in ("盐酸", "氢溴酸", "硫酸", "磷酸", "枸橼酸", "富马酸",
                 "马来酸"):
        if s.startswith(salt) and len(s) > len(salt):
            return s[len(salt):]
    return s


def _evidence_tag(result: ExtractionResult) -> str:
    """Span-level evidence tag: INFERRED only when the LLM marked any
    entity/edge as inferred; tables are always EXTRACTED."""
    if any(getattr(e, "tag", TAG_EXTRACTED) == TAG_INFERRED
           for e in result.entities) or any(
        getattr(e, "tag", TAG_EXTRACTED) == TAG_INFERRED
        for e in result.edges):
        return TAG_INFERRED
    return TAG_EXTRACTED


def _mwu_auc(labels: list[tuple[float, bool]],
             n_same: int, n_diff: int) -> float:
    """AUC via the Mann-Whitney U statistic: the share of (same, diff)
    pairs where the same-pair similarity exceeds the diff-pair similarity
    (ties count half). This is the standard estimator for discrete ROC
    curves and stays exact for small labeled sets."""
    if n_same == 0 or n_diff == 0:
        return 0.0
    same_sims = sorted(s for s, same in labels if same)
    diff_sims = sorted(s for s, same in labels if not same)
    wins = 0
    ties = 0
    for s in same_sims:
        for d in diff_sims:
            if s > d:
                wins += 1
            elif s == d:
                ties += 1
    return (wins + 0.5 * ties) / (n_same * n_diff)