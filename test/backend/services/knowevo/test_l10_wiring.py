"""L10 wiring routes in apps/knowledge_graph_app.py (W5 evolution + W10 apply).

Layer 1 (always runs): endpoint behavior with the auth seam and the service
seam monkeypatched - no database, no HTTP server. Endpoints are called as
plain async functions (same style as test_knowledge_graph_app.py).

P0 (verify report #1): round_detail has no tenant filter in the store, so
the HTTP route must refuse another tenant's round (403 by default).
W10 body field is ``name`` (frontend), not the MCP tool's ``template_name``.
"""
import asyncio
import sys
from pathlib import Path

import pytest

# Upstream convention: backend root on sys.path; never add __init__.py
# under the test tree.
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException

ADMIN = "admin@knowevo.com"
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def _run(coro):
    return asyncio.run(coro)


def _auth_as(monkeypatch, user_id=ADMIN, tenant_id=TENANT_A, role="ADMIN",
             kb_manage=True, graph_manage=True):
    """Patch the two seams the app layer touches: session context and RBAC."""
    monkeypatch.setattr(
        "apps.knowledge_graph_app.get_current_user_context",
        lambda _authorization: (user_id, tenant_id, role))

    def fake_check(user_role, category, ptype, subtype=None):
        if (category, ptype) == ("RESOURCE", "KNOWLEDGE_GRAPH"):
            return graph_manage
        if (category, ptype) == ("RESOURCE", "KB"):
            return kb_manage
        return False

    monkeypatch.setattr(
        "apps.knowledge_graph_app.check_role_permission", fake_check)


class FakeEvolutionService:
    """Stand-in for EvolutionService.timeline / round_detail."""

    def __init__(self, rounds=None, reports=None, fail=False):
        self.rounds = rounds or []
        self.reports = reports or {}
        self.fail = fail
        self.timeline_calls = []
        self.detail_calls = []

    async def timeline(self, tenant_id, since=None):
        self.timeline_calls.append((tenant_id, since))
        if self.fail:
            raise RuntimeError("store down")
        return self.rounds

    async def round_detail(self, round_id):
        self.detail_calls.append(round_id)
        if self.fail:
            raise RuntimeError("store down")
        if round_id not in self.reports:
            raise KeyError(f"unknown round {round_id}")
        return self.reports[round_id]


class FakeSkillTemplateService:
    """Stand-in for SkillTemplateService.apply_template."""

    def __init__(self, result=None, missing=False, fail=False,
                 outcome_calls=None):
        self.result = result or {
            "name": "demo",
            "skill_md": "# demo",
            "variables": {},
            "reuse_count": 3,
        }
        self.missing = missing
        self.fail = fail
        self.outcome_calls = outcome_calls if outcome_calls is not None else []
        self.apply_calls = []

    async def apply_template(self, name, variables=None):
        self.apply_calls.append((name, variables))
        if self.fail:
            raise RuntimeError("store down")
        if self.missing:
            raise KeyError(f"template not found: {name}")
        return self.result

    async def record_reuse_outcome(self, *args, **kwargs):
        self.outcome_calls.append((args, kwargs))
        return {}


def _summary(**over):
    """Real RoundSummary dataclass (route uses dataclasses.asdict)."""
    from services.knowevo.evolution_service import RoundSummary

    base = {
        "round_id": "r-1",
        "at": "2026-09-30T00:00:00",
        "trigger_source": "manual",
        "status": "settled",
        "ops_summary": {},
        "eval_delta": None,
    }
    base.update(over)
    return RoundSummary(**base)


def _report(tenant_id=TENANT_A, **over):
    """Real RoundReport dataclass (route uses dataclasses.asdict)."""
    from services.knowevo.evolution_service import RoundReport

    base = {
        "round_id": "r-1",
        "tenant_id": tenant_id,
        "trigger_source": "manual",
        "status": "settled",
        "steps": [],
        "ops_summary": {},
        "cost": {},
        "eval_delta": None,
        "edges_superseded_ids": [],
        "rollback_of": None,
        "created_at": "2026-09-30T00:00:00",
    }
    base.update(over)
    return RoundReport(**base)


# ── W5: evolution timeline ───────────────────────────────────────────


def test_w5_timeline_wraps_rounds_and_count(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    svc = FakeEvolutionService(rounds=[_summary(round_id="r-1"),
                                       _summary(round_id="r-2")])
    monkeypatch.setattr(app, "_evolution_service", lambda _t: svc)

    out = _run(app.list_evolution_rounds(since=None, authorization="Bearer t"))
    assert set(out) == {"rounds", "count"}
    assert out["count"] == 2
    assert out["rounds"][0]["round_id"] == "r-1"
    # session tenant is the only tenant id passed to the service
    assert svc.timeline_calls[0][0] == TENANT_A


def test_w5_timeline_bad_since_is_400(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    svc = FakeEvolutionService()
    monkeypatch.setattr(app, "_evolution_service", lambda _t: svc)
    with pytest.raises(HTTPException) as exc:
        _run(app.list_evolution_rounds(
            since="not-a-date", authorization="Bearer t"))
    assert exc.value.status_code == 400
    assert svc.timeline_calls == []


def test_w5_timeline_store_down_is_502(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    monkeypatch.setattr(
        app, "_evolution_service", lambda _t: FakeEvolutionService(fail=True))
    with pytest.raises(HTTPException) as exc:
        _run(app.list_evolution_rounds(since=None, authorization="Bearer t"))
    assert exc.value.status_code == 502


# ── W5 P0: round_detail tenant gate ──────────────────────────────────


def test_w5_round_detail_same_tenant_ok(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch, tenant_id=TENANT_A)
    svc = FakeEvolutionService(reports={"r-1": _report(tenant_id=TENANT_A)})
    monkeypatch.setattr(app, "_evolution_service", lambda _t: svc)

    out = _run(app.get_evolution_round(round_id="r-1",
                                       authorization="Bearer t"))
    assert out["round_id"] == "r-1"
    assert out["tenant_id"] == TENANT_A


def test_w5_round_detail_cross_tenant_rejected(monkeypatch):
    """P0 (verify report #1): store.get_round filters by id only; the HTTP
    layer must refuse another tenant's round. Workorder default is 403;
    if leadership switches to 404, flip the expected status here too.
    """
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch, tenant_id=TENANT_A)
    svc = FakeEvolutionService(reports={"r-x": _report(tenant_id=TENANT_B)})
    monkeypatch.setattr(app, "_evolution_service", lambda _t: svc)

    with pytest.raises(HTTPException) as exc:
        _run(app.get_evolution_round(round_id="r-x", authorization="Bearer t"))
    assert exc.value.status_code == 403
    assert "tenant" in exc.value.detail.lower()


def test_w5_round_detail_unknown_is_404(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    svc = FakeEvolutionService(reports={})
    monkeypatch.setattr(app, "_evolution_service", lambda _t: svc)

    with pytest.raises(HTTPException) as exc:
        _run(app.get_evolution_round(round_id="nope",
                                     authorization="Bearer t"))
    assert exc.value.status_code == 404


def test_w5_round_detail_store_down_is_502(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    monkeypatch.setattr(
        app, "_evolution_service", lambda _t: FakeEvolutionService(fail=True))
    with pytest.raises(HTTPException) as exc:
        _run(app.get_evolution_round(round_id="r-1", authorization="Bearer t"))
    assert exc.value.status_code == 502


# ── W10: skill-template apply ────────────────────────────────────────


def test_w10_apply_happy_path_returns_server_payload(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    outcome_calls = []
    svc = FakeSkillTemplateService(
        result={"name": "demo", "skill_md": "# demo",
                "variables": {"k": "v"}, "reuse_count": 4},
        outcome_calls=outcome_calls)
    monkeypatch.setattr(app, "_skill_template_service", lambda _t: svc)

    req = app.SkillTemplateApplyRequest(name="demo", variables={"k": "v"})
    out = _run(app.apply_skill_template(
        request=req, authorization="Bearer t"))
    assert out["name"] == "demo"
    assert out["skill_md"] == "# demo"
    assert out["reuse_count"] == 4
    assert svc.apply_calls == [("demo", {"k": "v"})]
    # Workorder: this route must NOT call record_reuse_outcome.
    assert outcome_calls == []


def test_w10_apply_unknown_template_is_404(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    svc = FakeSkillTemplateService(missing=True)
    monkeypatch.setattr(app, "_skill_template_service", lambda _t: svc)

    with pytest.raises(HTTPException) as exc:
        _run(app.apply_skill_template(
            request=app.SkillTemplateApplyRequest(name="ghost", variables={}),
            authorization="Bearer t"))
    assert exc.value.status_code == 404


def test_w10_apply_store_down_is_502(monkeypatch):
    from apps import knowledge_graph_app as app

    _auth_as(monkeypatch)
    monkeypatch.setattr(
        app, "_skill_template_service",
        lambda _t: FakeSkillTemplateService(fail=True))
    with pytest.raises(HTTPException) as exc:
        _run(app.apply_skill_template(
            request=app.SkillTemplateApplyRequest(name="demo", variables={}),
            authorization="Bearer t"))
    assert exc.value.status_code == 502


def test_w10_body_field_is_name_not_template_name():
    """Verify report #2: MCP tool input uses template_name; HTTP uses name
    (frontend contract). Reject a model that silently aliases the MCP field.
    """
    from apps import knowledge_graph_app as app

    model = app.SkillTemplateApplyRequest
    fields = getattr(model, "model_fields", None) or model.__fields__
    assert "name" in fields
    assert "template_name" not in fields
    assert "variables" in fields


def test_w10_empty_name_rejected():
    from apps import knowledge_graph_app as app
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        app.SkillTemplateApplyRequest(name="", variables={})


# ── route-mount guardrail (HTTP surface, not just handlers) ──────────
#
# The handler tests above call the endpoint functions directly, so they
# cannot catch a route that never reached the mounted router. These tests
# probe the real routing tables of both apps that include the knowevo
# router; "not 404" is the mount proof (auth refusal 401/403 or a method
# gate 405 still proves the path exists - only 404 means it does not).

EVOLUTION_ROUTES = [
    ("/api/knowevo/evolution/timeline", "get"),
    ("/api/knowevo/evolution/rounds/r-1", "get"),
]


def _routing_client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path,method", EVOLUTION_ROUTES)
def test_evolution_routes_mounted_in_config_app(path, method):
    from apps.config_app import app

    response = getattr(_routing_client(app), method)(path)
    assert response.status_code != 404, (
        f"{method.upper()} {path} -> 404; the evolution routes are "
        "unmounted (router not included or prefix doubled)")


@pytest.mark.parametrize("path,method", EVOLUTION_ROUTES)
def test_evolution_routes_mounted_in_runtime_app(path, method):
    from apps.runtime_app import app

    response = getattr(_routing_client(app), method)(path)
    assert response.status_code != 404, (
        f"{method.upper()} {path} -> 404; the evolution routes are "
        "unmounted (router not included or prefix doubled)")
