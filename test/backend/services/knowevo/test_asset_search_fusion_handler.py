"""asset_search handler fusion branch (phase 2, offline).

The handler tries the asset fusion factory first and only consumes its
order when the ignition gate is open (>= 2 non-empty routes). Otherwise
- missing ES adapter, gate closed, fusion exception, no overlap with the
service hits - the handler reproduces today's ``search_assets`` path bit
for bit. ``DocAssetService.search_assets`` itself is untouched.

Fused order only reorders the service's hits (A2: details stay a single
source); membership comes from ``search_assets`` (parse gate, supersede
fold, filters already applied there).
"""
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

TENANT = "11111111-1111-1111-1111-111111111111"


def _hit(id_, score=0.5, **over):
    from services.knowevo.schemas import AssetHit

    base = {"id": id_, "asset_no": f"N-{id_}", "title": f"t-{id_}",
            "modality": "text", "doc_type": "guideline",
            "authority_level": 3, "score": score,
            "why": {"matched": "title+metadata",
                    "filters_applied": {"modality": None},
                    "parse_gate": "processed", "es_score_raw": 1.0},
            "parse_status": "processed", "parse_quality": 0.9,
            "superseded": False}
    base.update(over)
    return AssetHit(**base)


class FakeAssetService:
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


class FakeRawClient:
    """Duck-typed AssetRawListClient for the fusion factory."""

    def __init__(self, bm25=None, dense=None, bm25_error=None):
        self.bm25 = list(bm25 or [])
        self.dense = list(dense or [])
        self.bm25_error = bm25_error

    def asset_bm25_hits(self, tenant_id, query, top_k):
        if self.bm25_error is not None:
            raise self.bm25_error
        return list(self.bm25)

    def asset_dense_hits(self, tenant_id, query, top_k):
        return list(self.dense)


def _raw(hid):
    return {"id": hid, "title": f"t-{hid}", "asset_no": f"N-{hid}",
            "es_score": 1.0}


@pytest.fixture(autouse=True)
def _reset_server_injection():
    yield
    import mcp_servers.knowevo_mcp.server as srv

    srv._graph_store = None
    srv._default_tenant = ""
    srv._decision_service = None
    srv._skill_template_service = None
    srv._kg_service = None
    srv._alignment_service = None
    srv._asset_service = None
    if hasattr(srv, "_asset_raw_client"):
        srv._asset_raw_client = None


class TestFusionGateClosed:
    def test_no_raw_client_reproduces_today_path(self, monkeypatch):
        monkeypatch.delenv("ELASTICSEARCH_HOST", raising=False)

        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import asset_search_handler

            svc = FakeAssetService(hits=[_hit("a1"), _hit("a2")])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=5), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        assert [a.id for a in out.assets] == ["a1", "a2"]
        assert out.elapsed_ms >= 0

    def test_gate_closed_single_route_keeps_service_order(self):
        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import (
                asset_search_handler,
                configure,
            )

            configure(asset_raw_client=FakeRawClient(
                bm25=[_raw("a1"), _raw("a2")]))  # dense empty -> gate closed
            svc = FakeAssetService(hits=[_hit("a2"), _hit("a1")])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=5), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        # Service order preserved (fusion must not fire on one route).
        assert [a.id for a in out.assets] == ["a2", "a1"]

    def test_fusion_exception_falls_back_bit_for_bit(self):
        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import (
                asset_search_handler,
                configure,
            )

            configure(asset_raw_client=FakeRawClient(
                bm25=[_raw("a1")], dense=[_raw("d1")],
                bm25_error=RuntimeError("ES down")))
            svc = FakeAssetService(hits=[_hit("a1", score=0.9)])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=5), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        assert [a.id for a in out.assets] == ["a1"]


class TestFusionFired:
    def test_reorders_service_hits_by_fused_ids(self):
        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import (
                asset_search_handler,
                configure,
            )

            # Fused order will be a2 (both routes) then a1 (bm25) then d1.
            configure(asset_raw_client=FakeRawClient(
                bm25=[_raw("a1"), _raw("a2")],
                dense=[_raw("a2"), _raw("d1")],
            ))
            # Service returns a1, a2 (d1 filtered out by parse gate etc.).
            svc = FakeAssetService(hits=[_hit("a1"), _hit("a2")])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=5), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        assert [a.id for a in out.assets] == ["a2", "a1"]

    def test_membership_stays_search_assets_only(self):
        """Fused ids absent from service hits are dropped (A2)."""

        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import (
                asset_search_handler,
                configure,
            )

            configure(asset_raw_client=FakeRawClient(
                bm25=[_raw("ghost"), _raw("a1")],
                dense=[_raw("ghost"), _raw("d1")],
            ))
            svc = FakeAssetService(hits=[_hit("a1"), _hit("other")])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=5), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        ids = [a.id for a in out.assets]
        assert "ghost" not in ids and "d1" not in ids
        assert ids[0] == "a1"
        assert "other" in ids  # service-only hit kept (membership source)

    def test_no_overlap_falls_back_to_service_order(self):
        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import (
                asset_search_handler,
                configure,
            )

            configure(asset_raw_client=FakeRawClient(
                bm25=[_raw("x1"), _raw("x2")],
                dense=[_raw("x1"), _raw("x2")],
            ))
            svc = FakeAssetService(hits=[_hit("a1"), _hit("a2")])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=5), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        assert [a.id for a in out.assets] == ["a1", "a2"]

    def test_limit_truncates_after_reorder(self):
        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import (
                asset_search_handler,
                configure,
            )

            configure(asset_raw_client=FakeRawClient(
                bm25=[_raw("a3"), _raw("a1"), _raw("a2")],
                dense=[_raw("a3"), _raw("a1"), _raw("a2")],
            ))
            svc = FakeAssetService(hits=[
                _hit("a1"), _hit("a2"), _hit("a3")])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=2), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        assert [a.id for a in out.assets] == ["a3", "a1"]

    def test_why_and_card_fields_come_from_service_hits(self):
        async def _run():
            from mcp_servers.knowevo_mcp.schemas import AssetSearchInput
            from mcp_servers.knowevo_mcp.server import (
                asset_search_handler,
                configure,
            )

            configure(asset_raw_client=FakeRawClient(
                bm25=[_raw("a1"), _raw("a2")],
                dense=[_raw("a1"), _raw("a2")],
            ))
            svc = FakeAssetService(hits=[
                _hit("a1", title="说明书A", score=0.8),
                _hit("a2", title="说明书B", score=0.4),
            ])
            return await asset_search_handler(
                AssetSearchInput(query="q", limit=5), asset_service=svc,
                tenant_id=TENANT)

        out = asyncio.run(_run())
        assert out.assets[0].id == "a1"
        assert out.assets[0].title == "说明书A"
        assert out.assets[0].score == 0.8
        assert out.assets[0].why["es_score_raw"] == 1.0
