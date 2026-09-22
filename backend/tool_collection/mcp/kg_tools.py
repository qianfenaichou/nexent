"""
KnowEvo Local-MCP inner registration - kg_search + kg_stats (T-07b),
kg_multi_hop (T-09), decision_card_render (T-19) and skill_template_apply
(T-20).

This is the second registration surface of the same tool handlers: the
standalone FastMCP server (mcp_servers/knowevo_mcp/server.py) serves them
in the deployed form, and this module exposes the SAME handlers for the
Nexent Agent's local MCP pipeline (tool_collection/mcp/) - single schema
source, dual registration, no drift (SPEC discipline 1).

The mount into ``local_mcp_service.py`` is a T-08 wiring task (that file is
upstream-owned and frozen); because the mounted unit is the shared FastMCP
app itself, tools added here reach the Agent on the next wiring run without
a second mount call.
"""
# The mcp_servers package lives at the repo root (outside the backend
# package); make it importable from a backend-only process. Same pattern
# as the test files (repo root on sys.path) - keeps the single-schema
# source reachable by both registration surfaces.
import os
import sys
from pathlib import Path

from fastmcp import FastMCP

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp_servers.knowevo_mcp.schemas import (
    DecisionCardInput,
    KGMultiHopInput,
    KGMultiHopOutput,
    KGSearchInput,
    KGSearchOutput,
    KGStatsInput,
    KGStatsOutput,
    SkillTemplateApplyInput,
)
from mcp_servers.knowevo_mcp.server import (
    configure as knowevo_configure,
)
from mcp_servers.knowevo_mcp.server import (
    decision_card_render_handler,
    kg_multi_hop_handler,
    kg_search_handler,
    kg_stats_handler,
    skill_template_apply_handler,
)

SERVICE_NAME = "knowevo"
KG_MCP_TOOL_NAMES = (
    "kg_search", "kg_stats", "kg_multi_hop", "decision_card_render",
    "skill_template_apply")

# Reuse the standalone app as the mountable unit: the same FastMCP instance
# can be mounted into local_mcp_service.py via ``local_mcp_service.mount``,
# so there is exactly one tool definition per name across both surfaces.
from mcp_servers.knowevo_mcp.server import mcp as knowevo_mcp_app


def tool_schemas() -> dict[str, dict]:
    """Schema manifest for registration docs / tests (no server needed)."""
    return {
        "kg_search": KGSearchInput.model_json_schema(),
        "kg_stats": KGStatsInput.model_json_schema(),
        "kg_multi_hop": KGMultiHopInput.model_json_schema(),
        "decision_card_render": DecisionCardInput.model_json_schema(),
        "skill_template_apply": SkillTemplateApplyInput.model_json_schema(),
    }


def handlers() -> dict[str, object]:
    """Handler map for direct invocation without an MCP runtime."""
    return {
        "kg_search": kg_search_handler,
        "kg_stats": kg_stats_handler,
        "kg_multi_hop": kg_multi_hop_handler,
        "decision_card_render": decision_card_render_handler,
        "skill_template_apply": skill_template_apply_handler,
    }


def wire(tenant_id: str = "") -> FastMCP:
    """Return the mounted app after configuring the shared store binding.

    T-08 calls this with no argument and mounts the result into
    local_mcp_service, so the mount is process-wide. Per-call tenants are
    resolved from the caller's Authorization header when the platform
    forwards one (mcp_servers/knowevo_mcp/server.py::_request_tenant); the
    built-in local MCP server of Nexent v2.5.1 sends no header
    (create_agent_info.py registers ``authorization_token: None``), so a
    single-tenant deployment can pin its tenant here through
    ``KW_MCP_TENANT_ID`` - the documented v0 server-level setting.
    """
    knowevo_configure(
        tenant_id=tenant_id or os.getenv("KW_MCP_TENANT_ID", ""))
    return knowevo_mcp_app


# Reference the output schemas so the module documents the full contract
# even before T-08 wiring (imports are used by documentation hooks).
__all__ = [
    "KG_MCP_TOOL_NAMES",
    "SERVICE_NAME",
    "DecisionCardInput",
    "KGMultiHopInput",
    "KGMultiHopOutput",
    "KGSearchInput",
    "KGSearchOutput",
    "KGStatsInput",
    "KGStatsOutput",
    "SkillTemplateApplyInput",
    "handlers",
    "tool_schemas",
    "wire",
]