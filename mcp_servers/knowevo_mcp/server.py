"""
KnowEvo FastMCP server - graph-query,
decision-layer and asset-retrieval tool service.

Tools delivered (SPEC.md freezes 8 tools plus the additive
skill_template_apply; all 9 are registered, asset_search having closed the
last frozen gap):

    kg_search             lexical entity lookup + 1..2 hop neighborhood
    asset_search          registered-asset retrieval over doc_asset_t:
                          ES-first hybrid with a deterministic PG fallback,
                          uniform parse gate and an auditable why per hit
                          (added 2026-09-29)
    kg_stats              graph scale numbers
    kg_multi_hop          version-pinned beam walk (every hop constrained
                          to the facts valid at the requested knowledge version)
    kg_evolution_trace    bi-temporal timeline for an entity or a decision
                          card (KGService.evolution_trace)
    ontology_diff         persisted document-version alignment diffs with
                          change-type counts (AlignmentService.list_diffs)
    evidence_verify       evidence rows referencing given entities (GIN
                          reverse lookup, GraphStore.reachable_decisions)
    decision_card_render question -> evidence-backed decision card 
    skill_template_apply mined SKILL.md template -> rendered instance 

Standalone form: ``python -m mcp_servers.knowevo_mcp.server`` serves a
FastMCP app that tools are registered on. The Local-MCP inner form
(backend/tool_collection/mcp/kg_tools.py) imports the same handler
functions and Pydantic schemas - single schema source, dual registration,
no drift (SPEC discipline 1).

Errors are structured {error_code, hint} (SPEC discipline 4) so the skill
layer can choose downgrade over blind retry. ``tenant_id`` resolves per
call: explicit argument > the caller's Authorization header (request-scoped
tenant of the shared MCP service) > server-level setting.
"""
import logging
import time
from datetime import UTC, datetime

from fastmcp import FastMCP

from mcp_servers.knowevo_mcp.schemas import (
    AssetCard as _AssetCard,
)
from mcp_servers.knowevo_mcp.schemas import (
    AssetSearchInput,
    AssetSearchOutput,
    DecisionCardInput,
    EvidenceVerifyInput,
    EvidenceVerifyOutput,
    KGEvolutionTraceInput,
    KGEvolutionTraceOutput,
    KGMultiHopInput,
    KGMultiHopOutput,
    KGMultiHopPath,
    KGSearchInput,
    KGSearchOutput,
    KGStatsInput,
    KGStatsOutput,
    OntologyDiffInput,
    OntologyDiffOutput,
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

# Tenant resolution is request-scoped first: the platform mounts this app
# once inside the shared MCP service, so the caller's Authorization header is
# the only per-call tenant source (same pattern as
# tool_collection/mcp/nl2agent_mcp_tools.py). The server-level setting stays
# the fallback for stdio / tests / single-tenant use, and tests inject a fake
# store through the module-level hook below.
_graph_store = None
_default_tenant = ""
# the decision service is what owns the pinned beam walk, so the
# multi-hop handler delegates to it. Same injection shape as the store.
_decision_service = None
# the skill-template service owns the reuse loop over skill_template_t;
# same injection shape so tests can supply an in-memory seam.
_skill_template_service = None
# L2: the KG service owns the bi-temporal evolution timeline and the
# alignment service owns the persisted diff ledger; same injection shape.
_kg_service = None
_alignment_service = None
# asset_search (2026-09-29): the doc-asset service owns the
# asset_search retrieval capability over doc_asset_t; same injection shape
# so tests can supply an in-memory seam.
_asset_service = None
# fusion phase 2 (2026-09-30): optional AssetRawListClient seam for
# the asset_search RRF branch. None keeps today's search_assets path.
_asset_raw_client = None


def configure(tenant_id: str = "", store=None, decision_service=None,
              skill_template_service=None, kg_service=None,
              alignment_service=None, asset_service=None,
              asset_raw_client=None):
    """Server-level dependency injection (tests / wiring)."""
    global _graph_store, _default_tenant, _decision_service
    global _skill_template_service, _kg_service, _alignment_service
    global _asset_service, _asset_raw_client
    _default_tenant = tenant_id
    _graph_store = store
    if decision_service is not None:
        _decision_service = decision_service
    if skill_template_service is not None:
        _skill_template_service = skill_template_service
    if kg_service is not None:
        _kg_service = kg_service
    if alignment_service is not None:
        _alignment_service = alignment_service
    if asset_service is not None:
        _asset_service = asset_service
    if asset_raw_client is not None:
        _asset_raw_client = asset_raw_client


def _store():
    if _graph_store is not None:
        return _graph_store
    from services.knowevo.es_raw_list import build_es_raw_client
    from services.knowevo.graph_store import PgJsonbGraphStore

    # 2026-09-30: when the ES raw-list adapter is configured, it
    # doubles as the store's es_client, making entity_lookup's ES-first
    # branch real in production. Without the env the store is constructed
    # exactly as before (bit-for-bit PG behaviour).
    client = build_es_raw_client()
    if client is not None:
        return PgJsonbGraphStore(es_client=client)
    return PgJsonbGraphStore()


def _request_tenant() -> str:
    """Tenant of the current MCP HTTP request, '' when unavailable.

    The shared MCP service serves every tenant from one process, so the
    caller's JWT is the only per-call tenant source. Failures degrade to ''
    (callers then keep the server-level setting) instead of failing the
    tool: a stdio call or an unauthenticated request must stay usable.
    Resolution failures are logged - header *names* only, never token
    values - because a silent '' turns every graph query tenant-less.
    """
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
    except Exception as exc:  # noqa: BLE001 - no HTTP context (stdio / direct call)
        logger.info("request-scoped tenant unavailable (no HTTP context): %s: %s",
                    type(exc).__name__, exc)
        return ""
    if request is None:
        logger.warning("request-scoped tenant unavailable (no HTTP request)")
        return ""
    authorization = request.headers.get("Authorization")
    if not authorization:
        logger.warning(
            "request-scoped tenant unavailable: no Authorization header "
            "(headers present: %s)", sorted(request.headers.keys()))
        return ""
    try:
        from utils.auth_utils import get_current_user_id

        _user_id, tenant_id = get_current_user_id(authorization)
    except Exception as exc:  # noqa: BLE001 - degrade to server-level default
        logger.warning("request-scoped tenant lookup failed: %s: %s",
                       type(exc).__name__, exc)
        return ""
    if not tenant_id:
        logger.warning("request-scoped tenant lookup returned an empty tenant")
    return tenant_id or ""


def _tenant(explicit: str = "") -> str:
    """Per-call tenant: explicit arg > request-scoped auth > server default."""
    return explicit or _request_tenant() or _default_tenant


def _service(store=None, tenant_id: str = ""):
    """The decision service for this request scope.

    Built on demand when no instance was injected: the pinned walk needs
    the service layer (it scores between hops), so the MCP tool cannot
    bypass it the way kg_search bypasses it for a plain neighborhood.
    """
    if _decision_service is not None:
        return _decision_service
    from services.knowevo.decision_service import DecisionService
    return DecisionService(store=store or _store(), tenant_id=_tenant(tenant_id))


def _resolve(store=None, tenant_id: str = ""):
    """(store, tenant) from explicit args or request/server settings."""
    return store or _store(), _tenant(tenant_id)


def _card_service(store=None, tenant_id: str = ""):
    """An LLM-bound decision service for card rendering.

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

    tenant = _tenant(tenant_id)
    llm = None
    try:
        from services.knowevo.llm_client import build_llm_callable

        llm = build_llm_callable(tenant)
    except Exception as exc:  # noqa: BLE001 - degrade, handler reports it
        logger.warning("decision-card llm wiring unavailable: %s", exc)
    return DecisionService(store=store or _store(), tenant_id=tenant, llm=llm)


def _template_service(tenant_id: str = ""):
    """The skill-template service for this request scope.

    Deliberately LLM-free: apply only renders the stored body_md and
    bumps the reuse counter - induction (the LLM channel) lives in the
    mining pipeline, not behind this tool.
    """
    if _skill_template_service is not None:
        return _skill_template_service
    from services.knowevo.skill_template_service import SkillTemplateService
    return SkillTemplateService(tenant_id=_tenant(tenant_id))


def _kg_service_for(store=None, tenant_id: str = ""):
    """The KG service backing kg_evolution_trace (query surface).

    Deliberately ``KGService.evolution_trace`` and not a re-implementation:
    the entity branch needs ``store.list_relations_by_entity`` - the
    bi-temporal relation list that includes superseded edges, which the
    GraphStore seams cannot return (``neighbors`` yields the current view
    or the invalidated set, never both, so no faithful timeline can be
    assembled from it). That seam lives on kg_service's own PgStore, so
    the default builds the service with that adapter; a caller-supplied
    store is used only when it actually carries the seam. Tests inject a
    fake service through configure() / the handler argument.
    """
    if _kg_service is not None:
        return _kg_service
    from services.knowevo.kg_service import KGService, PgStore

    graph = store or _store()
    if not hasattr(graph, "list_relations_by_entity"):
        graph = PgStore()
    return KGService(store=graph, tenant_id=_tenant(tenant_id))


def _alignment_service_for(tenant_id: str = ""):
    """The alignment service backing ontology_diff (the diff ledger).

    Read-only use: ``list_diffs`` touches only the session seam - no LLM
    and no embedding callable - so the default construction is cheap and
    the tool never spends a token. Tests inject a fake through
    configure() / the handler argument.
    """
    if _alignment_service is not None:
        return _alignment_service
    from services.knowevo.alignment_service import AlignmentService

    return AlignmentService(tenant_id=_tenant(tenant_id))


def _asset_service_for(tenant_id: str = ""):
    """The doc-asset service backing asset_search.

    Tenant-agnostic on purpose: ``DocAssetService.search_assets`` takes
    the tenant per call (the handler resolves it via ``_tenant``), so the
    default construction carries no state beyond the optional ES client
    seam - and without an injected client it degrades to the deterministic
    PG path rather than pretending to search ES. Tests inject a fake
    through configure() / the handler argument.
    """
    if _asset_service is not None:
        return _asset_service
    from services.knowevo.doc_asset_service import DocAssetService

    return DocAssetService()


def _err(code: str, hint: str) -> dict:
    """Structured error payload (SPEC discipline 4)."""
    return ToolError(error_code=code, hint=hint).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tool handlers - pure functions over (store, tenant), shared by both the
# FastMCP registration and the Local-MCP inner form (kg_tools.py).
# ---------------------------------------------------------------------------

def _kg_output(sub, entities, t0: float) -> KGSearchOutput:
    """Shared KGSearchOutput builder for both kg_search paths (Standards
    review 2026-09-30: the card mapping used to be duplicated verbatim in
    the fused and legacy branches)."""
    edge_rows = sub.edges if sub is not None else []
    edges = [_EdgeCard(
        id=str(e.id), src=e.src, dst=e.dst, rel_type=e.rel_type,
        claim=e.claim, props=dict(e.props), contested=e.contested,
    ) for e in edge_rows]
    return KGSearchOutput(
        entities=entities, edges=edges, valid_view=datetime.now(UTC),
        used_tokens=0,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )


async def _kg_search_fused(inputs: KGSearchInput, store, tenant: str,
                           t0: float):
    """Fusion attempt for kg_search; ``None`` means "fall back".

    When the ES raw-list adapter is configured, the three-way RRF fusion
    (rrf_fusion.fuse, k=60) ranks the seeds: the BM25 raw list over the
    entity ES index, an honestly empty dense slot (the entity index has no
    embedding field - recorded in the fusion audit, never fabricated) and
    the deterministic graph route (entity_lookup seeds -> per-hop BFS ->
    ``(hop, -degree, stable_id)``). The neighborhood expansion and the
    output membership stay exactly what ``store.neighbors`` returns - only
    the entity-card ORDER changes: seed cards lead in fused-rank order,
    the remaining neighborhood cards follow in the graph route's
    deterministic order. Missing env, any fusion failure or an empty
    fusion returns None so the caller reproduces the pre-L6 path bit for
    bit; exceptions never reach the MCP runtime and the error-code
    semantics stay untouched.
    """
    from services.knowevo.es_raw_list import build_es_raw_client
    from services.knowevo.graph_retrieve import ordering_key
    from services.knowevo.kg_fusion import fused_entity_cards
    from services.knowevo.rrf_fusion import DEFAULT_RRF_K

    # Reuse the store's injected adapter (same ES client, no per-request
    # double construction); fall back to the env-gated factory for stores
    # injected without one (tests / configure(store=...) seams).
    client = getattr(store, "es_client", None) or build_es_raw_client()
    if client is None:
        logger.debug("kg_search fusion skipped: ES raw-list adapter "
                     "not configured")
        return None
    outcome = await fused_entity_cards(
        client, store, tenant, inputs.query,
        seed_top_k=inputs.top_k, hop=inputs.hop, k=DEFAULT_RRF_K)
    seed_ids = outcome.ids[:inputs.top_k]
    if not seed_ids:
        logger.debug("kg_search fusion produced no seeds; falling back")
        return None
    sub = await store.neighbors(tenant, list(seed_ids), hop=inputs.hop)

    seed_rank = {sid: i for i, sid in enumerate(seed_ids)}
    route = outcome.graph_route
    hop_by_id = route.hop_by_id if route is not None else {}
    degree_by_id = route.degree_by_id if route is not None else {}
    seed_cards = sorted(
        (e for e in sub.entities if e.stable_id in seed_rank),
        key=lambda e: seed_rank[e.stable_id])
    tail_cards = sorted(
        (e for e in sub.entities if e.stable_id not in seed_rank),
        key=lambda e: ordering_key(e.stable_id, hop_by_id, degree_by_id))

    entities = [_EntityCard(
        stable_id=e.stable_id, name=e.name, class_ref=e.class_ref,
        props=dict(e.props), aliases=list(e.aliases),
    ) for e in seed_cards + tail_cards]
    return _kg_output(sub, entities, t0)


async def kg_search_handler(inputs: KGSearchInput,
                            store=None,
                            tenant_id: str = "") -> KGSearchOutput | dict:
    """Entity lookup + neighborhood walk (current view).

    2026-09-30: when the ES raw-list adapter is configured, seeds
    are ranked by the three-way RRF fusion (BM25 over the entity index +
    honestly-empty dense slot + deterministic graph route; rrf_fusion
    k=60) before the usual 1..2 hop neighborhood expansion; output
    membership is unchanged and only the entity-card order becomes
    fusion-ranked. Without the adapter (or on any fusion failure or empty
    fusion) the handler falls back bit for bit to the lexical
    ``entity_lookup`` path below.

    Returns KGSearchOutput on success or a structured error dict on store
    failure (never raises into the MCP runtime).
    """
    t0 = time.monotonic()
    store, tenant = _resolve(store, tenant_id)
    try:
        fused = await _kg_search_fused(inputs, store, tenant, t0)
        if fused is not None:
            return fused
    except Exception as exc:
        # No noqa here - empirically verified 2026-09-30: RUF100 is active
        # in this repo and flags a fresh `noqa: BLE001` on this line as
        # unused (the 13 sibling directives above predate that behavior and
        # still pass; do not copy them onto new lines without re-verifying).
        # The fusion must never break the tool.
        logger.debug("kg_search fusion path failed (%s); falling back to "
                     "the lookup path", type(exc).__name__, exc_info=True)
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
        return _kg_output(sub, entities, t0)
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
    """Version-pinned beam walk over the knowledge graph.

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
    refusal without a single LLM call (honest degradation, kept
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
    """Question -> decision card : seeds -> pinned walk -> evidence
    chain -> rendered card, persisted to decision_card_t.

    Returns the card payload dict (``DecisionCardContract`` shape plus
    ``persisted``/``card_id``) or a structured error dict - never raises
    into the MCP runtime. An evidence-backed question with no LLM wired
    answers ``llm_unavailable`` rather than rendering a card it cannot
    ground; an evidence-free question keeps the refusal contract
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
    """Instantiate a mined SKILL.md template : render the stored
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


async def kg_evolution_trace_handler(inputs: KGEvolutionTraceInput,
                                     store=None,
                                     tenant_id: str = "",
                                     kg_service=None
                                     ) -> KGEvolutionTraceOutput | dict:
    """Knowledge-evolution timeline for one entity or one decision card
    (frozen vocabulary, wrapped over ``KGService.evolution_trace``).

    Returns KGEvolutionTraceOutput on success or a structured error dict
    on failure (never raises into the MCP runtime). Superseded edges come
    back on purpose: the tool's value is showing that something evolved,
    and a current-view-only answer cannot show that.
    """
    t0 = time.monotonic()
    store, tenant = _resolve(store, tenant_id)
    try:
        svc = kg_service or _kg_service_for(store, tenant)
        timeline = await svc.evolution_trace(
            entity_id=inputs.entity_id, decision_id=inputs.decision_id,
            limit=inputs.limit)
        return KGEvolutionTraceOutput(
            entity_id=timeline.entity_id,
            decision_id=str(timeline.decision_id)
            if timeline.decision_id is not None else None,
            events=[dict(e) for e in timeline.events],
            truncated=bool(timeline.truncated),
            valid_view=datetime.now(UTC),
            used_tokens=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("kg_evolution_trace_failed",
                    f"evolution trace failed: {type(exc).__name__}")


async def ontology_diff_handler(inputs: OntologyDiffInput,
                                tenant_id: str = "",
                                alignment_service=None
                                ) -> OntologyDiffOutput | dict:
    """Persisted document-version alignment diffs for the tenant (frozen
    vocabulary, wrapped over ``AlignmentService.list_diffs``).

    Returns OntologyDiffOutput on success or a structured error dict on
    failure (never raises into the MCP runtime). Read-only by design:
    detection and persistence stay in the alignment pipeline, the tool
    only reads the ledger the pipeline wrote.
    """
    t0 = time.monotonic()
    try:
        svc = alignment_service or _alignment_service_for(tenant_id)
        diffs = svc.list_diffs(limit=inputs.limit)
        return OntologyDiffOutput(
            diffs=[dict(d) for d in diffs], count=len(diffs),
            used_tokens=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("ontology_diff_failed",
                    f"diff query failed: {type(exc).__name__}")


async def evidence_verify_handler(inputs: EvidenceVerifyInput,
                                  store=None,
                                  tenant_id: str = ""
                                  ) -> EvidenceVerifyOutput | dict:
    """Evidence rows referencing the given entities (frozen vocabulary,
    wrapped over ``GraphStore.reachable_decisions`` - the GIN reverse
    lookup over kg_evidence_t.entity_refs, no traversal).

    Returns EvidenceVerifyOutput on success or a structured error dict on
    failure (never raises into the MCP runtime). A reverse lookup, not a
    truthfulness verdict: callers that need a verdict render a decision
    card instead.
    """
    t0 = time.monotonic()
    store, tenant = _resolve(store, tenant_id)
    try:
        rows = await store.reachable_decisions(tenant,
                                               list(inputs.entity_ids))
        return EvidenceVerifyOutput(
            entity_ids=list(inputs.entity_ids),
            evidence_ids=[str(r) for r in rows],
            matched=len(rows),
            used_tokens=0,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("evidence_verify_failed",
                    f"evidence reverse lookup failed: {type(exc).__name__}")


def _asset_raw_client_for():
    """Optional AssetRawListClient seam (fusion phase 2).

    Injected via configure() or resolved from the env-gated factory.
    ``None`` means "no fusion adapter" and the handler keeps today's
    search_assets path bit for bit.
    """
    if _asset_raw_client is not None:
        return _asset_raw_client
    from services.knowevo.asset_raw_list import build_asset_raw_client

    return build_asset_raw_client()


def _asset_output(hits, t0: float) -> AssetSearchOutput:
    """Shared AssetSearchOutput builder (same card mapping as legacy)."""
    return AssetSearchOutput(
        assets=[_AssetCard(
            id=h.id, asset_no=h.asset_no, title=h.title,
            modality=h.modality, doc_type=h.doc_type,
            authority_level=h.authority_level, score=h.score,
            why=dict(h.why), parse_status=h.parse_status,
            parse_quality=h.parse_quality, superseded=h.superseded)
            for h in hits],
        valid_view=datetime.now(UTC),
        used_tokens=0,
        elapsed_ms=int((time.monotonic() - t0) * 1000))


async def _asset_search_fused(inputs: AssetSearchInput, svc, tenant: str,
                              raw_client) -> list | None:
    """asset fusion attempt; ``None`` means "fall back to today".

    When the ignition gate is open (>= 2 non-empty routes -
    anti-theatre rule) the fused ``AssetHit.id`` order reorders the
    ``search_assets`` hits. Membership always comes from
    ``search_assets`` (parse gate / supersede fold / filters stay single
    source - A2); fused ids that the service did not return are dropped.
    No overlap or a closed gate returns None so the caller reproduces
    the pre-path bit for bit. Exceptions propagate to the handler's
    try/except which falls back.
    """
    from services.knowevo.asset_fusion import fused_asset_hits

    outcome = await fused_asset_hits(
        raw_client, tenant, inputs.query, top_k=inputs.limit)
    if not outcome.fired or not outcome.ids:
        logger.debug(
            "asset_search fusion gate closed (bm25=%s dense=%s); "
            "falling back", outcome.bm25_count, outcome.dense_count)
        return None
    hits = await svc.search_assets(
        tenant, inputs.query, modality=inputs.modality,
        doc_type=inputs.doc_type, authority_min=inputs.authority_min,
        include_superseded=inputs.include_superseded,
        limit=inputs.limit)
    by_id = {h.id: h for h in hits}
    ordered = [by_id[i] for i in outcome.ids if i in by_id]
    if not ordered:
        logger.debug("asset_search fusion produced no service-resolvable "
                     "ids; falling back")
        return None
    seen = {h.id for h in ordered}
    ordered.extend(h for h in hits if h.id not in seen)
    return ordered[:inputs.limit]


async def asset_search_handler(inputs: AssetSearchInput,
                               asset_service=None,
                               tenant_id: str = ""
                               ) -> AssetSearchOutput | dict:
    """Search the tenant's registered assets over doc_asset_t (frozen
    vocabulary, wrapped over ``DocAssetService.search_assets`` - the
    asset_search capability, 2026-09-29: ES-first hybrid over
    title+metadata with a deterministic PG fallback).

    Returns AssetSearchOutput on success or a structured error dict on
    failure (never raises into the MCP runtime). The parse gate
    (parse_status == processed) and the caller's filters are applied
    uniformly on both backend paths; every hit carries its auditable
    ``why`` (raw ES score or the PG ranking rule) - no score is
    fabricated where no relevance signal exists.

    fusion phase 2 (2026-09-30): when the asset raw-list adapter is
    configured AND the RRF ignition gate is open (>= 2 non-empty routes
    over the ``AssetHit.id`` space), the fused order reorders
    ``search_assets`` hits. With only one route live (today's dense slot
    is honestly empty) the gate stays closed and this tool reproduces
    the ES-first + PG-fallback path bit for bit - single-route fuse is
    theatre, not fusion.
    """
    t0 = time.monotonic()
    try:
        svc = asset_service or _asset_service_for(tenant_id)
        raw_client = _asset_raw_client_for()
        if raw_client is not None:
            try:
                fused = await _asset_search_fused(
                    inputs, svc, _tenant(tenant_id), raw_client)
                if fused is not None:
                    return _asset_output(fused, t0)
            except Exception as exc:
                # Fusion must never break the tool: silent fall-through to
                # today's path (same call-site discipline as kg_search).
                logger.debug(
                    "asset_search fusion path failed (%s); falling back",
                    type(exc).__name__, exc_info=True)
        hits = await svc.search_assets(
            _tenant(tenant_id), inputs.query, modality=inputs.modality,
            doc_type=inputs.doc_type, authority_min=inputs.authority_min,
            include_superseded=inputs.include_superseded,
            limit=inputs.limit)
        return _asset_output(hits, t0)
    except Exception as exc:  # noqa: BLE001 - structured error boundary
        return _err("asset_search_failed",
                    f"asset search failed: {type(exc).__name__}")


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


@mcp.tool(name="kg_evolution_trace", description="Knowledge-evolution "
          "timeline for one entity or one decision card: bi-temporal "
          "relation events (superseded edges included on purpose) and the "
          "card's knowledge stamp, oldest first, with an honest truncated "
          "flag when the history was cut short.")
async def kg_evolution_trace(inputs: KGEvolutionTraceInput) -> dict:
    out = await kg_evolution_trace_handler(inputs)
    return out.model_dump(mode="json") if not isinstance(out, dict) else out


@mcp.tool(name="ontology_diff", description="List persisted "
          "document-version alignment diffs for the tenant (newest first), "
          "each with change-type counts - the change ledger that drives "
          "ontology and graph updates. Read-only: no detection, no LLM.")
async def ontology_diff(inputs: OntologyDiffInput) -> dict:
    out = await ontology_diff_handler(inputs)
    return out.model_dump(mode="json") if not isinstance(out, dict) else out


@mcp.tool(name="evidence_verify", description="Verify which evidence rows "
          "reference the given entities: a reverse lookup over the "
          "evidence index (no graph traversal). Returns evidence ids and "
          "a match count - a reference check, not a truthfulness verdict; "
          "render a decision card when you need a verdict.")
async def evidence_verify(inputs: EvidenceVerifyInput) -> dict:
    out = await evidence_verify_handler(inputs)
    return out.model_dump(mode="json") if not isinstance(out, dict) else out


@mcp.tool(name="asset_search", description="Search the tenant's "
          "registered assets (doc_asset_t): ES-first hybrid over "
          "title+metadata with a deterministic PG fallback; parse gate "
          "and filters apply uniformly and every hit carries an "
          "auditable 'why' (raw ES score or the fallback ranking rule).")
async def asset_search(inputs: AssetSearchInput) -> dict:
    out = await asset_search_handler(inputs)
    return out.model_dump(mode="json") if not isinstance(out, dict) else out


def main() -> None:
    """Standalone run: ``python -m mcp_servers.knowevo_mcp.server``."""
    mcp.run()


if __name__ == "__main__":
    main()