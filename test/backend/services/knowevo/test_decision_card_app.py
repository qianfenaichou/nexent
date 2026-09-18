"""
Tests for the T-19 decision-card production surfaces: the HTTP route
(POST /api/knowevo/decision/card in apps/knowledge_graph_app.py) and the
MCP tool decision_card_render (mcp_servers/knowevo_mcp, dual-registered
through tool_collection/mcp/kg_tools.py).

Layer 1 (always runs): input guardrails, dual registration with a single
schema source, the card pipeline through both surfaces with a fake store
plus a fake LLM, the INSUFFICIENT_EVIDENCE refusal with zero LLM calls
(T-09 honest-degradation contract must survive exposure), the honest
error mapping (a broken store is an error, not a fake refusal), tenant
from session only, the 403 gate, and the persist seam.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): the HTTP route persisting to the
real decision_card_t and reading the row back.

No HTTP server: endpoints are called as plain async functions (same
style as test_knowledge_graph_app.py).
"""
import asyncio
import os
import sys
import uuid as uuid_mod
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

# Upstream convention (test/backend/services/knowevo/test_ontology_service.py):
# backend root + repo root on sys.path; never add __init__.py under the test tree.
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException

from apps import knowledge_graph_app
from services.knowevo.decision_service import DecisionService
from services.knowevo.graph_store import EdgeCard, EntityCard, Subgraph
from services.knowevo.schemas import (
    DECISION_INSUFFICIENT,
    DECISION_RECOMMEND,
)

ADMIN = "admin@knowevo.com"
TENANT_A = "11111111-1111-1111-1111-111111111111"
T_V = datetime(2025, 1, 1, tzinfo=UTC)
BEFORE = datetime(2024, 1, 1, tzinfo=UTC)
VERSION_ROWS = [{"version": "v1.0.0", "created_at": T_V}]

CARD_JSON = """{
  "candidates": [{
    "option": "首选 SGLT2i（恩格列净）",
    "score": 0.82, "confidence_calibrated": 0.9,
    "evidence_chain": [{
      "claim": "eGFR 45 时 SGLT2i 仍可起始",
      "provenance": {"doc": "指南2024版", "span": "§9.2 用药",
                     "kg_path": ["Drug:sglt2i -> CKD_G3a"],
                     "version_pinned": true},
      "tag": "EXTRACTED", "source_channel": "kg+doc"}],
    "risks": ["eGFR 持续下降至<30 需停用"],
    "counterfactual": {"not_choose": "若选二甲双胍常规剂量：风险↑",
                       "tag": "INFERRED"}}],
  "decision": "RECOMMEND",
  "conflict_adjudications": [{"conflict_id": "c1", "type": "guideline_vs_label",
                              "resolution": "以指南为准并标注"}],
  "uncertainty_notes": ["说明书剂量与指南表述存在差异"]
}"""


class ScriptedCardLLM:
    """Async callable matching the frozen llm contract, dispatching on kind.

    The card pipeline touches two kinds (hop_plan before the walk,
    decision_card for the render); routing every kind through one
    callable is what lets the test assert exactly which kinds ran - and
    that the refusal path runs none.
    """

    def __init__(self, card_reply: str = CARD_JSON):
        self.card_reply = card_reply
        self.calls: list[str] = []

    async def __call__(self, prompt, *, kind, tier="mid", temperature=0.0):
        self.calls.append(kind)
        if kind == "decision_card":
            return self.card_reply
        if kind == "hop_plan":
            return '{"hops": [{"rel_types": ["treats"]}]}'
        if kind == "route_llm":
            return '{"route": "RM", "confidence": 0.9, "reason": "test"}'
        return "{}"


class FakeStore:
    """One entity + one in-version edge; honours as_of like the PG store.

    ``entity_lookup`` matches the question substring against the seeded
    name, mirroring PgJsonbGraphStore's ilike path closely enough for
    the seed step under test.
    """

    def __init__(self, name: str = "SGLT2抑制剂", stable_id: str = "Drug:sglt2i",
                 empty: bool = False):
        self.empty = empty
        self.entity = EntityCard(stable_id=stable_id, name=name,
                                 class_ref="Drug")
        self.edge = EdgeCard(id="e1", src=stable_id, dst="Disease:ckd",
                             rel_type="treats", claim="SGLT2抑制剂 治疗 CKD",
                             props={"evidence_id": "ev1"},
                             valid_at=BEFORE, invalid_at=None)
        self.lookup_calls: list[str] = []

    async def entity_lookup(self, tenant_id, query, top_k=5):
        self.lookup_calls.append(query)
        if self.empty:
            return []
        return [self.entity]

    async def neighbors(self, tenant_id, entity_ids, rel_types=None,
                        hop=1, valid_view=True, as_of=None):
        if self.empty:
            return Subgraph(entities=[], edges=[])
        frontier = list(entity_ids)
        if self.edge.src not in frontier and self.edge.dst not in frontier:
            return Subgraph(entities=[], edges=[])
        if valid_view and as_of is not None and self.edge.valid_at > as_of:
            return Subgraph(entities=[], edges=[])
        return Subgraph(entities=[], edges=[self.edge])


class BoomStore:
    """A store that is down - every touch raises."""

    async def entity_lookup(self, tenant_id, query, top_k=5):
        raise RuntimeError("db down")

    async def neighbors(self, *a, **k):
        raise RuntimeError("db down")


def _recording_persist(svc):
    """Replace the instance persist with a recorder (no DB in layer 1)."""
    saved = []

    async def fake_persist(card, session_id=None):
        saved.append(card)
        return "row-t19-1"

    svc.persist = fake_persist
    return saved


def _auth_as(monkeypatch, user_id=ADMIN, tenant_id=TENANT_A, role="ADMIN",
             kb_manage=True, graph_manage=True):
    """Patch the two seams the app layer touches: session context, RBAC."""
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


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# MCP schema: input guardrails + single schema source
# ---------------------------------------------------------------------------


class TestDecisionCardInputGuardrails:
    def test_empty_question_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        with pytest.raises(ValidationError):
            DecisionCardInput(question="")

    def test_question_above_500_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        with pytest.raises(ValidationError):
            DecisionCardInput(question="q" * 501)

    def test_unknown_mode_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        with pytest.raises(ValidationError):
            DecisionCardInput(question="q", mode="quick")

    def test_defaults_are_full_mode_unpinned(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        payload = DecisionCardInput(question="q")
        assert payload.mode == "full" and payload.ontology_version is None

    def test_schema_advertises_the_bounds(self):
        # An Agent reads the advertised schema, not the code (SPEC 3).
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        schema = DecisionCardInput.model_json_schema()
        assert schema["properties"]["question"]["maxLength"] == 500
        assert schema["properties"]["mode"]["pattern"] == "^(full|lite)$"


class TestDualRegistration:
    def test_tool_names_include_decision_card_render(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES
        assert "decision_card_render" in KG_MCP_TOOL_NAMES

    def test_handlers_map_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, handlers
        assert set(handlers()) == set(KG_MCP_TOOL_NAMES)

    def test_schema_manifest_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, tool_schemas
        assert set(tool_schemas()) == set(KG_MCP_TOOL_NAMES)

    def test_local_and_standalone_share_one_schema_object(self):
        # Single schema source (SPEC discipline 1 / pitfall #27).
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput as std

        from tool_collection.mcp.kg_tools import DecisionCardInput as local
        assert std is local

    def test_local_and_standalone_share_one_handler(self):
        from mcp_servers.knowevo_mcp.server import (
            decision_card_render_handler as std,
        )

        from tool_collection.mcp.kg_tools import handlers
        assert handlers()["decision_card_render"] is std

    def test_standalone_app_registers_the_tool(self):
        from mcp_servers.knowevo_mcp.server import mcp
        names = {t.name for t in mcp._tool_manager._tools.values()}
        assert "decision_card_render" in names


# ---------------------------------------------------------------------------
# MCP handler: the card pipeline
# ---------------------------------------------------------------------------


class TestDecisionCardMCPHandler:
    @pytest.mark.asyncio
    async def test_insufficient_evidence_refuses_without_llm(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        from mcp_servers.knowevo_mcp.server import (
            decision_card_render_handler,
        )

        llm = ScriptedCardLLM()
        svc = DecisionService(store=FakeStore(empty=True), tenant_id=TENANT_A,
                              llm=llm)
        saved = _recording_persist(svc)
        out = await decision_card_render_handler(
            DecisionCardInput(question="图里没有的知识？"),
            tenant_id=TENANT_A, service=svc)
        assert isinstance(out, dict)
        assert out["decision"] == DECISION_INSUFFICIENT
        assert out["candidates"] == []
        assert llm.calls == [], (
            "the T-09 refusal contract: no evidence means no LLM call, "
            "the exposure layer must not regress it")
        assert out["disclaimer"], "healthcare cards always carry it"
        assert len(saved) == 1 and out["persisted"] is True
        assert out["card_id"] == "row-t19-1"

    @pytest.mark.asyncio
    async def test_full_card_with_pinned_provenance(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        from mcp_servers.knowevo_mcp.server import (
            decision_card_render_handler,
        )

        llm = ScriptedCardLLM()
        store = FakeStore()
        svc = DecisionService(store=store, tenant_id=TENANT_A, llm=llm,
                              version_rows=VERSION_ROWS)
        _recording_persist(svc)
        out = await decision_card_render_handler(
            DecisionCardInput(question="SGLT2抑制剂",
                              ontology_version="v1.0.0"),
            tenant_id=TENANT_A, service=svc)
        assert out["decision"] == DECISION_RECOMMEND
        cand = out["candidates"][0]
        prov = cand["evidence_chain"][0]["provenance"]
        assert prov["version_pinned"] is True
        assert out["knowledge_stamp"]["ontology_version"] == "v1.0.0"
        assert out["knowledge_stamp"]["kg_cutoff"]
        assert out["conflict_adjudications"][0]["conflict_id"] == "c1"
        assert cand["risks"] and cand["counterfactual"]["not_choose"]
        assert out["disclaimer"]
        assert "elapsed_ms" in out and "used_tokens" in out

    @pytest.mark.asyncio
    async def test_evidence_with_no_llm_is_llm_unavailable_not_a_refusal(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        from mcp_servers.knowevo_mcp.server import (
            decision_card_render_handler,
        )

        svc = DecisionService(store=FakeStore(), tenant_id=TENANT_A, llm=None)
        out = await decision_card_render_handler(
            DecisionCardInput(question="SGLT2抑制剂"),
            tenant_id=TENANT_A, service=svc)
        assert isinstance(out, dict)
        assert out["error_code"] == "llm_unavailable"
        assert "INSUFFICIENT" not in str(out), (
            "a store-down or llm-less run must never be dressed up as the "
            "honest refusal")

    @pytest.mark.asyncio
    async def test_store_failure_is_structured_error(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        from mcp_servers.knowevo_mcp.server import (
            decision_card_render_handler,
        )

        svc = DecisionService(store=BoomStore(), tenant_id=TENANT_A)
        out = await decision_card_render_handler(
            DecisionCardInput(question="SGLT2抑制剂"),
            tenant_id=TENANT_A, service=svc)
        assert out["error_code"] == "decision_card_render_failed"
        assert out["hint"]
        assert "db down" not in out["hint"], (
            "structured errors carry the type, not the internal message")

    @pytest.mark.asyncio
    async def test_lite_mode_flows_through(self):
        from mcp_servers.knowevo_mcp.schemas import DecisionCardInput
        from mcp_servers.knowevo_mcp.server import (
            decision_card_render_handler,
        )

        llm = ScriptedCardLLM()
        svc = DecisionService(store=FakeStore(), tenant_id=TENANT_A, llm=llm)
        _recording_persist(svc)
        out = await decision_card_render_handler(
            DecisionCardInput(question="SGLT2抑制剂", mode="lite"),
            store=FakeStore(), tenant_id=TENANT_A, service=svc)
        assert out["decision"] == DECISION_RECOMMEND
        assert any("lite" in n for n in out["uncertainty_notes"])


# ---------------------------------------------------------------------------
# HTTP route: POST /knowevo/decision/card
# ---------------------------------------------------------------------------


class TestDecisionCardHTTPRoute:
    def test_route_registered_on_router(self):
        matches = [r for r in knowledge_graph_app.router.routes
                   if getattr(r, "path", "") == "/knowevo/decision/card"]
        assert matches and "POST" in matches[0].methods

    def test_no_workbench_permission_maps_to_403(self, monkeypatch):
        _auth_as(monkeypatch, kb_manage=False, graph_manage=False)
        req = knowledge_graph_app.DecisionCardRequest(question="q")
        with pytest.raises(HTTPException) as denied:
            _run(knowledge_graph_app.render_decision_card(
                req, authorization="Bearer t"))
        assert denied.value.status_code == 403

    def test_insufficient_evidence_without_llm(self, monkeypatch):
        _auth_as(monkeypatch)
        llm = ScriptedCardLLM()
        svc = DecisionService(store=FakeStore(empty=True), tenant_id=TENANT_A,
                              llm=llm)
        saved = _recording_persist(svc)
        monkeypatch.setattr(knowledge_graph_app, "_decision_service",
                            lambda tenant_id: svc)
        req = knowledge_graph_app.DecisionCardRequest(question="图里没有的知识？")
        resp = _run(knowledge_graph_app.render_decision_card(
            req, authorization="Bearer t"))
        assert resp["decision"] == DECISION_INSUFFICIENT
        assert resp["candidates"] == []
        assert llm.calls == []
        assert resp["persisted"] is True and saved

    def test_tenant_comes_from_session_not_body(self, monkeypatch):
        _auth_as(monkeypatch, tenant_id=TENANT_A)
        seen: list[str] = []
        svc = DecisionService(store=FakeStore(empty=True),
                              tenant_id=TENANT_A, llm=ScriptedCardLLM())
        _recording_persist(svc)

        def fake_factory(tenant_id: str):
            seen.append(tenant_id)
            return svc

        monkeypatch.setattr(knowledge_graph_app, "_decision_service",
                            fake_factory)
        # The body carries a tenant_id-shaped extra key: pydantic's
        # extra="ignore" drops it, and the service must be built with the
        # session tenant only - there is no body seam for tenant at all.
        req = knowledge_graph_app.DecisionCardRequest.model_validate({
            "question": "图里没有的知识？",
            "tenant_id": "22222222-2222-2222-2222-222222222222"})
        _run(knowledge_graph_app.render_decision_card(
            req, authorization="Bearer t"))
        assert seen == [TENANT_A]

    def test_full_card_payload_contract_fields(self, monkeypatch):
        _auth_as(monkeypatch)
        llm = ScriptedCardLLM()
        svc = DecisionService(store=FakeStore(), tenant_id=TENANT_A, llm=llm,
                              version_rows=VERSION_ROWS)
        _recording_persist(svc)
        monkeypatch.setattr(knowledge_graph_app, "_decision_service",
                            lambda tenant_id: svc)
        req = knowledge_graph_app.DecisionCardRequest(
            question="SGLT2抑制剂", ontology_version="v1.0.0")
        resp = _run(knowledge_graph_app.render_decision_card(
            req, authorization="Bearer t"))
        cand = resp["candidates"][0]
        prov = cand["evidence_chain"][0]["provenance"]
        for field in ("risks", "counterfactual", "decision",
                      "knowledge_stamp", "conflict_adjudications",
                      "disclaimer", "question_id"):
            assert field in resp or field in cand, field
        for field in ("doc", "span", "kg_path", "version_pinned"):
            assert field in prov, field
        assert prov["version_pinned"] is True
        assert resp["knowledge_stamp"]["ontology_version"] == "v1.0.0"
        assert resp["persisted"] is True

    def test_store_failure_maps_to_502_not_a_refusal(self, monkeypatch):
        _auth_as(monkeypatch)
        svc = DecisionService(store=BoomStore(), tenant_id=TENANT_A,
                              llm=ScriptedCardLLM())
        monkeypatch.setattr(knowledge_graph_app, "_decision_service",
                            lambda tenant_id: svc)
        req = knowledge_graph_app.DecisionCardRequest(question="SGLT2抑制剂")
        with pytest.raises(HTTPException) as err:
            _run(knowledge_graph_app.render_decision_card(
                req, authorization="Bearer t"))
        assert err.value.status_code == 502
        assert "db down" not in err.value.detail

    def test_evidence_without_llm_maps_to_503(self, monkeypatch):
        _auth_as(monkeypatch)
        svc = DecisionService(store=FakeStore(), tenant_id=TENANT_A, llm=None)
        monkeypatch.setattr(knowledge_graph_app, "_decision_service",
                            lambda tenant_id: svc)
        req = knowledge_graph_app.DecisionCardRequest(question="SGLT2抑制剂")
        with pytest.raises(HTTPException) as err:
            _run(knowledge_graph_app.render_decision_card(
                req, authorization="Bearer t"))
        assert err.value.status_code == 503

    def test_bad_mode_maps_to_422(self, monkeypatch):
        _auth_as(monkeypatch)
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            knowledge_graph_app.DecisionCardRequest(question="q",
                                                    mode="quick")


# ---------------------------------------------------------------------------
# Layer 2: the route persists to the real decision_card_t
# ---------------------------------------------------------------------------


class TestPostgresDecisionCardPersistence:
    @pytest.mark.asyncio
    @pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
        reason="set RUN_POSTGRES_INTEGRATION=1 with a reachable PG",
    )
    async def test_http_route_persists_card_to_pg(self, monkeypatch):
        from database.knowevo_db import DecisionCard as DecisionCardRow
        from database.knowevo_db import KgEntity, KgRelation, _get_db_session
        from services.knowevo.graph_store import PgJsonbGraphStore

        def _clean():
            with _get_db_session() as session:
                session.query(DecisionCardRow).filter(
                    DecisionCardRow.tenant_id == TENANT_A).delete(
                    synchronize_session=False)
                session.query(KgRelation).filter(
                    KgRelation.tenant_id == TENANT_A).delete(
                    synchronize_session=False)
                session.query(KgEntity).filter(
                    KgEntity.tenant_id == TENANT_A).delete(
                    synchronize_session=False)

        _clean()
        try:
            store = PgJsonbGraphStore()
            await store.upsert_entities(TENANT_A, [{
                "stable_id": "Drug:sglt2i", "name": "SGLT2抑制剂",
                "class_ref": "Drug", "props": {"evidence_id": "ev-t19"}}])
            await store.upsert_relations(TENANT_A, [{
                "src": "Drug:sglt2i", "dst": "Disease:ckd",
                "rel_type": "treats", "claim": "SGLT2抑制剂 治疗 CKD",
                "props": {"evidence_id": "ev-t19"}}])

            svc = DecisionService(store=store, tenant_id=TENANT_A,
                                  llm=ScriptedCardLLM())
            _auth_as(monkeypatch)
            monkeypatch.setattr(knowledge_graph_app, "_decision_service",
                                lambda tenant_id: svc)
            req = knowledge_graph_app.DecisionCardRequest(question="SGLT2抑制剂")
            # Already inside the pytest-asyncio loop: await the endpoint
            # coroutine directly instead of the sync _run() helper.
            resp = await knowledge_graph_app.render_decision_card(
                req, authorization="Bearer t")
            assert resp["persisted"] is True
            assert resp["decision"] == DECISION_RECOMMEND

            with _get_db_session() as session:
                row = session.query(DecisionCardRow).filter(
                    DecisionCardRow.id == uuid_mod.UUID(
                        resp["card_id"])).first()
                assert row is not None, (
                    "the rendered card must land in the table")
                # Read the JSONB payload while the row is still attached.
                payload = dict(row.payload or {})
                needs_rerun = bool(row.needs_rerun)
            assert payload["question"] == "SGLT2抑制剂"
            assert payload["knowledge_stamp"]["ontology_version"] in (
                None, "v1.0.0")
            assert needs_rerun is False, (
                "a RECOMMEND card is not queued for the rerun ledger")
        finally:
            _clean()
