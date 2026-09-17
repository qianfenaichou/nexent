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


# ---------------------------------------------------------------------------
# Decision layer (K3, T-09): routes, evidence chains, decision cards.
# Field names follow the frozen card JSON in memo 04-K3 section 3, so the
# payload persisted to decision_card_t is the schema itself.
# ---------------------------------------------------------------------------

# Route vocabulary (02-tech-plan 3.1): retrieval, reasoning, both.
ROUTE_RETRIEVAL = "R"
ROUTE_REASONING = "M"
ROUTE_BOTH = "RM"

# Card verdicts. INSUFFICIENT_EVIDENCE is a first-class outcome, not an
# error: refusing beats fabricating when no key fact has evidence.
DECISION_RECOMMEND = "RECOMMEND"
DECISION_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

# Provenance tags, matching kg_evidence_t.tag.
TAG_EXTRACTED = "EXTRACTED"
TAG_INFERRED = "INFERRED"

# Which channel a proposition came from (02-tech-plan 3.4 evidence fusion).
CHANNEL_DOC = "doc"
CHANNEL_KG = "kg"
CHANNEL_KG_DOC = "kg+doc"


@dataclass
class Route:
    """Routing verdict plus how it was reached.

    ``level`` records which layer decided (L1 rule / L2 few-shot / L3
    default-safe), because the E6 experiment measures route hit rate per
    layer and the escalation rule (>=2 signature failures) needs to know
    whether a decision was a confident classification or a fallback.
    """
    route: str = ROUTE_BOTH
    confidence: float = 0.0
    level: str = "L3"
    reason: str = ""


@dataclass
class DocHit:
    """One retrieval-path document hit (from knowledge_base_search).

    Kept deliberately thin: the decision layer consumes hits for evidence
    fusion, so it needs the claim text and enough provenance to jump back
    to the passage, nothing more.
    """
    doc_id: Any
    doc_title: str = ""
    span_text: str = ""
    score: float = 0.0
    span_loc: dict[str, Any] = field(default_factory=dict)


@dataclass
class PathScore:
    """Beam-search score for one candidate path.

    The three components are stored separately (not just their sum) so the
    T-10b ablation can show which signal carried the ranking, and so a
    path that scored high purely on connectivity can be spotted and
    penalised when every edge lacks a claim.
    """
    path: Any = None
    relevance: float = 0.0
    evidence_richness: float = 0.0
    conflict_signal: float = 0.0
    evidence_missing: bool = False

    @property
    def total(self) -> float:
        """Weighted sum. Relevance dominates; a conflict signal subtracts
        because contested knowledge should not outrank clean evidence."""
        return (0.6 * self.relevance + 0.4 * self.evidence_richness
                - 0.3 * self.conflict_signal)


@dataclass
class Provenance:
    """Where one proposition comes from - the traceability unit.

    ``kg_path`` holds the alternating entity/relation walk as readable
    strings (e.g. "Drug:metformin -> indicated_for -> Disease:t2dm"), which
    is what a human reviewer actually reads; ``version_pinned`` records
    that the walk was constrained to the card's knowledge version, so a
    card can prove its claims were not assembled from expired facts.
    """
    doc: str = ""
    span: str = ""
    kg_path: list[str] = field(default_factory=list)
    version_pinned: bool = False


@dataclass
class EvidenceItem:
    """One proposition in the evidence chain with full provenance.

    ``source_channel`` is one of CHANNEL_*; ``contested`` marks a
    cross-channel or cross-document contradiction that was surfaced rather
    than silently resolved.
    """
    claim: str = ""
    provenance: Provenance = field(default_factory=Provenance)
    tag: str = TAG_EXTRACTED
    source_channel: str = CHANNEL_KG
    contested: bool = False


@dataclass
class Counterfactual:
    """The 'what if we chose otherwise' note (top-1 candidate only).

    Material comes from the beam search's failed paths, so this is a
    by-product of the walk rather than an extra LLM call for content.
    """
    not_choose: str = ""
    tag: str = TAG_INFERRED


@dataclass
class Candidate:
    """One decision option with its calibrated confidence and evidence."""
    option: str = ""
    score: float = 0.0
    confidence_calibrated: float = 0.0
    evidence_chain: list[EvidenceItem] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    counterfactual: Counterfactual | None = None


@dataclass
class ConflictAdjudication:
    """A contradiction and how it was handled (never silently dropped)."""
    conflict_id: str = ""
    type: str = ""
    resolution: str = ""


@dataclass
class EvidenceChain:
    """Assembled evidence for a question, with the contested flag the card
    surfaces as 'knowledge inconsistency'."""
    items: list[EvidenceItem] = field(default_factory=list)
    contested: bool = False
    failed_paths: list[Any] = field(default_factory=list)

    def has_evidence(self) -> bool:
        return bool(self.items)


@dataclass
class KnowledgeStamp:
    """The knowledge version a card was produced under.

    ``clock_source`` mirrors VersionClock.source so a card records whether
    its version was explicit, derived from a version row, or a fallback -
    the honest-traceability requirement from 02-tech-plan 3.3.
    """
    ontology_version: str | None = None
    kg_cutoff: Any = None
    clock_source: str = "now"


@dataclass
class DecisionCard:
    """The user-facing output (memo 04-K3 section 3 JSON, as a dataclass).

    ``calibration_applied`` is deliberately explicit: when no calibration
    table exists in eval_run_t the confidences pass through unchanged, and
    the card must say so rather than imply a calibration that never ran.
    """
    question_id: str = ""
    question: str = ""
    knowledge_stamp: KnowledgeStamp = field(default_factory=KnowledgeStamp)
    candidates: list[Candidate] = field(default_factory=list)
    decision: str = DECISION_RECOMMEND
    conflict_adjudications: list[ConflictAdjudication] = field(
        default_factory=list)
    uncertainty_notes: list[str] = field(default_factory=list)
    disclaimer: str = ""
    route: str = ROUTE_REASONING
    calibration_applied: bool = False
    knowledge_version_pinned: bool = False
    used_tokens: int = 0
    elapsed_ms: int = 0

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe dict for decision_card_t.payload.

        Walks the dataclass tree and stringifies anything JSONB cannot hold
        (UUID, datetime). ``dataclasses.asdict`` alone is not enough: it
        recurses through dataclasses but leaves those scalar types in
        place, and psycopg2 then fails at INSERT time rather than here.
        """
        import dataclasses
        import datetime as _dt
        import uuid as _uuid

        def convert(value: Any) -> Any:
            if dataclasses.is_dataclass(value) and not isinstance(value, type):
                return {f.name: convert(getattr(value, f.name))
                        for f in dataclasses.fields(value)}
            if isinstance(value, dict):
                return {str(k): convert(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(v) for v in value]
            if isinstance(value, (_dt.datetime, _dt.date)):
                return value.isoformat()
            if isinstance(value, _uuid.UUID):
                return str(value)
            return value

        return convert(self)


@dataclass
class HopCurve:
    """Output of calibrate_hops: one row per depth (K4 L5 calibration).

    Each row is {depth, accuracy, tokens, latency_ms, n_questions}; the
    curve is what fixes KW_MULTIHOP_MAX_DEPTH with evidence instead of a
    guess, and doubles as PPT material (02-tech-plan 3.2).
    """
    rows: list[dict[str, Any]] = field(default_factory=list)
    recommended_depth: int = 3
    testset_hash: str = ""

    def best_by(self, metric: str = "accuracy") -> int:
        """Depth with the best metric value (ties go to the shallower one,
        since every extra hop costs latency and tokens)."""
        if not self.rows:
            return self.recommended_depth
        best = max(self.rows, key=lambda r: (r.get(metric, 0.0),
                                             -r.get("depth", 99)))
        return int(best["depth"])


@dataclass
class ScoredPath:
    """A walked path together with its score and version verdict.

    ``version_valid`` is False exactly when the path contains an edge that
    was not in force at the pinned cutoff. Keeping invalid paths in the
    result (instead of dropping them) is what makes the pinned/on-off
    ablation observable: the same walk runs twice and only the flag moves.
    """
    path: Any = None
    score: PathScore = field(default_factory=PathScore)
    version_valid: bool = True
    invalid_edge_reason: str = ""


@dataclass
class PathSet:
    """Multi-hop output: the paths that carry evidence plus the failures.

    ``failed`` holds walks that hit a dead end or an expired edge; the
    counterfactual generator reads them, which is why they are returned
    rather than discarded (02-tech-plan 3.2: "Failed paths kept for
    counterfactual").

    ``claims_by_path`` / ``edges_by_path`` are keyed by ``id(path)`` and
    exist because ``Path`` itself only carries edge *ids* and claim texts:
    the assembly step needs the EdgeCards (for contested flags, evidence
    refs and time windows) without re-querying the store per edge.
    ``clock`` is the resolved version the walk was pinned to, kept so the
    card's knowledge stamp reports the same instant the walk actually used.
    """
    paths: list[Any] = field(default_factory=list)
    failed: list[Any] = field(default_factory=list)
    scored: list[Any] = field(default_factory=list)
    version_pinned: bool = False
    early_stopped: bool = False
    claims_by_path: dict[int, list[str]] = field(default_factory=dict)
    edges_by_path: dict[int, list[Any]] = field(default_factory=dict)
    clock: Any = None

    def has_paths(self) -> bool:
        return bool(self.paths)


@dataclass
class Timeline:
    """Knowledge-evolution timeline for an entity or a decision card.

    Events are ordered oldest-first and each carries its own ``at`` stamp,
    so the dashboard can render the evolution without knowing event types
    in advance. ``truncated`` tells the caller the history was longer than
    the requested limit - silent truncation would let a card claim a
    complete provenance it does not have.
    """
    entity_id: str | None = None
    decision_id: Any | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
