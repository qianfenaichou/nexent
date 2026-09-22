"""
Unit tests for mcp_servers/knowevo_mcp (T-07b): kg_search + kg_stats.

Layer 1 (always runs): schema guardrails (hop > 2, top_k > 20 rejected by
pydantic), handler behaviour against a fake GraphStore (empty graph, found
entities + edges, stats scopes), and the structured output shape
(used_tokens / elapsed_ms present). No database and no network: the store
is a fake mirroring the GraphStore contract from T-07a.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): the FastMCP tool registration wiring
against the real PgJsonbGraphStore - configure() + handler round-trip.
"""
import os
import sys
import uuid as uuid_mod
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from pydantic import ValidationError

from mcp_servers.knowevo_mcp.schemas import (
    KGSearchInput,
    KGSearchOutput,
    KGStatsInput,
    KGStatsOutput,
)
from mcp_servers.knowevo_mcp.server import (
    kg_search_handler,
    kg_stats_handler,
)

TENANT_A = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# Fake GraphStore (mirrors the T-07a contract)
# ---------------------------------------------------------------------------

class FakeGraphStore:
    """In-memory GraphStore for handler tests (no DB)."""

    def __init__(self):
        self.entities: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.tenant = TENANT_A

    async def entity_lookup(self, tenant_id, query, top_k=5):
        return [SimpleNamespace(**e) for e in self.entities.values()
                if tenant_id == self.tenant
                and query in e["name"]][:top_k]

    async def neighbors(self, tenant_id, entity_ids, rel_types=None,
                        hop=1, valid_view=True):
        if not entity_ids:
            return SimpleNamespace(entities=[], edges=[])
        ids = set(entity_ids)
        es = [e for e in self.entities.values() if e["stable_id"] in ids]
        cards = [SimpleNamespace(
            stable_id=e["stable_id"], name=e["name"], class_ref=e["class_ref"],
            props=dict(e.get("props") or {}), aliases=list(e.get("aliases") or []))
            for e in es]
        edges = [SimpleNamespace(
            id=e["id"], src=e["src"], dst=e["dst"], rel_type=e["rel_type"],
            claim=e["claim"], props=dict(e.get("props") or {}),
            contested=e.get("contested", False))
            for e in self.edges
            if (e["src"] in ids or e["dst"] in ids)]
        return SimpleNamespace(entities=cards, edges=edges)

    async def stats(self, tenant_id, scope):
        return {"tenant_id": tenant_id, "scope": scope,
                "entities": len(self.entities),
                "edges_total": len(self.edges),
                "edges_valid": len(self.edges),
                "pending": 3 if scope == "full" else None}


def _store_with_graph():
    store = FakeGraphStore()
    store.entities = {
        "Drug:a": {"stable_id": "Drug:a", "name": "二甲双胍",
                   "class_ref": "Drug", "props": {}, "aliases": []},
        "Disease:b": {"stable_id": "Disease:b", "name": "2型糖尿病",
                      "class_ref": "Disease", "props": {}, "aliases": []},
    }
    store.edges = [
        {"id": "e1", "src": "Drug:a", "dst": "Disease:b",
         "rel_type": "indicated_for", "claim": "一线用药", "props": {},
         "contested": False},
    ]
    return store


# ---------------------------------------------------------------------------
# Layer 1: schema guardrails
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_kg_search_input_happy(self):
        p = KGSearchInput(query="二甲双胍", hop=2, top_k=10)
        assert p.hop == 2 and p.top_k == 10 and p.ontology_version is None

    def test_kg_search_hop_guardrail(self):
        with pytest.raises(ValidationError):
            KGSearchInput(query="x", hop=3)  # SPEC: hard cap at 2

    def test_kg_search_topk_guardrail(self):
        with pytest.raises(ValidationError):
            KGSearchInput(query="x", top_k=21)  # SPEC: hard cap at 20

    def test_kg_search_empty_query_rejected(self):
        with pytest.raises(ValidationError):
            KGSearchInput(query="")

    def test_kg_stats_scope_pattern(self):
        assert KGStatsInput().scope == "graph"
        with pytest.raises(ValidationError):
            KGStatsInput(scope="everything")


# ---------------------------------------------------------------------------
# Layer 1: handlers against the fake store
# ---------------------------------------------------------------------------

class TestKgSearchHandler:
    @pytest.mark.asyncio
    async def test_no_hits_returns_empty_shape(self):
        out = await kg_search_handler(
            KGSearchInput(query="不存在"), store=FakeGraphStore(),
            tenant_id=TENANT_A)
        assert isinstance(out, KGSearchOutput)
        assert out.entities == [] and out.edges == []
        assert isinstance(out.valid_view, datetime)
        assert out.elapsed_ms >= 0 and out.used_tokens == 0

    @pytest.mark.asyncio
    async def test_hits_include_neighborhood(self):
        store = _store_with_graph()
        out = await kg_search_handler(
            KGSearchInput(query="二甲双胍", hop=1), store=store,
            tenant_id=TENANT_A)
        names = {e.name for e in out.entities}
        assert "二甲双胍" in names
        assert len(out.edges) == 1
        assert out.edges[0].claim == "一线用药"

    @pytest.mark.asyncio
    async def test_output_shape_is_jsonable(self):
        store = _store_with_graph()
        out = await kg_search_handler(
            KGSearchInput(query="二甲双胍"), store=store, tenant_id=TENANT_A)
        data = out.model_dump(mode="json")
        assert "valid_view" in data
        assert isinstance(data["entities"], list)
        assert isinstance(data["elapsed_ms"], int)


class TestKgStatsHandler:
    @pytest.mark.asyncio
    async def test_stats_graph_scope(self):
        out = await kg_stats_handler(
            KGStatsInput(scope="graph"), store=_store_with_graph(),
            tenant_id=TENANT_A)
        assert isinstance(out, KGStatsOutput)
        assert out.entities == 2 and out.edges_total == 1
        assert out.pending is None

    @pytest.mark.asyncio
    async def test_stats_full_scope_includes_pending(self):
        out = await kg_stats_handler(
            KGStatsInput(scope="full"), store=_store_with_graph(),
            tenant_id=TENANT_A)
        assert out.pending == 3


class BrokenStore:
    """Store whose every call raises, to exercise the error boundary."""

    async def entity_lookup(self, *a, **k):
        raise RuntimeError("db down")

    async def neighbors(self, *a, **k):
        raise RuntimeError("db down")

    async def stats(self, *a, **k):
        raise RuntimeError("db down")


class TestStructuredErrors:
    """SPEC discipline 4: errors come back as {error_code, hint}, never as
    an exception into the MCP runtime."""

    @pytest.mark.asyncio
    async def test_kg_search_store_failure_is_structured(self):
        out = await kg_search_handler(
            KGSearchInput(query="x"), store=BrokenStore(),
            tenant_id=TENANT_A)
        assert isinstance(out, dict)
        assert out["error_code"] == "kg_search_failed"
        assert out.get("hint")

    @pytest.mark.asyncio
    async def test_kg_stats_store_failure_is_structured(self):
        out = await kg_stats_handler(
            KGStatsInput(), store=BrokenStore(), tenant_id=TENANT_A)
        assert isinstance(out, dict)
        assert out["error_code"] == "kg_stats_failed"
        assert "hint" in out


# ---------------------------------------------------------------------------
# T-27: request-scoped tenant resolution
# ---------------------------------------------------------------------------

class RecordingStore(FakeGraphStore):
    """FakeGraphStore that records the tenant every lookup arrived with."""

    def __init__(self):
        super().__init__()
        self.seen_tenants: list[str] = []

    async def entity_lookup(self, tenant_id, query, top_k=5):
        self.seen_tenants.append(tenant_id)
        return await super().entity_lookup(tenant_id, query, top_k)


def _fake_request(authorization: str | None):
    headers = {} if authorization is None else {"Authorization": authorization}
    return SimpleNamespace(headers=headers)


def _raise_no_context():
    raise RuntimeError("no request context available")


@pytest.fixture
def srv():
    """The server module, with its settings restored after the test."""
    from mcp_servers.knowevo_mcp import server as server_mod
    yield server_mod
    server_mod.configure(tenant_id="", store=None)


class TestRequestScopedTenant:
    """The shared MCP service mounts this app once for every tenant, so the
    tenant must come from the caller's Authorization header (T-27)."""

    def test_explicit_arg_wins(self, monkeypatch, srv):
        monkeypatch.setattr(srv, "_request_tenant", lambda: "req-tenant")
        assert srv._tenant("explicit-tenant") == "explicit-tenant"

    def test_request_header_overrides_server_default(self, monkeypatch, srv):
        srv.configure(tenant_id="server-default")
        monkeypatch.setattr("fastmcp.server.dependencies.get_http_request",
                            lambda: _fake_request("Bearer token"))
        monkeypatch.setattr("utils.auth_utils.get_current_user_id",
                            lambda authorization=None: ("user-1", TENANT_A))
        assert srv._request_tenant() == TENANT_A
        assert srv._tenant() == TENANT_A

    def test_no_http_context_falls_back_to_server_default(
            self, monkeypatch, srv):
        srv.configure(tenant_id="server-default")
        monkeypatch.setattr("fastmcp.server.dependencies.get_http_request",
                            _raise_no_context)
        assert srv._request_tenant() == ""
        assert srv._tenant() == "server-default"

    def test_auth_failure_degrades_to_server_default(self, monkeypatch, srv):
        srv.configure(tenant_id="server-default")
        monkeypatch.setattr("fastmcp.server.dependencies.get_http_request",
                            lambda: _fake_request("Bearer expired"))

        def _raise(*_a, **_k):
            raise RuntimeError("token expired")

        monkeypatch.setattr("utils.auth_utils.get_current_user_id", _raise)
        assert srv._request_tenant() == ""
        assert srv._tenant() == "server-default"

    def test_missing_authorization_header_is_empty(self, monkeypatch, srv):
        monkeypatch.setattr("fastmcp.server.dependencies.get_http_request",
                            lambda: _fake_request(None))
        assert srv._request_tenant() == ""

    @pytest.mark.asyncio
    async def test_kg_search_passes_request_tenant_to_store(
            self, monkeypatch, srv):
        store = RecordingStore()
        store.entities = _store_with_graph().entities
        monkeypatch.setattr(srv, "_request_tenant", lambda: TENANT_A)
        out = await kg_search_handler(KGSearchInput(query="二甲双胍"),
                                      store=store)
        assert isinstance(out, KGSearchOutput)
        assert store.seen_tenants == [TENANT_A]

    @pytest.mark.asyncio
    async def test_explicit_tenant_still_skips_request_lookup(
            self, monkeypatch, srv):
        store = RecordingStore()
        store.entities = _store_with_graph().entities
        monkeypatch.setattr("fastmcp.server.dependencies.get_http_request",
                            _raise_no_context)
        out = await kg_search_handler(KGSearchInput(query="二甲双胍"),
                                      store=store, tenant_id=TENANT_A)
        assert isinstance(out, KGSearchOutput)
        assert store.seen_tenants == [TENANT_A]


# ---------------------------------------------------------------------------
# Layer 2: real-PG round-trip (RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

pg_gate = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION", "0") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run real-Postgres tests")


@pg_gate
@pytest.mark.asyncio
class TestPgMcp:
    async def test_kg_search_on_real_graph(self):
        from services.knowevo.graph_store import PgJsonbGraphStore
        store = PgJsonbGraphStore()
        tenant = str(uuid_mod.uuid4())
        await store.upsert_entities(tenant, [
            {"stable_id": "Drug:met", "name": "二甲双胍",
             "class_ref": "Drug"},
            {"stable_id": "Disease:t2d", "name": "2型糖尿病",
             "class_ref": "Disease"},
        ])
        await store.upsert_relations(tenant, [
            {"src": "Drug:met", "dst": "Disease:t2d",
             "rel_type": "indicated_for", "claim": "一线用药"},
        ])
        out = await kg_search_handler(
            KGSearchInput(query="二甲双胍", hop=1), store=store,
            tenant_id=tenant)
        assert {e.name for e in out.entities} == {"二甲双胍", "2型糖尿病"}
        assert len(out.edges) == 1
        stats = await kg_stats_handler(
            KGStatsInput(scope="graph"), store=store, tenant_id=tenant)
        assert stats.entities == 2 and stats.edges_total == 1
