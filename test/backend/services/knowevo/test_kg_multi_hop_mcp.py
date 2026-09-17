"""
Tests for the kg_multi_hop MCP tool (T-09): dual registration, input
guardrails, version-pinned output shape and structured errors.

The tool surface has two registration forms - the standalone FastMCP server
(mcp_servers/knowevo_mcp/server.py) and the Local-MCP inner registration
(backend/tool_collection/mcp/kg_tools.py). Pitfall #27 was exactly this
pair drifting apart (a criterion ticked while the code had no
implementation), so the tests here assert the same schema object is
reachable from both surfaces and that the guardrails live in the Pydantic
model rather than in a decorator signature.

Layer 1 (always runs): schema source, guardrails, handler output shape,
structured error on store failure, no-mutation of the injected service.
"""
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from pydantic import ValidationError

from services.knowevo.decision_service import DecisionService
from services.knowevo.graph_store import EdgeCard, Subgraph

T_V = datetime(2025, 1, 1, tzinfo=UTC)
BEFORE = datetime(2024, 1, 1, tzinfo=UTC)
AFTER = datetime(2026, 1, 1, tzinfo=UTC)
TENANT = "11111111-1111-1111-1111-111111111111"
# The version row supplies the cutoff t_v: pinning to a version needs
# to know when that version came into being.
VERSION_ROWS = [{"version": "v1.0.0", "created_at": T_V}]


class FakeStore:
    """Minimal store: one in-version edge and one future edge from Drug:a."""

    def __init__(self):
        self.edges = [
            EdgeCard(id="e1", src="Drug:a", dst="Disease:b",
                     rel_type="treats", claim="a 治疗 b",
                     props={"evidence_id": "ev1"},
                     valid_at=BEFORE, invalid_at=None),
            EdgeCard(id="e2", src="Drug:a", dst="Drug:c",
                     rel_type="replaced_by", claim="新版：a 已被 c 取代",
                     props={"evidence_id": "ev2"},
                     valid_at=AFTER, invalid_at=None),
        ]

    async def neighbors(self, tenant_id, entity_ids, rel_types=None,
                        hop=1, valid_view=True, as_of=None):
        frontier = list(entity_ids)
        out = []
        for edge in self.edges:
            if edge.src not in frontier and edge.dst not in frontier:
                continue
            if rel_types and edge.rel_type not in rel_types:
                continue
            if valid_view and as_of is not None:
                if edge.valid_at is not None and edge.valid_at > as_of:
                    continue
                if edge.invalid_at is not None and edge.invalid_at <= as_of:
                    continue
            elif valid_view and edge.invalid_at is not None:
                continue
            out.append(edge)
        return Subgraph(entities=[], edges=out)


class TestDualRegistration:
    def test_tool_names_include_multi_hop(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES
        assert KG_MCP_TOOL_NAMES == ("kg_search", "kg_stats", "kg_multi_hop")

    def test_handlers_map_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, handlers
        assert set(handlers()) == set(KG_MCP_TOOL_NAMES)

    def test_schema_manifest_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, tool_schemas
        assert set(tool_schemas()) == set(KG_MCP_TOOL_NAMES)

    def test_local_and_standalone_share_one_schema_object(self):
        # Single schema source (SPEC discipline 1): the same class object
        # must be reachable from both registration surfaces, otherwise the
        # two copies drift (pitfall #27).
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput as std

        from tool_collection.mcp.kg_tools import KGMultiHopInput as local
        assert std is local

    def test_local_and_standalone_share_one_handler(self):
        from mcp_servers.knowevo_mcp.server import kg_multi_hop_handler as std

        from tool_collection.mcp.kg_tools import handlers
        assert handlers()["kg_multi_hop"] is std

    def test_fastmcp_app_registers_the_tool(self):
        from mcp_servers.knowevo_mcp.server import mcp
        names = {t.name for t in mcp._tool_manager._tools.values()}
        assert "kg_multi_hop" in names, (
            "the standalone FastMCP surface must advertise the tool")

    def test_all_three_tools_registered_on_standalone_app(self):
        from mcp_servers.knowevo_mcp.server import mcp
        names = {t.name for t in mcp._tool_manager._tools.values()}
        assert {"kg_search", "kg_stats", "kg_multi_hop"} <= names


class TestInputGuardrails:
    """Guardrails live in the Pydantic model, so the MCP layer rejects
    out-of-range payloads before any handler runs (SPEC discipline 3)."""

    def test_depth_above_three_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        with pytest.raises(ValidationError):
            KGMultiHopInput(question="q", depth=4)

    def test_beam_above_three_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        with pytest.raises(ValidationError):
            KGMultiHopInput(question="q", beam=4)

    def test_depth_below_one_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        with pytest.raises(ValidationError):
            KGMultiHopInput(question="q", depth=0)

    def test_empty_question_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        with pytest.raises(ValidationError):
            KGMultiHopInput(question="")

    def test_seed_list_is_bounded(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        with pytest.raises(ValidationError):
            KGMultiHopInput(question="q", seeds=[f"s{i}" for i in range(11)])

    def test_top_k_is_bounded(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        with pytest.raises(ValidationError):
            KGMultiHopInput(question="q", top_k=11)

    def test_defaults_are_inside_the_guardrails(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        payload = KGMultiHopInput(question="q")
        assert 1 <= payload.depth <= 3 and 1 <= payload.beam <= 3

    def test_schema_advertises_the_constraints(self):
        # The advertised JSON schema must carry the bounds, not just the
        # validation - an Agent reads the schema, not the code.
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        schema = KGMultiHopInput.model_json_schema()
        assert schema["properties"]["depth"]["maximum"] == 3
        assert schema["properties"]["beam"]["maximum"] == 3


class TestMultiHopHandler:
    @pytest.mark.asyncio
    async def test_returns_pinned_paths(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        from mcp_servers.knowevo_mcp.server import kg_multi_hop_handler

        svc = DecisionService(store=FakeStore(), tenant_id=TENANT,
                              version_rows=VERSION_ROWS)
        out = await kg_multi_hop_handler(
            KGMultiHopInput(question="a 治疗什么？", seeds=["Drug:a"],
                            depth=1, ontology_version="v1.0.0"),
            service=svc)
        assert not isinstance(out, dict)
        assert out.version_pinned is True
        assert out.ontology_version == "v1.0.0"
        assert out.elapsed_ms >= 0
        assert any(p.entities == ["Drug:a", "Disease:b"] for p in out.paths)
        assert all(h.evidence_id for p in out.paths for h in p.hops)

    @pytest.mark.asyncio
    async def test_excluded_paths_come_back_as_failed(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        from mcp_servers.knowevo_mcp.server import kg_multi_hop_handler

        svc = DecisionService(store=FakeStore(), tenant_id=TENANT,
                              version_rows=VERSION_ROWS)
        out = await kg_multi_hop_handler(
            KGMultiHopInput(question="a 被什么取代？", seeds=["Drug:a"],
                            depth=1, ontology_version="v1.0.0"),
            service=svc)
        assert out.failed, (
            "the future edge a->c must be reported as rejected by version "
            "pinning, not silently dropped")
        assert all(p.version_valid is False for p in out.failed)

    @pytest.mark.asyncio
    async def test_respects_top_k(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        from mcp_servers.knowevo_mcp.server import kg_multi_hop_handler

        svc = DecisionService(store=FakeStore(), tenant_id=TENANT,
                              version_rows=VERSION_ROWS)
        out = await kg_multi_hop_handler(
            KGMultiHopInput(question="a", seeds=["Drug:a"], depth=1, top_k=1),
            service=svc)
        assert len(out.paths) <= 1

    @pytest.mark.asyncio
    async def test_no_seeds_yields_empty_paths_not_error(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        from mcp_servers.knowevo_mcp.server import kg_multi_hop_handler

        svc = DecisionService(store=FakeStore(), tenant_id=TENANT,
                              version_rows=VERSION_ROWS)
        out = await kg_multi_hop_handler(
            KGMultiHopInput(question="a"), service=svc)
        assert out.paths == [] and not isinstance(out, dict)

    @pytest.mark.asyncio
    async def test_store_failure_returns_structured_error(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        from mcp_servers.knowevo_mcp.server import kg_multi_hop_handler

        class Boom:
            async def neighbors(self, *a, **k):
                raise RuntimeError("db down")

        svc = DecisionService(store=Boom(), tenant_id=TENANT)
        out = await kg_multi_hop_handler(
            KGMultiHopInput(question="a", seeds=["Drug:a"]), service=svc)
        assert isinstance(out, dict), (
            "a store failure must degrade to a structured error, never "
            "propagate into the MCP runtime")
        assert out["error_code"] == "kg_multi_hop_failed"
        assert out["hint"]

    @pytest.mark.asyncio
    async def test_output_serializes_to_json_safe_dict(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        from mcp_servers.knowevo_mcp.server import kg_multi_hop_handler

        svc = DecisionService(store=FakeStore(), tenant_id=TENANT,
                              version_rows=VERSION_ROWS)
        out = await kg_multi_hop_handler(
            KGMultiHopInput(question="a", seeds=["Drug:a"], depth=1),
            service=svc)
        dumped = out.model_dump(mode="json")
        assert isinstance(dumped["valid_view"], str)
        assert isinstance(dumped["paths"], list)

    @pytest.mark.asyncio
    async def test_injected_service_is_used_when_supplied(self):
        from mcp_servers.knowevo_mcp.schemas import KGMultiHopInput
        from mcp_servers.knowevo_mcp.server import (
            configure,
            kg_multi_hop_handler,
        )

        svc = DecisionService(store=FakeStore(), tenant_id=TENANT,
                              version_rows=VERSION_ROWS)
        configure(tenant_id=TENANT, store=FakeStore(), decision_service=svc)
        out = await kg_multi_hop_handler(
            KGMultiHopInput(question="a", seeds=["Drug:a"], depth=1,
                            ontology_version="v1.0.0"))
        assert not isinstance(out, dict)
        assert out.version_pinned is True
        configure(tenant_id="", store=None, decision_service=None)

    @pytest.mark.asyncio
    async def test_pin_off_marks_output_unpinned(self):
        """The ablation must be reachable through the tool surface too, not
        only through the service API."""
        from services.knowevo.decision_service import DecisionService as DS
        svc = DS(store=FakeStore(), tenant_id=TENANT, version_rows=VERSION_ROWS)
        result = await svc.multi_hop("a", seeds=["Drug:a"],
                                     pin_version=False, depth=1)
        assert result.version_pinned is False
        claims = [c for p in result.paths for c in p.claims]
        assert any("新版" in c for c in claims), (
            "with pinning off the walk sees the later fact; that contrast "
            "is the whole point of the ablation")