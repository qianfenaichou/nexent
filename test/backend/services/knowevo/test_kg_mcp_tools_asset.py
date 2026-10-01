"""Tests for the asset_search MCP tool (asset-search charter M3, 2026-09-29).

Closes the last frozen-vocabulary gap: the tool surface is now 9/9
(FROZEN_EIGHT + T-20's skill_template_apply), so this file carries the
same dual-registration assertions as the earlier tools (pitfall #27:
the two registration surfaces must never drift) plus the handler
behaviour over a single-seam fake service (RelationStore style, no
internal mocking) and the memo-10 shape-deviation pin.

Layer 1 (always runs): schema source, guardrails, handler behaviour,
tenant resolution priority, structured errors on failure, and the
memo-10 deviation pin (see competition/docs/verification-reports/
asset-search-shape-deviation-2026-09-29.md).
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from pydantic import ValidationError

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"

# The frozen SPEC.md vocabulary (knowevo/mcp_servers/knowevo_mcp/SPEC.md:10).
FROZEN_EIGHT = {
    "kg_search", "kg_multi_hop", "kg_evolution_trace", "ontology_diff",
    "asset_search", "decision_card_render", "evidence_verify", "kg_stats",
}


@pytest.fixture(autouse=True)
def _reset_server_injection():
    """configure() cannot clear an injected service (None means keep), so
    tests reset the module globals directly - a leak here would reach the
    other MCP test files in the same pytest process."""
    yield
    import mcp_servers.knowevo_mcp.server as srv

    srv._graph_store = None
    srv._default_tenant = ""
    srv._decision_service = None
    srv._skill_template_service = None
    srv._kg_service = None
    srv._alignment_service = None
    srv._asset_service = None


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeAssetService:
    """``search_assets`` stand-in (single seam, RelationStore style):
    records the call kwargs and returns canned AssetHits or raises."""

    def __init__(self, hits=None, error=None):
        self.hits = list(hits or [])
        self.error = error
        self.calls = []

    async def search_assets(self, tenant_id, query, modality=None,
                            doc_type=None, authority_min=None,
                            include_superseded=False, limit=5):
        self.calls.append({"tenant_id": tenant_id, "query": query,
                           "modality": modality, "doc_type": doc_type,
                           "authority_min": authority_min,
                           "include_superseded": include_superseded,
                           "limit": limit})
        if self.error is not None:
            raise self.error
        return list(self.hits)


def _hit(id_="DA-1", **over):
    from services.knowevo.schemas import AssetHit

    base = {"id": id_, "asset_no": f"ASSET-{id_}", "title": "二甲双胍片说明书",
            "modality": "text", "doc_type": "drug_label", "authority_level": 3,
            "score": 0.9,
            "why": {"es_score_raw": 2.7, "matched": "title+metadata",
                    "filters_applied": {"modality": None},
                    "parse_gate": "processed"},
            "parse_status": "processed", "parse_quality": 0.8,
            "superseded": False}
    base.update(over)
    return AssetHit(**base)


# ---------------------------------------------------------------------------
# Dual registration (pitfall #27 discipline, extended to asset_search)
# ---------------------------------------------------------------------------

class TestAssetRegistration:
    def test_tool_names_cover_frozen_vocabulary_nine_of_nine(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES

        assert set(KG_MCP_TOOL_NAMES) == (FROZEN_EIGHT
                                          | {"skill_template_apply"})
        assert len(KG_MCP_TOOL_NAMES) == 9

    def test_local_and_standalone_share_one_schema_object(self):
        # Single schema source (SPEC discipline 1): the same class object
        # must be reachable from both registration surfaces.
        import tool_collection.mcp.kg_tools as local_mod

        import mcp_servers.knowevo_mcp.schemas as std_mod

        assert std_mod.AssetSearchInput is local_mod.AssetSearchInput
        assert std_mod.AssetSearchOutput is local_mod.AssetSearchOutput

    def test_local_and_standalone_share_one_handler(self):
        from tool_collection.mcp.kg_tools import handlers

        import mcp_servers.knowevo_mcp.server as srv

        assert handlers()["asset_search"] is srv.asset_search_handler

    def test_schema_manifest_and_handlers_cover_all_nine(self):
        from tool_collection.mcp.kg_tools import (
            KG_MCP_TOOL_NAMES,
            handlers,
            tool_schemas,
        )

        assert set(tool_schemas()) == set(KG_MCP_TOOL_NAMES)
        assert set(handlers()) == set(KG_MCP_TOOL_NAMES)

    def test_fastmcp_app_advertises_all_nine(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES

        from mcp_servers.knowevo_mcp.server import mcp

        names = {t.name for t in mcp._tool_manager._tools.values()}
        assert set(KG_MCP_TOOL_NAMES) <= names

    def test_standalone_app_tool_count_is_nine(self):
        from mcp_servers.knowevo_mcp.server import mcp

        assert len(mcp._tool_manager._tools) == 9


# ---------------------------------------------------------------------------
# asset_search handler
# ---------------------------------------------------------------------------

class TestAssetSearchHandler:
    @pytest.mark.asyncio
    async def test_normal_return_maps_hits_to_asset_cards(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
        from mcp_servers.knowevo_mcp.server import asset_search_handler

        hit = _hit()
        svc = FakeAssetService([hit, _hit("DA-2")])
        out = await asset_search_handler(
            AssetSearchInput(query="二甲双胍", limit=2), asset_service=svc,
            tenant_id=TENANT)
        assert not isinstance(out, dict)
        assert [a.id for a in out.assets] == ["DA-1", "DA-2"]
        first = out.assets[0]
        assert first.asset_no == "ASSET-DA-1"
        assert first.title == "二甲双胍片说明书"
        assert first.modality == "text" and first.doc_type == "drug_label"
        assert first.authority_level == 3 and first.score == 0.9
        assert first.why == hit.why
        assert first.parse_status == "processed"
        assert first.parse_quality == 0.8
        assert first.superseded is False
        assert out.used_tokens == 0 and out.elapsed_ms >= 0
        assert svc.calls[0]["tenant_id"] == TENANT
        assert svc.calls[0]["query"] == "二甲双胍"
        assert svc.calls[0]["limit"] == 2

    @pytest.mark.asyncio
    async def test_filters_pass_through_to_service(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
        from mcp_servers.knowevo_mcp.server import asset_search_handler

        svc = FakeAssetService()
        await asset_search_handler(
            AssetSearchInput(query="报告", modality="table",
                             doc_type="lab_report", authority_min=2,
                             include_superseded=True, limit=7),
            asset_service=svc, tenant_id=TENANT)
        call = svc.calls[0]
        assert call["modality"] == "table"
        assert call["doc_type"] == "lab_report"
        assert call["authority_min"] == 2
        assert call["include_superseded"] is True
        assert call["limit"] == 7

    @pytest.mark.asyncio
    async def test_service_failure_returns_structured_error(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
        from mcp_servers.knowevo_mcp.server import asset_search_handler

        svc = FakeAssetService(error=RuntimeError("db down"))
        out = await asset_search_handler(
            AssetSearchInput(query="说明书"), asset_service=svc)
        assert isinstance(out, dict), (
            "a service failure must degrade to a structured error, never "
            "propagate into the MCP runtime")
        assert out["error_code"] == "asset_search_failed"
        assert "RuntimeError" in out["hint"]

    @pytest.mark.asyncio
    async def test_tenant_resolution_priority(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
        from mcp_servers.knowevo_mcp.server import asset_search_handler, configure

        svc = FakeAssetService()
        await asset_search_handler(AssetSearchInput(query="说明书"),
                                   asset_service=svc, tenant_id=TENANT)
        assert svc.calls[-1]["tenant_id"] == TENANT

        configure(tenant_id=OTHER_TENANT)
        await asset_search_handler(AssetSearchInput(query="说明书"),
                                   asset_service=svc)
        assert svc.calls[-1]["tenant_id"] == OTHER_TENANT

    @pytest.mark.asyncio
    async def test_missing_tenant_stays_usable(self):
        # No tenant anywhere: the call must not crash - the tenant passes
        # through as '' and the service decides what that means.
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
        from mcp_servers.knowevo_mcp.server import asset_search_handler

        svc = FakeAssetService()
        out = await asset_search_handler(AssetSearchInput(query="说明书"),
                                         asset_service=svc)
        assert not isinstance(out, dict)
        assert svc.calls[-1]["tenant_id"] == ""

    @pytest.mark.asyncio
    async def test_configure_injection_is_used(self):
        import mcp_servers.knowevo_mcp.server as srv
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
        from mcp_servers.knowevo_mcp.server import asset_search_handler, configure

        svc = FakeAssetService([_hit()])
        configure(tenant_id=TENANT, asset_service=svc)
        out = await asset_search_handler(AssetSearchInput(query="说明书"))
        assert not isinstance(out, dict)
        assert out.assets[0].id == "DA-1"
        assert svc.calls and svc.calls[0]["query"] == "说明书"
        assert srv._asset_service is svc

    def test_builder_builds_default_doc_asset_service(self):
        from services.knowevo.doc_asset_service import DocAssetService

        import mcp_servers.knowevo_mcp.server as srv

        svc = srv._asset_service_for()
        assert isinstance(svc, DocAssetService)
        assert svc.es_client is None, (
            "default construction is ES-free: the seam is injected by the "
            "T-08 wiring, the bare service degrades to the PG path")

    @pytest.mark.asyncio
    async def test_output_serializes_to_json_safe_dict(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
        from mcp_servers.knowevo_mcp.server import asset_search_handler

        out = await asset_search_handler(
            AssetSearchInput(query="说明书"), asset_service=FakeAssetService(
                [_hit()]), tenant_id=TENANT)
        dumped = out.model_dump(mode="json")
        assert isinstance(dumped["valid_view"], str)
        assert isinstance(dumped["assets"][0]["why"], dict)


# ---------------------------------------------------------------------------
# Input guardrails (SPEC discipline 3: Field constraints are the boundary)
# ---------------------------------------------------------------------------

class TestAssetSearchGuardrails:
    def test_query_bounds_are_enforced(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput

        with pytest.raises(ValidationError):
            AssetSearchInput(query="")
        with pytest.raises(ValidationError):
            AssetSearchInput(query="x" * 201)
        assert AssetSearchInput(query="x" * 200)

    def test_limit_bounds_are_enforced_and_advertised(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput

        with pytest.raises(ValidationError):
            AssetSearchInput(query="q", limit=0)
        with pytest.raises(ValidationError):
            AssetSearchInput(query="q", limit=21)
        schema = AssetSearchInput.model_json_schema()
        assert schema["properties"]["limit"]["maximum"] == 20

    def test_authority_min_bounds_are_enforced(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput

        with pytest.raises(ValidationError):
            AssetSearchInput(query="q", authority_min=0)
        with pytest.raises(ValidationError):
            AssetSearchInput(query="q", authority_min=5)
        assert AssetSearchInput(query="q", authority_min=4)

    def test_string_field_lengths_are_enforced(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput

        with pytest.raises(ValidationError):
            AssetSearchInput(query="q", modality="x" * 13)
        with pytest.raises(ValidationError):
            AssetSearchInput(query="q", doc_type="x" * 25)
        assert AssetSearchInput(query="q", modality="x" * 12,
                                doc_type="x" * 24)


# ---------------------------------------------------------------------------
# memo-10 shape-deviation pin (2026-09-29)
# ---------------------------------------------------------------------------

class TestMemo10ShapeDeviationPin:
    """The frozen memo-10 §1 asset_search row (archive/04-算法与架构决策
    备忘录/10-A2-MCP工具面与A3版本模型.md:16) sketches
    ``{query, modality?: list, authority_level?} ->
    {assets: [{doc_id, modality, version, anchor_span}]}``. The implemented
    I/O follows the landed DocAssetService capability instead (asset-search
    charter §4.2); the deviation is registered in
    competition/docs/verification-reports/asset-search-shape-deviation-
    2026-09-29.md and pinned here so it can neither drift back to the
    memo sketch nor wander further."""

    def test_input_shape_is_the_landed_capability(self):
        from mcp_servers.knowevo_mcp.schemas import AssetSearchInput

        props = AssetSearchInput.model_json_schema()["properties"]
        assert set(props) == {"query", "modality", "doc_type",
                              "authority_min", "include_superseded", "limit"}
        # deviation vs memo-10: single-value modality filter (not a list)
        # and an authority floor (authority_min), not an exact level.
        assert "authority_level" not in props
        modality_types = {v.get("type")
                          for v in props["modality"]["anyOf"]}
        assert modality_types == {"string", "null"}, (
            "modality is one optional string (exact filter), not the "
            "memo-10 sketch's [text, table, image] list")

    def test_output_card_shape_is_the_landed_capability(self):
        from mcp_servers.knowevo_mcp.schemas import AssetCard

        props = AssetCard.model_json_schema()["properties"]
        assert set(props) == {"id", "asset_no", "title", "modality",
                              "doc_type", "authority_level", "score", "why",
                              "parse_status", "parse_quality", "superseded"}
        # deviation vs memo-10 sketch: no doc_id/version/anchor_span; the
        # audit channel is why (+ parse_status/parse_quality/superseded).
        assert "doc_id" not in props and "anchor_span" not in props
        assert props["why"]["type"] == "object"

    def test_service_layer_hit_carries_the_same_fields(self):
        # The MCP card mirrors the service AssetHit 1:1 - re-declaring a
        # different shape would be a second schema source (SPEC disc. 1).
        import dataclasses

        from services.knowevo.schemas import AssetHit

        service_fields = {f.name for f in dataclasses.fields(AssetHit)}
        assert service_fields == {"id", "asset_no", "title", "modality",
                                  "doc_type", "authority_level", "score",
                                  "why", "parse_status", "parse_quality",
                                  "superseded"}
