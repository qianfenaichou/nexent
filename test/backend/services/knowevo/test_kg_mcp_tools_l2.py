"""
Tests for the L2 tool-surface completion: kg_evolution_trace,
ontology_diff and evidence_verify (the frozen-vocabulary tools wrapped
over KGService.evolution_trace / AlignmentService.list_diffs /
GraphStore.reachable_decisions).

The tool surface has two registration forms - the standalone FastMCP
server (mcp_servers/knowevo_mcp/server.py) and the Local-MCP inner
registration (backend/tool_collection/mcp/kg_tools.py). Pitfall #27 was
exactly this pair drifting apart, so the same dual-registration
assertions as the earlier tools apply here, plus the frozen-vocabulary
accounting: the SPEC.md 8-tool list is covered except asset_search,
whose backend capability does not exist yet (2026-09-28 audit: no
doc_asset retrieval in services/knowevo) - the gap is pinned by a test
so it cannot silently grow or be claimed closed.

Layer 1 (always runs): schema source, guardrails, handler behaviour over
in-memory seams (real KGService where its store seam is injectable),
tenant resolution priority and structured errors on failure.
"""
import sys
import uuid as uuid_mod
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from pydantic import ValidationError
from services.knowevo.kg_service import KGService
from services.knowevo.schemas import Timeline

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"

# The frozen SPEC.md vocabulary (knowevo/mcp_servers/knowevo_mcp/SPEC.md:10).
FROZEN_EIGHT = {
    "kg_search", "kg_multi_hop", "kg_evolution_trace", "ontology_diff",
    "asset_search", "decision_card_render", "evidence_verify", "kg_stats",
}
# skill_template_apply is T-20's additive tool beyond the frozen 8.
L2_NEW = ("kg_evolution_trace", "ontology_diff", "evidence_verify")
# tool name -> the schema class / handler function shared by both surfaces
L2_SCHEMA_CLASSES = {
    "kg_evolution_trace": "KGEvolutionTraceInput",
    "ontology_diff": "OntologyDiffInput",
    "evidence_verify": "EvidenceVerifyInput",
}

BEFORE = datetime(2024, 1, 1, tzinfo=UTC)
EARLY = datetime(2023, 6, 1, tzinfo=UTC)
MID = datetime(2023, 12, 1, tzinfo=UTC)


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


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class RelationStore:
    """The ``list_relations_by_entity`` seam only - what the entity branch
    of KGService.evolution_trace needs. Superseded edges are included:
    that is the point of a timeline."""

    def __init__(self, relations):
        self.relations = list(relations)
        self.calls = []

    async def list_relations_by_entity(self, tenant_id, stable_id):
        self.calls.append((tenant_id, stable_id))
        return list(self.relations)


class EvidenceStore:
    """The ``reachable_decisions`` seam only (GIN reverse lookup)."""

    def __init__(self, ids=None, error=None):
        self.ids = list(ids or [])
        self.error = error
        self.calls = []

    async def reachable_decisions(self, tenant_id, entity_ids):
        self.calls.append((tenant_id, list(entity_ids)))
        if self.error is not None:
            raise self.error
        return list(self.ids)


class FakeKGService:
    """evolution_trace stand-in for the decision branch (which reads
    decision_card_t through a DB session - never touchable offline)."""

    def __init__(self, timeline=None, error=None):
        self.timeline = timeline
        self.error = error
        self.calls = []

    async def evolution_trace(self, entity_id=None, decision_id=None,
                              limit=50):
        self.calls.append({"entity_id": entity_id,
                           "decision_id": decision_id, "limit": limit})
        if self.error is not None:
            raise self.error
        return self.timeline or Timeline(entity_id=entity_id,
                                         decision_id=decision_id)


class FakeAlignmentService:
    """``list_diffs`` stand-in: records the limit, optionally raises."""

    def __init__(self, diffs=None, error=None):
        self.diffs = list(diffs) if diffs is not None else [dict(_DIFF)]
        self.error = error
        self.limits = []

    def list_diffs(self, *, limit=50):
        self.limits.append(limit)
        if self.error is not None:
            raise self.error
        return list(self.diffs)[:limit]


_DIFF = {
    "diff_id": "d1", "old_asset_no": "GL-2024", "new_asset_no": "GL-2025",
    "created_at": "2026-09-01T00:00:00",
    "change_counts": {"modified": 3, "added": 1},
}

# Relation dicts shaped like kg_service._relation_row_to_dict - the shape
# KGService.evolution_trace consumes. REL_SUPERSEDED is an invalidated
# edge: a timeline that hides it cannot show that anything evolved.
REL_CURRENT = {"id": "e1", "src": "Drug:a", "dst": "Disease:b",
               "rel_type": "treats", "claim": "a 治疗 b", "contested": False,
               "evidence_id": "ev1", "valid_at": BEFORE, "invalid_at": None}
REL_SUPERSEDED = {"id": "e2", "src": "Drug:a", "dst": "Drug:c",
                  "rel_type": "replaced_by", "claim": "旧版：a 曾被 c 取代",
                  "contested": False, "evidence_id": "ev2",
                  "valid_at": EARLY, "invalid_at": MID}


# ---------------------------------------------------------------------------
# Dual registration (pitfall #27 discipline, extended to the L2 trio)
# ---------------------------------------------------------------------------

class TestL2Registration:
    def test_tool_names_cover_frozen_eight_minus_asset_search(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES

        assert set(KG_MCP_TOOL_NAMES) == (FROZEN_EIGHT - {"asset_search"}
                                          | {"skill_template_apply"})
        assert len(KG_MCP_TOOL_NAMES) == 8

    def test_frozen_vocabulary_gap_is_exactly_asset_search(self):
        # The one frozen member still missing is asset_search - no backend
        # capability exists (no doc_asset retrieval in services/knowevo).
        # This pin keeps the gap explicit: it must not silently grow, and
        # the tool must not be claimed registered until it exists.
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES

        assert FROZEN_EIGHT - set(KG_MCP_TOOL_NAMES) == {"asset_search"}

    def test_handlers_map_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, handlers

        assert set(handlers()) == set(KG_MCP_TOOL_NAMES)

    def test_schema_manifest_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, tool_schemas

        assert set(tool_schemas()) == set(KG_MCP_TOOL_NAMES)

    @pytest.mark.parametrize("name", L2_NEW)
    def test_local_and_standalone_share_one_schema_object(self, name):
        # Single schema source (SPEC discipline 1): the same class object
        # must be reachable from both registration surfaces.
        import tool_collection.mcp.kg_tools as local_mod

        import mcp_servers.knowevo_mcp.schemas as std_mod

        cls = L2_SCHEMA_CLASSES[name]
        assert getattr(std_mod, cls) is getattr(local_mod, cls)

    @pytest.mark.parametrize("name", L2_NEW)
    def test_local_and_standalone_share_one_handler(self, name):
        from tool_collection.mcp.kg_tools import handlers

        import mcp_servers.knowevo_mcp.server as srv

        assert handlers()[name] is getattr(srv, f"{name}_handler")

    def test_fastmcp_app_advertises_all_eight(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES

        from mcp_servers.knowevo_mcp.server import mcp

        names = {t.name for t in mcp._tool_manager._tools.values()}
        assert set(KG_MCP_TOOL_NAMES) <= names

    def test_standalone_app_tool_count_is_eight(self):
        from mcp_servers.knowevo_mcp.server import mcp

        assert len(mcp._tool_manager._tools) == 8


# ---------------------------------------------------------------------------
# kg_evolution_trace
# ---------------------------------------------------------------------------

class TestKGEvolutionTrace:
    @pytest.mark.asyncio
    async def test_entity_timeline_includes_superseded_edges(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput
        from mcp_servers.knowevo_mcp.server import kg_evolution_trace_handler

        store = RelationStore([REL_CURRENT, REL_SUPERSEDED])
        out = await kg_evolution_trace_handler(
            KGEvolutionTraceInput(entity_id="Drug:a"), store=store,
            tenant_id=TENANT)
        assert not isinstance(out, dict)
        assert out.entity_id == "Drug:a"
        assert [e["dst"] for e in out.events] == ["Drug:c", "Disease:b"], (
            "events are oldest-first and the superseded edge must appear - "
            "a current-view-only timeline cannot show evolution")
        assert out.events[0]["evidence_id"] == "ev2"
        assert out.events[0]["invalid_at"] is not None
        assert out.truncated is False
        assert out.used_tokens == 0 and out.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_tenant_flows_through_service_to_store(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput
        from mcp_servers.knowevo_mcp.server import kg_evolution_trace_handler

        store = RelationStore([REL_CURRENT])
        await kg_evolution_trace_handler(
            KGEvolutionTraceInput(entity_id="Drug:a"), store=store,
            tenant_id=TENANT)
        assert store.calls and store.calls[0][0] == TENANT

    @pytest.mark.asyncio
    async def test_limit_truncates_honestly(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput
        from mcp_servers.knowevo_mcp.server import kg_evolution_trace_handler

        store = RelationStore([REL_CURRENT, REL_SUPERSEDED])
        out = await kg_evolution_trace_handler(
            KGEvolutionTraceInput(entity_id="Drug:a", limit=1), store=store,
            tenant_id=TENANT)
        assert len(out.events) == 1
        assert out.truncated is True

    @pytest.mark.asyncio
    async def test_decision_branch_through_injected_service(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput
        from mcp_servers.knowevo_mcp.server import kg_evolution_trace_handler

        card_id = uuid_mod.uuid4()
        svc = FakeKGService(Timeline(
            entity_id=None, decision_id=card_id,
            events=[{"type": "decision", "at": "2026-01-01T00:00:00+00:00",
                     "question_id": "q1",
                     "knowledge_stamp": {"cutoff": "2026-01-01"},
                     "needs_rerun": False}],
            truncated=False))
        out = await kg_evolution_trace_handler(
            KGEvolutionTraceInput(decision_id=str(card_id), limit=5),
            kg_service=svc)
        assert not isinstance(out, dict)
        assert out.decision_id == str(card_id)
        assert out.events[0]["knowledge_stamp"] == {"cutoff": "2026-01-01"}
        assert svc.calls[0]["limit"] == 5

    @pytest.mark.asyncio
    async def test_configure_injection_is_used(self):
        import mcp_servers.knowevo_mcp.server as srv
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput
        from mcp_servers.knowevo_mcp.server import configure, kg_evolution_trace_handler

        svc = FakeKGService()
        configure(tenant_id=TENANT, kg_service=svc)
        out = await kg_evolution_trace_handler(
            KGEvolutionTraceInput(entity_id="Drug:a"))
        assert not isinstance(out, dict)
        assert svc.calls and svc.calls[0]["entity_id"] == "Drug:a"
        assert srv._kg_service is svc

    def test_builder_uses_seam_carrying_store(self):
        import mcp_servers.knowevo_mcp.server as srv

        store = RelationStore([])
        svc = srv._kg_service_for(store, TENANT)
        assert isinstance(svc, KGService)
        assert svc.store is store
        assert svc.tenant_id == TENANT

    def test_builder_falls_back_to_pipeline_store_without_seam(self):
        # The GraphStore seams cannot return superseded edges, so a store
        # without list_relations_by_entity must NOT back the timeline -
        # the builder swaps in the K2 pipeline's own PgStore adapter.
        from services.knowevo.graph_store import PgJsonbGraphStore
        from services.knowevo.kg_service import PgStore

        import mcp_servers.knowevo_mcp.server as srv

        svc = srv._kg_service_for(PgJsonbGraphStore(), TENANT)
        assert isinstance(svc.store, PgStore)
        assert svc.tenant_id == TENANT

    def test_builder_tenant_falls_back_to_server_default(self):
        import mcp_servers.knowevo_mcp.server as srv
        from mcp_servers.knowevo_mcp.server import configure

        configure(tenant_id=OTHER_TENANT)
        svc = srv._kg_service_for()
        assert svc.tenant_id == OTHER_TENANT

    def test_missing_both_targets_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput

        with pytest.raises(ValidationError):
            KGEvolutionTraceInput()

    def test_limit_bounds_are_enforced_and_advertised(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput

        with pytest.raises(ValidationError):
            KGEvolutionTraceInput(entity_id="Drug:a", limit=0)
        with pytest.raises(ValidationError):
            KGEvolutionTraceInput(entity_id="Drug:a", limit=201)
        schema = KGEvolutionTraceInput.model_json_schema()
        assert schema["properties"]["limit"]["maximum"] == 200

    @pytest.mark.asyncio
    async def test_service_failure_returns_structured_error(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput
        from mcp_servers.knowevo_mcp.server import kg_evolution_trace_handler

        svc = FakeKGService(error=RuntimeError("db down"))
        out = await kg_evolution_trace_handler(
            KGEvolutionTraceInput(entity_id="Drug:a"), kg_service=svc)
        assert isinstance(out, dict), (
            "a service failure must degrade to a structured error, never "
            "propagate into the MCP runtime")
        assert out["error_code"] == "kg_evolution_trace_failed"
        assert out["hint"]

    @pytest.mark.asyncio
    async def test_output_serializes_to_json_safe_dict(self):
        from mcp_servers.knowevo_mcp.schemas import KGEvolutionTraceInput
        from mcp_servers.knowevo_mcp.server import kg_evolution_trace_handler

        store = RelationStore([REL_CURRENT])
        out = await kg_evolution_trace_handler(
            KGEvolutionTraceInput(entity_id="Drug:a"), store=store,
            tenant_id=TENANT)
        dumped = out.model_dump(mode="json")
        assert isinstance(dumped["valid_view"], str)
        assert isinstance(dumped["events"], list)


# ---------------------------------------------------------------------------
# ontology_diff
# ---------------------------------------------------------------------------

class TestOntologyDiff:
    @pytest.mark.asyncio
    async def test_lists_persisted_diffs_with_counts(self):
        from mcp_servers.knowevo_mcp.schemas import OntologyDiffInput
        from mcp_servers.knowevo_mcp.server import ontology_diff_handler

        svc = FakeAlignmentService()
        out = await ontology_diff_handler(OntologyDiffInput(limit=10),
                                          alignment_service=svc)
        assert not isinstance(out, dict)
        assert out.count == 1
        assert out.diffs[0]["change_counts"] == {"modified": 3, "added": 1}
        assert svc.limits == [10]
        assert out.used_tokens == 0 and out.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_configure_injection_is_used(self):
        import mcp_servers.knowevo_mcp.server as srv
        from mcp_servers.knowevo_mcp.schemas import OntologyDiffInput
        from mcp_servers.knowevo_mcp.server import configure, ontology_diff_handler

        svc = FakeAlignmentService()
        configure(tenant_id=TENANT, alignment_service=svc)
        out = await ontology_diff_handler(OntologyDiffInput())
        assert not isinstance(out, dict)
        assert out.count == 1
        assert srv._alignment_service is svc

    def test_builder_carries_explicit_tenant(self):
        from services.knowevo.alignment_service import AlignmentService

        import mcp_servers.knowevo_mcp.server as srv

        svc = srv._alignment_service_for(TENANT)
        assert isinstance(svc, AlignmentService)
        assert svc.tenant_id == TENANT

    def test_builder_tenant_falls_back_to_server_default(self):
        import mcp_servers.knowevo_mcp.server as srv
        from mcp_servers.knowevo_mcp.server import configure

        configure(tenant_id=OTHER_TENANT)
        assert srv._alignment_service_for().tenant_id == OTHER_TENANT

    @pytest.mark.parametrize("limit", [0, 201])
    def test_limit_bounds_are_enforced(self, limit):
        from mcp_servers.knowevo_mcp.schemas import OntologyDiffInput

        with pytest.raises(ValidationError):
            OntologyDiffInput(limit=limit)

    @pytest.mark.asyncio
    async def test_service_failure_returns_structured_error(self):
        from mcp_servers.knowevo_mcp.schemas import OntologyDiffInput
        from mcp_servers.knowevo_mcp.server import ontology_diff_handler

        svc = FakeAlignmentService(error=RuntimeError("db down"))
        out = await ontology_diff_handler(OntologyDiffInput(),
                                          alignment_service=svc)
        assert isinstance(out, dict)
        assert out["error_code"] == "ontology_diff_failed"
        assert out["hint"]


# ---------------------------------------------------------------------------
# evidence_verify
# ---------------------------------------------------------------------------

class TestEvidenceVerify:
    @pytest.mark.asyncio
    async def test_reverse_lookup_returns_evidence_ids(self):
        from mcp_servers.knowevo_mcp.schemas import EvidenceVerifyInput
        from mcp_servers.knowevo_mcp.server import evidence_verify_handler

        ids = [uuid_mod.uuid4(), uuid_mod.uuid4()]
        store = EvidenceStore(ids)
        out = await evidence_verify_handler(
            EvidenceVerifyInput(entity_ids=["Drug:a", "Disease:b"]),
            store=store, tenant_id=TENANT)
        assert not isinstance(out, dict)
        assert out.evidence_ids == [str(i) for i in ids]
        assert out.matched == 2
        assert out.entity_ids == ["Drug:a", "Disease:b"]
        assert store.calls == [(TENANT, ["Drug:a", "Disease:b"])]

    @pytest.mark.asyncio
    async def test_tenant_resolution_priority(self):
        from mcp_servers.knowevo_mcp.schemas import EvidenceVerifyInput
        from mcp_servers.knowevo_mcp.server import configure, evidence_verify_handler

        store = EvidenceStore()
        await evidence_verify_handler(
            EvidenceVerifyInput(entity_ids=["Drug:a"]), store=store,
            tenant_id=TENANT)
        assert store.calls[-1][0] == TENANT

        configure(tenant_id=OTHER_TENANT)
        await evidence_verify_handler(
            EvidenceVerifyInput(entity_ids=["Drug:a"]), store=store)
        assert store.calls[-1][0] == OTHER_TENANT

    @pytest.mark.asyncio
    async def test_missing_tenant_stays_usable(self):
        # No tenant anywhere: the call must not crash - the tenant passes
        # through as '' and the store decides what that means.
        from mcp_servers.knowevo_mcp.schemas import EvidenceVerifyInput
        from mcp_servers.knowevo_mcp.server import evidence_verify_handler

        store = EvidenceStore()
        out = await evidence_verify_handler(
            EvidenceVerifyInput(entity_ids=["Drug:a"]), store=store)
        assert not isinstance(out, dict)
        assert store.calls[-1][0] == ""

    def test_empty_entity_list_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import EvidenceVerifyInput

        with pytest.raises(ValidationError):
            EvidenceVerifyInput(entity_ids=[])

    def test_entity_list_is_bounded_and_advertised(self):
        from mcp_servers.knowevo_mcp.schemas import EvidenceVerifyInput

        with pytest.raises(ValidationError):
            EvidenceVerifyInput(entity_ids=[f"e{i}" for i in range(21)])
        schema = EvidenceVerifyInput.model_json_schema()
        assert schema["properties"]["entity_ids"]["maxItems"] == 20

    @pytest.mark.asyncio
    async def test_store_failure_returns_structured_error(self):
        from mcp_servers.knowevo_mcp.schemas import EvidenceVerifyInput
        from mcp_servers.knowevo_mcp.server import evidence_verify_handler

        store = EvidenceStore(error=RuntimeError("db down"))
        out = await evidence_verify_handler(
            EvidenceVerifyInput(entity_ids=["Drug:a"]), store=store,
            tenant_id=TENANT)
        assert isinstance(out, dict), (
            "a store failure must degrade to a structured error, never "
            "propagate into the MCP runtime")
        assert out["error_code"] == "evidence_verify_failed"
        assert out["hint"]

    @pytest.mark.asyncio
    async def test_output_serializes_to_json_safe_dict(self):
        from mcp_servers.knowevo_mcp.schemas import EvidenceVerifyInput
        from mcp_servers.knowevo_mcp.server import evidence_verify_handler

        out = await evidence_verify_handler(
            EvidenceVerifyInput(entity_ids=["Drug:a"]),
            store=EvidenceStore([uuid_mod.uuid4()]), tenant_id=TENANT)
        dumped = out.model_dump(mode="json")
        assert all(isinstance(i, str) for i in dumped["evidence_ids"])
