"""
KnowEvo MCP tool schemas (T-07b) - the single schema source.

The 8-tool vocabulary is frozen in knowevo/mcp_servers/knowevo_mcp/SPEC.md;
T-07b delivers kg_search + kg_stats (the graph-query pair). Both the
standalone FastMCP server (server.py) and the Local-MCP inner registration
(tool_collection/mcp/kg_tools.py, T-08 wiring) import from here, so the
shapes can never drift between the two registration surfaces.

Every tool returns used_tokens / elapsed_ms so the cost ledger can collect
from the outermost boundary (SPEC discipline 2).
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

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
# kg_multi_hop (T-09) - the reasoning-path tool, version-pinned
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
# decision_card_render (T-19) - the decision-card production tool
# ---------------------------------------------------------------------------

class DecisionCardInput(BaseModel):
    """Input of the decision-card tool (one of the 8 frozen SPEC tools).

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
# skill_template_apply (T-20, additive 9th tool beyond the frozen 8) -
# instantiate a mined SKILL.md template from skill_template_t
# ---------------------------------------------------------------------------

class SkillTemplateApplyInput(BaseModel):
    """Input of the skill-template apply tool (T-20).

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