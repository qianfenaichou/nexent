"""
KnowEvo FastMCP server (T-07b) - standalone graph-query tool service.

Two tools in this delivery (SPEC.md freezes 8; kg_search + kg_stats are the
graph-query pair, the decision-chain tools are T-09):

    kg_search  lexical entity lookup + 1..2 hop neighborhood
    kg_stats   graph scale numbers

Standalone form: ``python -m mcp_servers.knowevo_mcp.server`` serves a
FastMCP app that tools are registered on. The Local-MCP inner form
(backend/tool_collection/mcp/kg_tools.py) imports the same handler
functions and Pydantic schemas - single schema source, dual registration,
no drift (SPEC discipline 1).

Errors are structured {error_code, hint} (SPEC discipline 4) so the skill
layer can choose downgrade over blind retry. ``tenant_id`` is a server-level
setting in v0; request-scoped tenant comes with the T-08 wiring.
"""
import time
from datetime import UTC, datetime

from fastmcp import FastMCP
from mcp_servers.knowevo_mcp.schemas import (
    EdgeCard as _EdgeCard,
)
from mcp_servers.knowevo_mcp.schemas import (
    EntityCard as _EntityCard,
)
from mcp_servers.knowevo_mcp.schemas import (
    KGSearchInput,
    KGSearchOutput,
    KGStatsInput,
    KGStatsOutput,
    ToolError,
)

SERVICE_NAME = "knowevo"

mcp = FastMCP(SERVICE_NAME)

# v0: single-tenant server setting; request-scoped tenant comes with the
# T-08 wiring (upstream auth header -> tenant). Tests inject a fake store
# through the module-level hook below.
_graph_store = None
_default_tenant = ""


def configure(tenant_id: str = "", store=None):
    """Server-level dependency injection (tests / T-08 wiring)."""
    global _graph_store, _default_tenant
    _default_tenant = tenant_id
    _graph_store = store


def _store():
    if _graph_store is not None:
        return _graph_store
    from services.knowevo.graph_store import PgJsonbGraphStore
    return PgJsonbGraphStore()


def _resolve(store=None, tenant_id: str = ""):
    """(store, tenant) from explicit args or server-level settings."""
    return store or _store(), tenant_id or _default_tenant


def _err(code: str, hint: str) -> dict:
    """Structured error payload (SPEC discipline 4)."""
    return ToolError(error_code=code, hint=hint).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tool handlers - pure functions over (store, tenant), shared by both the
# FastMCP registration and the Local-MCP inner form (kg_tools.py).
# ---------------------------------------------------------------------------

async def kg_search_handler(inputs: KGSearchInput,
                            store=None,
                            tenant_id: str = "") -> KGSearchOutput | dict:
    """Lexical entity lookup + neighborhood walk (current view).

    Returns KGSearchOutput on success or a structured error dict on store
    failure (never raises into the MCP runtime).
    """
    t0 = time.monotonic()
    store, tenant = _resolve(store, tenant_id)
    try:
        hits = await store.entity_lookup(tenant, inputs.query, inputs.top_k)
        hit_ids = [h.stable_id for h in hits]
        sub = None
        if hit_ids:
            sub = await store.neighbors(tenant, hit_ids, hop=inputs.hop)

        entities = [_EntityCard(
            stable_id=e.stable_id, name=e.name, class_ref=e.class_ref,
            props=dict(e.props), aliases=list(e.aliases),
        ) for e in (sub.entities if sub else [])]
        edges = [_EdgeCard(
            id=str(e.id), src=e.src, dst=e.dst, rel_type=e.rel_type,
            claim=e.claim, props=dict(e.props), contested=e.contested,
        ) for e in (sub.edges if sub else [])]

        return KGSearchOutput(
            entities=entities, edges=edges, valid_view=datetime.now(UTC),
            used_tokens=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("kg_search_failed",
                    f"graph query failed: {type(exc).__name__}")


async def kg_stats_handler(inputs: KGStatsInput,
                           store=None,
                           tenant_id: str = "") -> KGStatsOutput | dict:
    """Graph scale numbers (scope=full adds the pending pool)."""
    t0 = time.monotonic()
    store, tenant = _resolve(store, tenant_id)
    try:
        stats = await store.stats(tenant, inputs.scope)
        return KGStatsOutput(
            tenant_id=str(stats["tenant_id"]),
            scope=stats["scope"],
            entities=stats["entities"],
            edges_total=stats["edges_total"],
            edges_valid=stats["edges_valid"],
            pending=stats.get("pending"),
            used_tokens=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("kg_stats_failed",
                    f"stats query failed: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# FastMCP registration (standalone form). Tool signatures ARE the Pydantic
# models - one field source, no decorator/schema drift (SPEC discipline 1).
# ---------------------------------------------------------------------------

@mcp.tool(name="kg_search", description="Search the knowledge graph: "
          "lexical entity lookup + neighborhood (current view).")
async def kg_search(inputs: KGSearchInput) -> dict:
    out = await kg_search_handler(inputs)
    return out.model_dump(mode="json") if not isinstance(out, dict) else out


@mcp.tool(name="kg_stats", description="Knowledge graph scale numbers.")
async def kg_stats(inputs: KGStatsInput) -> dict:
    out = await kg_stats_handler(inputs)
    return out.model_dump(mode="json") if not isinstance(out, dict) else out


def main() -> None:
    """Standalone run: ``python -m mcp_servers.knowevo_mcp.server``."""
    mcp.run()


if __name__ == "__main__":
    main()