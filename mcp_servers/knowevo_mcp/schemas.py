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