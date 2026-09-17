"""
KnowEvo FastMCP server (T-07b, extended by T-09) - graph-query tool service.

Tools delivered so far (SPEC.md freezes 8; the decision-layer trio is
T-09's, of which kg_multi_hop lands here):

    kg_search      lexical entity lookup + 1..2 hop neighborhood
    kg_stats       graph scale numbers
    kg_multi_hop   version-pinned beam walk (B2: every hop constrained to
                   the facts valid at the requested knowledge version)

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
    HopStep as HopStepSchema,
)
from mcp_servers.knowevo_mcp.schemas import (
    KGMultiHopInput,
    KGMultiHopOutput,
    KGMultiHopPath,
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
# T-09: the decision service is what owns the pinned beam walk, so the
# multi-hop handler delegates to it. Same injection shape as the store.
_decision_service = None


def configure(tenant_id: str = "", store=None, decision_service=None):
    """Server-level dependency injection (tests / T-08 wiring)."""
    global _graph_store, _default_tenant, _decision_service
    _default_tenant = tenant_id
    _graph_store = store
    if decision_service is not None:
        _decision_service = decision_service


def _store():
    if _graph_store is not None:
        return _graph_store
    from services.knowevo.graph_store import PgJsonbGraphStore
    return PgJsonbGraphStore()


def _service(store=None, tenant_id: str = ""):
    """The decision service for this request scope.

    Built on demand when no instance was injected: the pinned walk needs
    the service layer (it scores between hops), so the MCP tool cannot
    bypass it the way kg_search bypasses it for a plain neighborhood.
    """
    if _decision_service is not None:
        return _decision_service
    from services.knowevo.decision_service import DecisionService
    return DecisionService(store=store or _store(),
                           tenant_id=tenant_id or _default_tenant)


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


async def kg_multi_hop_handler(inputs: KGMultiHopInput,
                               store=None,
                               tenant_id: str = "",
                               service=None) -> KGMultiHopOutput | dict:
    """Version-pinned beam walk over the knowledge graph (T-09, B2).

    Returns KGMultiHopOutput on success or a structured error dict on
    failure (never raises into the MCP runtime). ``version_valid=False``
    paths are returned in ``failed`` rather than dropped, so the calling
    skill can see that evidence existed but was outside the requested
    knowledge version - which is the difference between "no knowledge" and
    "knowledge from a version you did not ask about".
    """
    t0 = time.monotonic()
    try:
        svc = service or _service(store, tenant_id)
        result = await svc.multi_hop(
            inputs.question, seeds=inputs.seeds, depth=inputs.depth,
            beam=inputs.beam, version=inputs.ontology_version)

        def to_path(entry) -> KGMultiHopPath:
            path = entry.path
            edges = result.edges_by_path.get(id(path), [])
            claims = result.claims_by_path.get(id(path), [])
            hops = []
            if edges:
                for idx, edge in enumerate(edges):
                    hops.append(HopStepSchema(
                        src=edge.src, dst=edge.dst, rel_type=edge.rel_type,
                        claim=claims[idx] if idx < len(claims) else edge.claim,
                        evidence_id=str((edge.props or {}).get("evidence_id"))
                        if (edge.props or {}).get("evidence_id") else None))
            else:
                # Boundary-probe entries carry no EdgeCards: the walk never
                # took that hop, the version cutoff refused it. Their two
                # entities and the edge id are all there is to report, and
                # reporting the step is the point - the caller must see that
                # evidence existed outside the requested version.
                hops.append(HopStepSchema(
                    src=path.entities[0], dst=path.entities[-1],
                    rel_type="", claim=(path.claims or [""])[0],
                    evidence_id=None))
            return KGMultiHopPath(
                entities=list(path.entities), hops=hops,
                score=entry.score.total, version_valid=entry.version_valid)

        # result.paths / result.failed, not a re-filter of scored: the
        # version boundary probe appends its findings straight to failed
        # (they never entered the beam), and re-filtering scored would
        # silently drop exactly the evidence the pinning claim rests on.
        paths = [to_path(e) for e in result.scored if e.version_valid]
        failed = [to_path(e) for e in result.failed]
        return KGMultiHopOutput(
            paths=paths[:inputs.top_k], failed=failed[:inputs.top_k],
            version_pinned=bool(result.version_pinned),
            ontology_version=inputs.ontology_version,
            valid_view=datetime.now(UTC),
            used_tokens=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("kg_multi_hop_failed",
                    f"pinned walk failed: {type(exc).__name__}")


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


@mcp.tool(name="kg_multi_hop", description="Version-pinned multi-hop walk: "
          "collect evidence paths constrained to a knowledge version.")
async def kg_multi_hop(inputs: KGMultiHopInput) -> dict:
    out = await kg_multi_hop_handler(inputs)
    return out.model_dump(mode="json") if not isinstance(out, dict) else out


def main() -> None:
    """Standalone run: ``python -m mcp_servers.knowevo_mcp.server``."""
    mcp.run()


if __name__ == "__main__":
    main()