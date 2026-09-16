"""
KnowEvo graph-layer shared data contracts (K2, T-06).

Pure dataclasses + geometry helpers, frozen interface shared by
kg_service (extraction/alignment/merge), alignment_service (LLM
adjudication) and the ingest_graph pipeline. Keeping them in one module
lets concurrent tasks (T-06a/b/c) code against a single contract without
importing each other's unfinished files.

Contract source: knowevo/backend/services/knowevo/kg_service.py.md
(interface frozen) and memo 02-tech-plan §2.3 (K2 extraction/alignment).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Spans and extraction output (K2 §1)
# ---------------------------------------------------------------------------

@dataclass
class EvidenceSpan:
    """Minimal extraction unit: one chunk with doc-level + position locators.

    ``span_hash`` is the idempotency key for extract runs (resume on crash:
    a span already extracted under the same hash is skipped).
    """
    doc_id: Any  # UUID of doc_asset_t row
    chunk_idx: int
    text: str
    modality: str = "text"  # text | table | caption
    page: int | None = None

    def span_hash(self) -> str:
        import hashlib
        return hashlib.sha256(
            f"{self.doc_id}:{self.chunk_idx}:{self.modality}:{self.text}".encode()
        ).hexdigest()[:32]


@dataclass
class Entity:
    """Ontology-anchored entity candidate from one span.

    ``class_ref`` must be an active ontology class stable_id; unmappable
    values (None after the LLM tried) route to the pending pool.
    ``ext_id`` carries an external identity primary key when the source
    text mentions one (ATC code, NMPA approval number, insurance code);
    P0 fix: alignment blocking prefers ext_id/alias-table over similarity.
    """
    name: str
    class_ref: str | None = None
    aliases: list[str] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)
    tag: str = "EXTRACTED"  # EXTRACTED | INFERRED
    ext_id: str | None = None
    ext_scheme: str | None = None  # atc | nmpa | insurance | alias_table
    evidence_id: Any | None = None


@dataclass
class Relation:
    """Edge candidate between two entities (names at extraction time;
    stable_ids resolved after alignment)."""
    src: str
    dst: str
    rel_type: str
    claim: str = ""
    tag: str = "EXTRACTED"
    evidence_id: Any | None = None


@dataclass
class ExtractionResult:
    """Output of one span's extraction (either LLM or deterministic channel)."""
    entities: list[Entity] = field(default_factory=list)
    edges: list[Relation] = field(default_factory=list)
    pending: list[Entity] = field(default_factory=list)  # unmappable names
    tokens_spent: int = 0
    channel: str = "llm"  # llm | table

    def all_entities(self) -> list[Entity]:
        return self.entities + self.pending


# ---------------------------------------------------------------------------
# Alignment (K2 §2, three-level: blocking -> vector -> LLM -> human)
# ---------------------------------------------------------------------------

@dataclass
class AlignDecision:
    """Result of aligning one Entity against the current graph.

    action ∈ {merge, new, pending_review}
    - merge: attach as alias of ``target_stable_id`` (records alias_type).
    - new: insert as fresh entity with a new stable_id.
    - pending_review: routed to human queue (L3) or LLM confidence too low.
    """
    action: str
    target_stable_id: str | None = None
    alias_type: str | None = None  # brand | generic | abbr | form
    confidence: float = 0.0
    level: str = "L0"  # L0 blocking | L1 vector | L2 llm | L3 human
    reason: str = ""


# ---------------------------------------------------------------------------
# Merge conflicts (K2 §3.2)
# ---------------------------------------------------------------------------

@dataclass
class IngestReport:
    """Per-batch accounting; also the cost-ledger hook structure."""
    added: int = 0
    merged: int = 0
    superseded: int = 0
    contended: int = 0
    pending: int = 0
    tokens_spent: int = 0
    wall_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic (table) channel + calibration + few-shot contracts
# ---------------------------------------------------------------------------

@dataclass
class ParsedTable:
    """One table block from a parsed document, feeding extract_table.

    Deterministic channel input: ``headers`` are the column labels (first
    column is the entity-name column by convention, the rest become props)
    and ``rows`` are raw cell strings. Zero LLM cost, byte-reproducible -
    design inspired by graphify's deterministic parsing.
    """
    doc_id: Any
    chunk_idx: int
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    title: str = ""
    page: int | None = None
    class_hint: str | None = None  # table title often names the class

    def span_hash(self) -> str:
        import hashlib
        body = "|".join(",".join(r) for r in self.rows)
        return hashlib.sha256(
            f"{self.doc_id}:{self.chunk_idx}:table:{','.join(self.headers)}:{body}"
            .encode()
        ).hexdigest()[:32]


@dataclass
class LabeledPair:
    """One human-labeled entity pair for threshold calibration (L3 open
    item). ``sim`` is the blocker-blocked cosine score; ``is_same`` is the
    human verdict. 200 pairs -> ROC -> tau1/tau2."""
    sim: float
    is_same: bool
    left: str = ""
    right: str = ""


@dataclass
class Thresholds:
    """Calibration output. tau1 = largest threshold whose false-merge rate
    stays <= 2%; tau2 = smallest threshold whose recall stays >= 95%.
    ``auc`` is the ROC area under curve for the audit record."""
    tau1: float = 0.80
    tau2: float = 0.60
    auc: float = 0.0
    n_pairs: int = 0
    false_merge_rate: float = 0.0
    recall: float = 0.0


@dataclass
class Example:
    """Few-shot example: an evidence span plus the expected extraction JSON.
    Three are static (cross-domain generic); two are retrieved dynamically
    from the confirmed-examples pool of the domain."""
    span_text: str
    output: dict[str, Any] = field(default_factory=dict)
    source: str = "static"  # static | dynamic


@dataclass
class PendingSummary:
    """Aggregate view of kg_pending_entity_t for the review surface."""
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_class: dict[str, int] = field(default_factory=dict)
    high_frequency: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Geometry helper (no numpy dependency; 20k-scale cosine stays in the
# service layer per memo 11 #8: pgvector absent, JSONB float arrays).
# ---------------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity; 0.0 on empty/mismatched vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def normalize_name_key(name: str) -> str:
    """Blocking key: trim, drop whitespace variants, full-width parens and
    trailing dosage-form suffixes for CJK drug labels (e.g. 片/胶囊/注射液).

    Conservative by design: only mechanical normalization, no fuzzy logic
    (the alias table owns semantics, not this function).
    """
    s = (name or "").strip().lower()
    for ch in "（）()· \t　":
        s = s.replace(ch, "")
    # strip common dosage-form suffixes (longest first)
    for suf in ("缓释片", "肠溶片", "缓释胶囊", "肠溶胶囊", "注射液", "咀嚼片",
                "分散片", "控释片", "胶囊", "片剂", "片", "口服液", "颗粒"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s
