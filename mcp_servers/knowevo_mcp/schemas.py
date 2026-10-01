"""
KnowEvo MCP tool schemas - the single schema source.

The frozen 8-tool vocabulary lives in knowevo/mcp_servers/knowevo_mcp/
SPEC.md; delivered kg_search + kg_stats (the graph-query pair). Both
the standalone FastMCP server (server.py) and the Local-MCP inner
registration (tool_collection/mcp/kg_tools.py, wiring) import from
here, so the shapes can never drift between the two registration surfaces.

Registered today = 9 tools (kg_search / kg_stats / kg_multi_hop /
kg_evolution_trace / ontology_diff / evidence_verify /
decision_card_render / skill_template_apply / asset_search - single
source of truth: KG_MCP_TOOL_NAMES in backend/tool_collection/mcp/
kg_tools.py). asset_search closed the last frozen-vocabulary gap on
2026-09-29 (asset-search charter M1/M3: DocAssetService.search_assets +
this wrapper; the I/O follows the landed capability, deviation from the
memo-10 §1 sketch registered in competition/docs/verification-reports/
asset-search-shape-deviation-2026-09-29.md).

Every tool returns used_tokens / elapsed_ms so the cost ledger can collect
from the outermost boundary (SPEC discipline 2).
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Shared output shapes (SPEC: EntityCard / EdgeCard)
# ---------------------------------------------------------------------------

class EntityCard(BaseModel):
    stable_id: str
    name: str
    class_ref: str
    props: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)


class EdgeCard(BaseModel):
    id: str
    src: str
    dst: str
    rel_type: str
    claim: str
    props: dict[str, Any] = Field(default_factory=dict)
    contested: bool = False


# ---------------------------------------------------------------------------
# kg_search
# ---------------------------------------------------------------------------

class KGSearchInput(BaseModel):
    """Hard input guardrails live in Field constraints (SPEC discipline 3:
    hop <= 2, top_k <= 20) - FastMCP rejects out-of-range payloads."""

    query: str = Field(min_length=1, max_length=200)
    hop: int = Field(1, ge=1, le=2)
    top_k: int = Field(5, ge=1, le=20)
    ontology_version: str | None = None


class KGSearchOutput(BaseModel):
    entities: list[EntityCard] = Field(default_factory=list)
    edges: list[EdgeCard] = Field(default_factory=list)
    valid_view: datetime
    used_tokens: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# kg_stats
# ---------------------------------------------------------------------------

class KGStatsInput(BaseModel):
    scope: str = Field("graph", pattern="^(graph|full)$")


class KGStatsOutput(BaseModel):
    tenant_id: str
    scope: str
    entities: int
    edges_total: int
    edges_valid: int
    pending: int | None = None
    used_tokens: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# Structured error (SPEC discipline 4: {error_code, hint}, so the skill
# layer can downgrade instead of blind retry)
# ---------------------------------------------------------------------------

class ToolError(BaseModel):
    error_code: str
    hint: str


# ---------------------------------------------------------------------------
# kg_multi_hop - the reasoning-path tool, version-pinned
# ---------------------------------------------------------------------------

class HopStep(BaseModel):
    """One hop of the returned walk: the edge and the claim it asserted."""
    src: str
    dst: str
    rel_type: str
    claim: str
    evidence_id: str | None = None


class KGMultiHopPath(BaseModel):
    """One walked path as the Agent sees it.

    ``version_valid`` is the version-pinned verdict: False means the walk
    touched a fact outside the requested knowledge version. Such paths are
    returned rather than hidden - the caller needs to see that the
    evidence was rejected and why, and the ablation depends on both
    variants being observable.
    """
    entities: list[str] = Field(default_factory=list)
    hops: list[HopStep] = Field(default_factory=list)
    score: float = 0.0
    version_valid: bool = True


class KGMultiHopInput(BaseModel):
    """Hard guardrails live in Field constraints (SPEC discipline 3): depth
    <= 3 and beam <= 3 are enforced by the MCP layer itself, so a
    hallucinating Agent cannot ask for an unbounded walk."""

    question: str = Field(min_length=1, max_length=500)
    seeds: list[str] = Field(default_factory=list, max_length=10)
    depth: int = Field(3, ge=1, le=3)
    beam: int = Field(3, ge=1, le=3)
    ontology_version: str | None = None
    top_k: int = Field(5, ge=1, le=10)


class KGMultiHopOutput(BaseModel):
    paths: list[KGMultiHopPath] = Field(default_factory=list)
    failed: list[KGMultiHopPath] = Field(default_factory=list)
    version_pinned: bool = False
    ontology_version: str | None = None
    valid_view: datetime
    used_tokens: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# decision_card_render - the decision-card production tool
# ---------------------------------------------------------------------------

class DecisionCardInput(BaseModel):
    """Input of the decision-card tool (frozen 8-tool vocabulary; I/O follows the implemented capability,
    see competition/docs/verification-reports/l2-mcp-shape-deviation-2026-09-28.md).

    ``mode`` maps 1:1 onto ``DecisionService.render_card``: ``full``
    carries risks + counterfactual, ``lite`` skips them for
    latency-bound callers. The question bound is the hard guardrail
    (SPEC discipline 3): one card per call, never a batch.

    The output is deliberately NOT re-modeled here: the card payload is
    owned by ``services.knowevo.schemas.DecisionCardContract`` (wire
    format of decision_card_t.payload) and re-declaring it would be a
    second schema source - the drift this module exists to prevent. The
    handler returns that payload as a JSON-safe dict with two extra
    keys (``persisted``, ``card_id``; the contract is extra="allow") or
    a structured ``{error_code, hint}`` dict on failure.
    """

    question: str = Field(min_length=1, max_length=500)
    ontology_version: str | None = None
    mode: str = Field("full", pattern="^(full|lite)$")


# ---------------------------------------------------------------------------
# skill_template_apply (additive 9th tool beyond the frozen 8) -
# instantiate a mined SKILL.md template from skill_template_t
# ---------------------------------------------------------------------------

class SkillTemplateApplyInput(BaseModel):
    """Input of the skill-template apply tool.

    ``template_name`` bound mirrors the skill_template_t.name column
    (String(64), unique per tenant) so an impossible name is rejected by
    the MCP layer before any DB round-trip (SPEC discipline 3).
    ``variables`` are injection overrides on top of the template's stored
    defaults ({domain, task_type, relation_template, domain_rules}); the
    service stringifies the values and merges them, unknown {placeholders}
    in the markdown survive untouched.

    Like DecisionCardInput, the output is NOT re-modeled here: the apply
    payload (``name``/``skill_md``/``variables``/``reuse_count``) is owned
    by ``services.knowevo.skill_template_service.SkillTemplateService.
    apply_template``; the handler returns it as a JSON-safe dict with the
    SPEC cost fields (``used_tokens``, ``elapsed_ms``) added, or a
    structured ``{error_code, hint}`` dict (``template_not_found`` /
    ``skill_template_apply_failed``).
    """

    template_name: str = Field(min_length=1, max_length=64)
    variables: dict[str, str] = Field(default_factory=dict, max_length=16)


# ---------------------------------------------------------------------------
# asset_search (frozen vocabulary; closed 2026-09-29 by the asset-search
# charter M1/M3) - the registered-asset retrieval tool, a wrapper over
# DocAssetService.search_assets (services/knowevo/doc_asset_service.py)
# ---------------------------------------------------------------------------

class AssetCard(BaseModel):
    """One registered-asset hit as the Agent sees it.

    Mirrors the service layer's ``AssetHit`` 1:1 - re-declaring a different
    shape would be a second schema source (SPEC discipline 1). ``score``
    is the ES hybrid relevance normalized to 0-1 (top = 1.0); the PG ilike
    fallback scores 0.0 (no relevance signal). ``why`` is the auditable
    hit reason (raw ES score / PG ranking rule / applied filters); the
    parse gate (parse_status == processed) is applied uniformly on both
    backend paths and no parse-quality threshold is invented -
    parse_quality is surfaced for audit only.
    """

    id: str
    asset_no: str
    title: str
    modality: str
    doc_type: str
    authority_level: int
    score: float
    why: dict[str, Any] = Field(default_factory=dict)
    parse_status: str | None = None
    parse_quality: float | None = None
    superseded: bool = False


class AssetSearchInput(BaseModel):
    """Input of the asset-search tool (frozen 8-tool vocabulary; I/O
    follows the implemented capability, see competition/docs/
    verification-reports/asset-search-shape-deviation-2026-09-29.md).

    ``modality`` / ``doc_type`` are exact-match filters and
    ``authority_min`` a floor on the 1..4 authority ladder
    (1 national std, 2 guideline, 3 label, 4 popular science).
    ``include_superseded`` folds replaced versions by default - a search
    that surfaces both a drug label and the label that replaced it serves
    the caller stale facts unless it asked for the lineage. The query and
    limit bounds are the hard guardrails (SPEC discipline 3).
    """

    query: str = Field(min_length=1, max_length=200)
    modality: str | None = Field(None, max_length=12)
    doc_type: str | None = Field(None, max_length=24)
    authority_min: int | None = Field(None, ge=1, le=4)
    include_superseded: bool = False
    limit: int = Field(5, ge=1, le=20)


class AssetSearchOutput(BaseModel):
    assets: list[AssetCard] = Field(default_factory=list)
    valid_view: datetime
    used_tokens: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# kg_evolution_trace (frozen vocabulary) - the bi-temporal timeline tool,
# a thin wrapper over KGService.evolution_trace (services/knowevo/
# kg_service.py, the query surface frozen in kg_service.py.md)
# ---------------------------------------------------------------------------

class KGEvolutionTraceInput(BaseModel):
    """Input of the evolution-trace tool (frozen 8-tool vocabulary; I/O follows the implemented capability,
    see competition/docs/verification-reports/l2-mcp-shape-deviation-2026-09-28.md).

    Exactly one target is required: ``entity_id`` yields the bi-temporal
    relation events touching that entity (superseded edges included on
    purpose - a timeline that only shows the current view cannot show
    that anything evolved); ``decision_id`` yields the stored card's
    knowledge stamp. When both are given the service's documented
    precedence applies (the decision branch wins, matching
    ``KGService.evolution_trace``).
    """

    entity_id: str | None = Field(None, max_length=128)
    decision_id: str | None = Field(None, max_length=64)
    limit: int = Field(50, ge=1, le=200)

    @model_validator(mode="after")
    def _require_one_target(self) -> "KGEvolutionTraceInput":
        if not self.entity_id and not self.decision_id:
            raise ValueError("entity_id or decision_id is required: a trace "
                             "of nothing would be an empty answer pretending "
                             "to be a query")
        return self


class KGEvolutionTraceOutput(BaseModel):
    """Timeline rows as the Agent sees them.

    The event dicts are owned by ``KGService.evolution_trace`` (graph
    events carry src/dst/rel_type/claim/valid_at/invalid_at/contested/
    evidence_id; the decision event carries question_id/knowledge_stamp/
    needs_rerun) - re-declaring them here would be a second schema
    source. ``truncated`` is the honesty flag: True means the limit cut
    the history short, never a silently clipped provenance chain.
    """

    entity_id: str | None = None
    decision_id: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    valid_view: datetime
    used_tokens: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# ontology_diff (frozen vocabulary) - the persisted alignment-diff ledger,
# a thin wrapper over AlignmentService.list_diffs (services/knowevo/
# alignment_service.py)
# ---------------------------------------------------------------------------

class OntologyDiffInput(BaseModel):
    """Input of the ontology-diff tool (frozen 8-tool vocabulary; I/O follows the implemented capability,
    see competition/docs/verification-reports/l2-mcp-shape-deviation-2026-09-28.md).

    Read-only listing of the persisted document-version diffs
    (doc_version_diff_t) for the tenant, newest first, each reduced to
    {diff_id, old_asset_no, new_asset_no, created_at, change_counts}.
    No LLM and no embedding call happens behind this tool: detect() and
    the write paths stay in the alignment pipeline, the MCP surface only
    reads the ledger.
    """

    limit: int = Field(20, ge=1, le=200)


class OntologyDiffOutput(BaseModel):
    """Diff ledger rows as the Agent sees them.

    The row dicts are owned by ``AlignmentService.list_diffs`` - not
    re-modeled here, same single-source discipline as the decision-card
    payload. ``count`` mirrors len(diffs) for callers that count before
    reading.
    """

    diffs: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    used_tokens: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# evidence_verify (frozen vocabulary) - the evidence reverse-lookup tool,
# a thin wrapper over GraphStore.reachable_decisions (the GIN reverse
# lookup over kg_evidence_t.entity_refs frozen in graph_store.py.md)
# ---------------------------------------------------------------------------

class EvidenceVerifyInput(BaseModel):
    """Input of the evidence-verify tool (frozen 8-tool vocabulary; I/O follows the implemented capability,
    see competition/docs/verification-reports/l2-mcp-shape-deviation-2026-09-28.md).

    ``entity_ids`` are stable_ids; the cap keeps a hallucinating Agent
    from fanning a lookup into hundreds of ids (SPEC discipline 3).
    """

    entity_ids: list[str] = Field(min_length=1, max_length=20)


class EvidenceVerifyOutput(BaseModel):
    """Evidence rows referencing the given entities, as the Agent sees them.

    Honest scope: the ids are kg_evidence_t rows whose entity_refs overlap
    the input (the same rows the decision layer cites in evidence chains).
    The tool answers "which evidence references these entities" - it is a
    reverse lookup, NOT a truthfulness verdict; a caller that needs a
    verdict renders a decision card instead.
    """

    entity_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    matched: int = 0
    used_tokens: int = 0
    elapsed_ms: int = 0
