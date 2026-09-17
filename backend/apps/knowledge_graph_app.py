"""HTTP endpoints for the KnowEvo ontology workbench (T-05a).

Layer rule (knowevo/backend/apps/knowledge_graph_app.py.md): this module
only parses input, checks tenant RBAC, and delegates to
services.knowevo.ontology_service - zero business logic here. tenant_id
always comes from the session via utils.auth_utils, never from the
request body, so the service layer stays free of request context.
"""
import logging
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


def _ontology_service() -> OntologyService:
    """One service instance per request; PgStore wiring stays lazy so the
    app imports cleanly in unit tests without a live database client."""
    return OntologyService(store=None)


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
