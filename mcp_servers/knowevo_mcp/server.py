"""
KnowEvo FastMCP server (T-07b, extended by T-09/T-19/T-20) - graph-query
and decision-layer tool service.

Tools delivered so far (SPEC.md freezes 8; kg_multi_hop and
decision_card_render landed here; skill_template_apply is T-20's additive
reuse-loop tool):

    kg_search             lexical entity lookup + 1..2 hop neighborhood
    kg_stats              graph scale numbers
    kg_multi_hop          version-pinned beam walk (B2: every hop constrained
                          to the facts valid at the requested knowledge version)
    decision_card_render  question -> evidence-backed decision card (T-19)
    skill_template_apply  mined SKILL.md template -> rendered instance (T-20)

Standalone form: ``python -m mcp_servers.knowevo_mcp.server`` serves a
FastMCP app that tools are registered on. The Local-MCP inner form
(backend/tool_collection/mcp/kg_tools.py) imports the same handler
functions and Pydantic schemas - single schema source, dual registration,
no drift (SPEC discipline 1).

Errors are structured {error_code, hint} (SPEC discipline 4) so the skill
layer can choose downgrade over blind retry. ``tenant_id`` is a server-level
setting in v0; request-scoped tenant comes with the T-08 wiring.
"""
import logging
import time
from datetime import UTC, datetime

from fastmcp import FastMCP
from mcp_servers.knowevo_mcp.schemas import (
    DecisionCardInput,
    KGMultiHopInput,
    KGMultiHopOutput,
    KGMultiHopPath,
    KGSearchInput,
    KGSearchOutput,
    KGStatsInput,
    KGStatsOutput,
    SkillTemplateApplyInput,
    ToolError,
)
from mcp_servers.knowevo_mcp.schemas import (
    EdgeCard as _EdgeCard,
)
from mcp_servers.knowevo_mcp.schemas import (
    EntityCard as _EntityCard,
)
from mcp_servers.knowevo_mcp.schemas import (
    HopStep as HopStepSchema,
)

SERVICE_NAME = "knowevo"

logger = logging.getLogger(__name__)

mcp = FastMCP(SERVICE_NAME)

# v0: single-tenant server setting; request-scoped tenant comes with the
# T-08 wiring (upstream auth header -> tenant). Tests inject a fake store
# through the module-level hook below.
_graph_store = None
_default_tenant = ""
# T-09: the decision service is what owns the pinned beam walk, so the
# multi-hop handler delegates to it. Same injection shape as the store.
_decision_service = None
# T-20: the skill-template service owns the reuse loop over skill_template_t;
# same injection shape so tests can supply an in-memory seam.
_skill_template_service = None


def configure(tenant_id: str = "", store=None, decision_service=None,
              skill_template_service=None):
    """Server-level dependency injection (tests / T-08 wiring)."""
    global _graph_store, _default_tenant, _decision_service
    global _skill_template_service
    _default_tenant = tenant_id
    _graph_store = store
    if decision_service is not None:
        _decision_service = decision_service
    if skill_template_service is not None:
        _skill_template_service = skill_template_service


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


def _card_service(store=None, tenant_id: str = ""):
    """An LLM-bound decision service for card rendering (T-19).

    Deliberately not the ``_service()`` instance the walk tool uses: the
    card render needs the three-tier LLM chain (KW_LLM_*, 02-tech-plan
    3.1), while kg_multi_hop must stay LLM-free for callers that only
    want paths. Building the callable is cheap (model resolution happens
    at call time); when the backend-side LLM wiring is unreachable the
    service degrades to llm=None and the handler reports
    ``llm_unavailable`` for evidence-backed questions instead of
    pretending to render.
    """
    from services.knowevo.decision_service import DecisionService

    llm = None
    try:
        from services.knowevo.llm_client import build_llm_callable

        llm = build_llm_callable(tenant_id or _default_tenant)
    except Exception as exc:  # noqa: BLE001 - degrade, handler reports it
        logger.warning("decision-card llm wiring unavailable: %s", exc)
    return DecisionService(store=store or _store(),
                           tenant_id=tenant_id or _default_tenant, llm=llm)


def _template_service(tenant_id: str = ""):
    """The skill-template service for this request scope (T-20).

    Deliberately LLM-free: apply only renders the stored body_md and
    bumps the reuse counter - induction (the LLM channel) lives in the
    mining pipeline, not behind this tool.
    """
    if _skill_template_service is not None:
        return _skill_template_service
    from services.knowevo.skill_template_service import SkillTemplateService
    return SkillTemplateService(tenant_id=tenant_id or _default_tenant)


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


async def _card_evidence(svc, tenant: str, inputs: DecisionCardInput):
    """Seeds -> version-pinned walk -> fused evidence chain.

    Seeds come from the store's lexical entity lookup over the question
    itself; a store without that seam (or an empty graph) yields no
    seeds, the walk returns an empty PathSet and the chain stays empty -
    which render_card turns into the deterministic INSUFFICIENT_EVIDENCE
    refusal without a single LLM call (T-09 honest degradation, kept
    intact here). The document channel is not wired in production yet
    (the ES write path is upstream-owned), so the card is assembled from
    the graph channel only - claimed as such, not silently narrowed.
    """
    seeds: list[str] = []
    lookup = getattr(svc.store, "entity_lookup", None)
    if lookup is not None:
        hits = await lookup(tenant, inputs.question, 5)
        seeds = [h.stable_id for h in hits]
    result = await svc.multi_hop(inputs.question, seeds=seeds,
                                 version=inputs.ontology_version)
    chain = await svc.assemble_evidence(result)
    return chain, result.clock


async def decision_card_render_handler(inputs: DecisionCardInput,
                                       store=None,
                                       tenant_id: str = "",
                                       service=None) -> dict:
    """Question -> decision card (T-19): seeds -> pinned walk -> evidence
    chain -> rendered card, persisted to decision_card_t.

    Returns the card payload dict (``DecisionCardContract`` shape plus
    ``persisted``/``card_id``) or a structured error dict - never raises
    into the MCP runtime. An evidence-backed question with no LLM wired
    answers ``llm_unavailable`` rather than rendering a card it cannot
    ground; an evidence-free question keeps the T-09 refusal contract
    (INSUFFICIENT_EVIDENCE, zero LLM calls) and still persists, so the
    rerun ledger (needs_rerun) sees the refusal.
    """
    t0 = time.monotonic()
    store, tenant = _resolve(store, tenant_id)
    try:
        svc = service or _card_service(store, tenant)
        chain, clock = await _card_evidence(svc, tenant, inputs)
        if chain.has_evidence() and getattr(svc, "llm", None) is None:
            return _err("llm_unavailable",
                        "decision card rendering needs a configured LLM; "
                        "set KW_LLM_SMALL/MID/LARGE_MODEL_ID or the tenant "
                        "default LLM")
        card = await svc.render_card(inputs.question, chain,
                                     mode=inputs.mode, clock=clock)
        card.elapsed_ms = int((time.monotonic() - t0) * 1000)
        card_id = None
        persisted = False
        try:
            card_id = await svc.persist(card)
            persisted = True
        except Exception as exc:  # noqa: BLE001 - persist is best-effort
            logger.warning("decision card persist failed: %s", exc)
        payload = card.to_payload()
        payload["persisted"] = persisted
        if card_id is not None:
            payload["card_id"] = str(card_id)
        return payload
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        from services.knowevo.llm_client import LLMConfigurationError

        if isinstance(exc, LLMConfigurationError):
            return _err("llm_unavailable",
                        "no LLM configured for decision card rendering; "
                        "set KW_LLM_SMALL/MID/LARGE_MODEL_ID or the tenant "
                        "default LLM")
        return _err("decision_card_render_failed",
                    f"card render failed: {type(exc).__name__}")


async def skill_template_apply_handler(inputs: SkillTemplateApplyInput,
                                       tenant_id: str = "",
                                       service=None) -> dict:
    """Instantiate a mined SKILL.md template (T-20): render the stored
    body_md with the merged variables and bump the template's reuse_count
    (both done by ``SkillTemplateService.apply_template``).

    Returns the service-owned apply payload (``name``/``skill_md``/
    ``variables``/``reuse_count``) with the SPEC cost fields added, or a
    structured error dict - never raises into the MCP runtime. Honesty
    contract: apply does NOT write ``reuse_success`` - at apply time the
    outcome is unknown, the success rate is written back only by
    ``SkillTemplateService.record_reuse_outcome`` after a real run, and
    no success number is fabricated here. An unknown template answers
    ``template_not_found`` (echoing only the caller's own input, nothing
    internal); any other failure degrades to ``skill_template_apply_failed``
    with the exception type name only.
    """
    t0 = time.monotonic()
    try:
        svc = service or _template_service(tenant_id)
        result = await svc.apply_template(
            inputs.template_name, variables=dict(inputs.variables))
        result["used_tokens"] = 0
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    except KeyError as exc:
        if "template not found" in str(exc):
            return _err(
                "template_not_found",
                f"no skill template named '{inputs.template_name}' in this "
                "tenant; check the name against skill_template_t or the "
                "template library page")
        return _err("skill_template_apply_failed",
                    f"template apply failed: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("skill_template_apply_failed",
                    f"template apply failed: {type(exc).__name__}")


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


@mcp.tool(name="decision_card_render",
          description="Render a decision card for one question: candidates "
          "with evidence chains, confidence, risks, counterfactual, "
          "knowledge-version stamp and conflict adjudications. Refuses with "
          "INSUFFICIENT_EVIDENCE when no evidence supports the question.")
async def decision_card_render(inputs: DecisionCardInput) -> dict:
    out = await decision_card_render_handler(inputs)
    return out if isinstance(out, dict) else out.model_dump(mode="json")


@mcp.tool(name="skill_template_apply",
          description="Instantiate a mined SKILL.md template: render its "
          "parameterized body with the given variables (domain, "
          "task_type, relation_template, domain_rules overrides) and bump "
          "the reuse counter. The success rate is NOT written here - apply "
          "cannot know the outcome; it is updated only after a real run.")
async def skill_template_apply(inputs: SkillTemplateApplyInput) -> dict:
    out = await skill_template_apply_handler(inputs)
    return out if isinstance(out, dict) else out.model_dump(mode="json")


def main() -> None:
    """Standalone run: ``python -m mcp_servers.knowevo_mcp.server``."""
    mcp.run()


if __name__ == "__main__":
    main()