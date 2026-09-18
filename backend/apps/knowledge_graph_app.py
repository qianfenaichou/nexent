"""HTTP endpoints for the KnowEvo ontology workbench (T-05a).

Layer rule (knowevo/backend/apps/knowledge_graph_app.py.md): this module
only parses input, checks tenant RBAC, and delegates to
services.knowevo.ontology_service - zero business logic here. tenant_id
always comes from the session via utils.auth_utils, never from the
request body, so the service layer stays free of request context.
"""
import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from database.role_permission_db import check_role_permission
from services.knowevo.ontology_service import OntologyService
from utils.auth_utils import get_current_user_context

logger = logging.getLogger(__name__)

# Prefix without /api: create_app(root_path="/api") mounts every router
# under /api, so the effective endpoint is /api/knowevo/... (T-08 wiring
# fix: this router previously declared "/api/knowevo", doubling the prefix).
router = APIRouter(prefix="/knowevo", tags=["knowevo"])

REVIEW_ACTIONS = {"confirm", "reject", "reparent"}

# Decision-card seed lookup width (T-19): the same bound the MCP tool
# uses - entity_lookup over the question text, then the pinned walk.
DECISION_SEED_TOP_K = 5


def _ontology_service() -> OntologyService:
    """One service instance per request; PgStore wiring stays lazy so the
    app imports cleanly in unit tests without a live database client."""
    return OntologyService(store=None)


def _decision_service(tenant_id: str):
    """One LLM-bound DecisionService per request (T-19 card route).

    Lazy imports on purpose: PgJsonbGraphStore opens a DB session pool
    and build_llm_callable pulls the OpenAI-model wiring, and neither
    may run at app-import time (the module must stay importable in unit
    tests with no database and no model config). Model resolution itself
    happens at call time inside the LlmRouter, so constructing the
    service is cheap even on tenants with no LLM configured - the card
    route maps that to a clean 503 below instead of leaking internals.
    """
    from services.knowevo.decision_service import DecisionService
    from services.knowevo.graph_store import PgJsonbGraphStore
    from services.knowevo.llm_client import build_llm_callable

    return DecisionService(store=PgJsonbGraphStore(),
                           llm=build_llm_callable(tenant_id),
                           tenant_id=tenant_id)


def _require_workbench_context(authorization: str | None) -> tuple[str, str, str]:
    """Parse the session, then require the RESOURCE.KNOWLEDGE_GRAPH MANAGE
    permission (RBAC rows are a T-08 wiring item; until then every role
    check falls back to refusing non-tenant callers only)."""
    user_id, tenant_id, role = get_current_user_context(authorization)
    # Upstream permission rows do not carry KNOWLEDGE_GRAPH yet (T-08 seeds
    # them). ADMIN-tier roles keep working through the KB MANAGE permission
    # so the workbench is not locked out before wiring.
    if (not check_role_permission(role, "RESOURCE", "KNOWLEDGE_GRAPH", "MANAGE")
            and not check_role_permission(role, "RESOURCE", "KB", "MANAGE")):
        raise HTTPException(
            status_code=403,
            detail="Ontology workbench permission is required",
        )
    return user_id, tenant_id, role


class ReviewActionRequest(BaseModel):
    action: str = Field(description="confirm | reject | reparent")
    ids: list[UUID] = Field(min_length=1)
    new_parent: str | None = Field(
        default=None,
        description="Target parent class for reparent; required when action=reparent",
    )
    reject_reason: str | None = None


class VersionCommitRequest(BaseModel):
    round_id: UUID | None = None
    base_version: str | None = None
    confirmed_ids: list[UUID] = Field(
        default_factory=list,
        description="Proposal ids to fold into the committed ops",
    )


@router.get("/ontology/proposals")
async def list_proposals(
    authorization: str | None = Header(None),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=40)] = 40,
    round_id: UUID | None = None,
    status: str | None = None,
):
    """Pending-review queue slice for one session (page_size capped at the
    40-per-session batch ceiling, K1 ss3)."""
    _, tenant_id, _ = _require_workbench_context(authorization)
    svc = _ontology_service()
    try:
        return await svc.list_review_queue(
            tenant_id, page=page, page_size=page_size,
            round_id=round_id, status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/ontology/proposals/review")
async def review_proposals(
    request: ReviewActionRequest,
    authorization: str | None = Header(None),
):
    """Apply one review action to a batch of proposals (A/X/P keyboard flow
    maps to confirm/reject/reparent)."""
    user_id, tenant_id, _ = _require_workbench_context(authorization)
    if request.action not in REVIEW_ACTIONS:
        raise HTTPException(status_code=422, detail="action must be confirm|reject|reparent")
    if request.action == "reparent" and not request.new_parent:
        raise HTTPException(status_code=422, detail="reparent requires new_parent")
    svc = _ontology_service()
    try:
        return await svc.review_proposals(
            tenant_id, [str(i) for i in request.ids], request.action,
            new_parent=request.new_parent,
            reviewed_by=user_id,
            reject_reason=request.reject_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/ontology/versions")
async def commit_version(
    request: VersionCommitRequest,
    authorization: str | None = Header(None),
):
    """Commit the confirmed queue slice as a new ontology version."""
    _, tenant_id, _ = _require_workbench_context(authorization)
    svc = _ontology_service()
    try:
        return await svc.commit_from_queue(
            tenant_id,
            round_id=str(request.round_id) if request.round_id else None,
            # Empty list = "fold the whole confirmed session" (workbench
            # commit button); only a non-empty list narrows the fold.
            confirmed_ids=[str(i) for i in request.confirmed_ids] or None,
            base_version=request.base_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/ontology/versions/active")
async def active_version(
    authorization: str | None = Header(None),
):
    """Latest published ontology version (label + snapshot + metrics).

    The workbench tree panel calls this to show the active version stamp.
    No published version answers 404 - the client maps that to ``null``
    (see frontend/services/knowledgeGraphService.ts) and renders "no
    version committed yet", which is different from an empty ontology.
    Declared before ``/ontology/versions/{version}/metrics`` so the static
    segment is unambiguous even if routing ever becomes order-sensitive.
    """
    _, tenant_id, _ = _require_workbench_context(authorization)
    svc = _ontology_service()
    row = await svc.get_active_row(tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no published ontology version")
    return row


@router.get("/ontology/versions/{version}/metrics")
async def version_metrics(
    version: str,
    authorization: str | None = Header(None),
):
    """K0 four metrics (cov/red/dep/align) for one committed version."""
    _, tenant_id, _ = _require_workbench_context(authorization)
    svc = _ontology_service()
    metrics = await svc.version_metrics(tenant_id, version)
    if metrics is None:
        raise HTTPException(status_code=404, detail=f"version {version} not found")
    return {"version": version, "metrics": metrics}


@router.get("/ontology/diff")
async def ontology_diff(
    authorization: str | None = Header(None),
    from_version: str = Query(alias="from"),
    to_version: str = Query(alias="to"),
):
    """Ops replay between two committed versions (T-12 diff view feeds
    off this too)."""
    _, tenant_id, _ = _require_workbench_context(authorization)
    svc = _ontology_service()
    ops = await svc.diff(from_version, to_version, tenant_id=tenant_id)
    return {"from": from_version, "to": to_version, "ops": ops}


# ── decision card (T-19) ──────────────────────────────────────────────


class DecisionCardRequest(BaseModel):
    """Body of POST /decision/card.

    The bounds mirror the MCP tool's ``DecisionCardInput`` (single
    guardrail source for the card surface): one question per call,
    500 chars, full|lite mode. ``tenant_id`` is deliberately absent -
    it comes from the session (see _require_workbench_context), never
    from the body, so a caller cannot read another tenant's knowledge.
    """

    question: str = Field(min_length=1, max_length=500)
    ontology_version: str | None = Field(
        default=None,
        description="Pin the card to this knowledge version; None = latest",
    )
    mode: str = Field(
        "full", pattern="^(full|lite)$",
        description="full carries risks + counterfactual; lite skips them",
    )


@router.post("/decision/card")
async def render_decision_card(
    request: DecisionCardRequest,
    authorization: str | None = Header(None),
):
    """Render and persist one decision card for the session's tenant.

    Same pipeline the MCP tool runs (seeds -> version-pinned walk ->
    fused evidence chain -> render), so the panel, the evaluation harness
    and the Agent see byte-identical cards. Honest-degradation contract
    (T-09) is preserved at this boundary: no evidence means the
    deterministic INSUFFICIENT_EVIDENCE card with zero LLM calls, never
    a rendered guess; a graph failure is a 5xx, not a fake refusal.

    The response is the card payload (DecisionCardContract shape) plus
    ``persisted``/``card_id`` - generation always writes decision_card_t
    (needs_rerun marks the refusals for the rerun ledger).
    """
    _, tenant_id, _ = _require_workbench_context(authorization)
    svc = _decision_service(tenant_id)
    t0 = time.monotonic()

    # Evidence collection: a store failure must not masquerade as "no
    # knowledge exists" - that distinction is the whole point of the
    # refusal discipline, so a broken store answers 502 instead of a card.
    try:
        lookup = svc.store.entity_lookup
        hits = await lookup(tenant_id, request.question, DECISION_SEED_TOP_K)
        result = await svc.multi_hop(
            request.question, seeds=[h.stable_id for h in hits],
            version=request.ontology_version)
        chain = await svc.assemble_evidence(result)
    except Exception as exc:
        logger.warning("decision card evidence collection failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="knowledge graph query failed") from exc

    if chain.has_evidence() and svc.llm is None:
        raise HTTPException(
            status_code=503,
            detail="decision card rendering requires a configured LLM")
    try:
        card = await svc.render_card(request.question, chain,
                                     mode=request.mode, clock=result.clock)
    except Exception as exc:
        from services.knowevo.llm_client import LLMConfigurationError
        if isinstance(exc, LLMConfigurationError):
            raise HTTPException(
                status_code=503,
                detail="no LLM configured for decision card rendering"
            ) from exc
        logger.warning("decision card render failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="decision card rendering failed") from exc

    card.elapsed_ms = int((time.monotonic() - t0) * 1000)
    card_id = None
    persisted = False
    try:
        card_id = await svc.persist(card)
        persisted = True
    except Exception as exc:  # noqa: BLE001 - persist failure is surfaced
        logger.warning("decision card persist failed: %s", exc)
    payload = card.to_payload()
    payload["persisted"] = persisted
    if card_id is not None:
        payload["card_id"] = str(card_id)
    return payload


def _skill_template_service(tenant_id: str):
    """Read-only SkillTemplateService for the list route (T-20 wiring).

    Same lazy-import discipline as _decision_service: the service opens
    a DB session pool on first use, which must not happen at app-import
    time. No LLM is bound - listing never needs one.
    """
    from services.knowevo.skill_template_service import SkillTemplateService

    return SkillTemplateService(tenant_id=tenant_id)


@router.get("/skill-template/list")
async def list_skill_templates(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    authorization: str | None = Header(None),
):
    """Read-only skill-template listing for the /skillTemplate panel.

    Closes T-20's pending-wiring item: the integration round shipped the
    frontend against exactly this contract but could not add the route
    itself (the T-20 brief authorized no HTTP surface and this file was
    T-19's territory then). Read-only by design - skill_template_t is
    only mutated by the mining pipeline and the apply path, never from
    this boundary. A store failure is a 502, not a fake empty list.
    """
    _, tenant_id, _ = _require_workbench_context(authorization)
    svc = _skill_template_service(tenant_id)
    try:
        rows = await svc.list_templates(limit=limit)
    except Exception as exc:
        logger.warning("skill template listing failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="skill template store unavailable") from exc
    return {"templates": rows, "count": len(rows)}
