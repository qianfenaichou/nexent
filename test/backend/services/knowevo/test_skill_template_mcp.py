"""
Tests for the skill_template_apply MCP tool (T-20): dual registration,
input guardrails, the apply/reuse honesty contract and structured errors.

The tool surface has two registration forms - the standalone FastMCP server
(mcp_servers/knowevo_mcp/server.py) and the Local-MCP inner registration
(backend/tool_collection/mcp/kg_tools.py). Pitfall #27 was exactly this
pair drifting apart, so the same assertions apply to the 5th tool: one
schema object, one handler, both surfaces.

Layer 1 (always runs): schema source, guardrails, handler behaviour over
the in-memory service seam (FakeStore from the service test's shape), and
the honesty contract - apply NEVER writes reuse_success (only
record_reuse_outcome does) and never fabricates a rate.
"""
import asyncio
import sys
import uuid as uuid_mod
from pathlib import Path
from typing import ClassVar

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from pydantic import ValidationError

TENANT = "11111111-1111-1111-1111-111111111111"

_BODY = ("---\nname: {template_name}\n---\n"
         "domain={domain} task={task_type} rel={relation_template} "
         "rules={domain_rules}")


class FakeStore:
    """Minimal skill_template_t stand-in: get_by_name + update only - the
    two operations apply_template touches. Update accepts only real
    columns (a regression that writes an internal key fails here too)."""

    COLUMNS: ClassVar[set[str]] = {"name", "task_type", "domain", "version",
                                   "body_md", "variables", "source",
                                   "reuse_count", "reuse_success",
                                   "avg_edit_distance"}

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.updates: list[dict] = []

    async def get_by_name(self, name, tenant_id):
        row = self.rows.get(name)
        return dict(row) if row else None

    async def update(self, name, tenant_id, values):
        self.updates.append(dict(values))
        row = self.rows.get(name)
        if row is None:
            raise KeyError(f"template not found: {name}")
        for key, value in values.items():
            assert key in self.COLUMNS, f"non-column update key: {key}"
            row[key] = value
        return row["id"]


def _make_service(store):
    from services.knowevo.skill_template_service import SkillTemplateService
    return SkillTemplateService(tenant_id=TENANT, store=store)


def _seed_row(name="reasoning_decision-general", reuse_count=0,
              reuse_success=None):
    return {
        "id": str(uuid_mod.uuid4()),
        "name": name,
        "task_type": "reasoning_decision",
        "domain": "general",
        "version": "1.0.0",
        "body_md": _BODY,
        "variables": {"domain": "general", "task_type": "reasoning_decision",
                      "relation_template": "禁忌->药品", "domain_rules": ""},
        "source": {"pattern": "general/reasoning_decision",
                   "mined_from": ["c1", "c2"], "induced_at": "t0"},
        "reuse_count": reuse_count,
        "reuse_success": reuse_success,
        "avg_edit_distance": None,
    }


# ---------------------------------------------------------------------------
# Dual registration (pitfall #27 discipline, extended to the 5th tool)
# ---------------------------------------------------------------------------

class TestDualRegistration:
    def test_tool_names_include_skill_template_apply(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES
        assert "skill_template_apply" in KG_MCP_TOOL_NAMES

    def test_handlers_map_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, handlers
        assert set(handlers()) == set(KG_MCP_TOOL_NAMES)

    def test_schema_manifest_matches_tool_names(self):
        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES, tool_schemas
        assert set(tool_schemas()) == set(KG_MCP_TOOL_NAMES)

    def test_local_and_standalone_share_one_schema_object(self):
        from mcp_servers.knowevo_mcp.schemas import (
            SkillTemplateApplyInput as std,
        )

        from tool_collection.mcp.kg_tools import (
            SkillTemplateApplyInput as local,
        )
        assert std is local

    def test_local_and_standalone_share_one_handler(self):
        from mcp_servers.knowevo_mcp.server import (
            skill_template_apply_handler as std,
        )

        from tool_collection.mcp.kg_tools import handlers
        assert handlers()["skill_template_apply"] is std

    def test_fastmcp_app_registers_the_tool(self):
        from mcp_servers.knowevo_mcp.server import mcp
        names = {t.name for t in mcp._tool_manager._tools.values()}
        assert "skill_template_apply" in names, (
            "the standalone FastMCP surface must advertise the tool")

    def test_all_five_tools_registered_on_standalone_app(self):
        from mcp_servers.knowevo_mcp.server import mcp

        from tool_collection.mcp.kg_tools import KG_MCP_TOOL_NAMES
        names = {t.name for t in mcp._tool_manager._tools.values()}
        assert set(KG_MCP_TOOL_NAMES) <= names

    def test_schema_advertises_the_name_bound(self):
        # The advertised JSON schema must carry the bound (name column is
        # String(64)) - an Agent reads the schema, not the code.
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        schema = SkillTemplateApplyInput.model_json_schema()
        assert schema["properties"]["template_name"]["maxLength"] == 64


# ---------------------------------------------------------------------------
# Input guardrails (SPEC discipline 3: live in the Pydantic model)
# ---------------------------------------------------------------------------

class TestInputGuardrails:
    def test_empty_template_name_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        with pytest.raises(ValidationError):
            SkillTemplateApplyInput(template_name="")

    def test_template_name_above_64_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        with pytest.raises(ValidationError):
            SkillTemplateApplyInput(template_name="x" * 65)

    def test_template_name_at_64_is_accepted(self):
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        payload = SkillTemplateApplyInput(template_name="x" * 64)
        assert payload.template_name == "x" * 64

    def test_variables_defaults_to_empty(self):
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        payload = SkillTemplateApplyInput(template_name="t")
        assert payload.variables == {}

    def test_non_string_variable_value_is_rejected(self):
        # The service stringifies values anyway; the MCP schema keeps the
        # Agent-facing contract strict so structured junk is refused before
        # any DB round-trip.
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        with pytest.raises(ValidationError):
            SkillTemplateApplyInput(
                template_name="t", variables={"domain": {"nested": 1}})

    def test_variables_above_16_keys_is_rejected(self):
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        with pytest.raises(ValidationError):
            SkillTemplateApplyInput(
                template_name="t",
                variables={f"k{i}": "v" for i in range(17)})


# ---------------------------------------------------------------------------
# Handler behaviour over the service seam
# ---------------------------------------------------------------------------

class TestSkillTemplateApplyHandler:
    def _handler(self):
        from mcp_servers.knowevo_mcp.server import skill_template_apply_handler
        return skill_template_apply_handler

    def _input(self, name="reasoning_decision-general", variables=None):
        from mcp_servers.knowevo_mcp.schemas import SkillTemplateApplyInput
        return SkillTemplateApplyInput(template_name=name,
                                       variables=variables or {})

    def test_apply_renders_and_bumps_reuse_count(self):
        store = FakeStore(rows={"reasoning_decision-general": _seed_row()})
        out = asyncio.run(self._handler()(
            self._input(variables={"domain": "t2dm"}),
            tenant_id=TENANT, service=_make_service(store)))
        assert out["name"] == "reasoning_decision-general"
        assert out["reuse_count"] == 1
        assert "domain=t2dm" in out["skill_md"]
        assert "task=reasoning_decision" in out["skill_md"]
        assert "rel=禁忌->药品" in out["skill_md"]
        # Store really updated the counter (the reuse loop closes).
        row = store.rows["reasoning_decision-general"]
        assert row["reuse_count"] == 1
        assert store.updates == [{"reuse_count": 1}]

    def test_apply_payload_carries_spec_cost_fields(self):
        store = FakeStore(rows={"reasoning_decision-general": _seed_row()})
        out = asyncio.run(self._handler()(
            self._input(), tenant_id=TENANT, service=_make_service(store)))
        assert out["used_tokens"] == 0
        assert isinstance(out["elapsed_ms"], int) and out["elapsed_ms"] >= 0

    def test_apply_never_writes_success_rate(self):
        # Honesty contract: at apply time the outcome is unknown. The
        # handler must not call record_reuse_outcome, must not return a
        # success rate, and the stored rate stays untouched (None).
        store = FakeStore(rows={"reasoning_decision-general": _seed_row()})
        out = asyncio.run(self._handler()(
            self._input(), tenant_id=TENANT, service=_make_service(store)))
        assert "reuse_success" not in out
        assert store.rows["reasoning_decision-general"]["reuse_success"] is None

    def test_apply_preserves_existing_success_rate(self):
        # Same contract when a rate was already recorded by real runs.
        store = FakeStore(rows={"reasoning_decision-general": _seed_row(
            reuse_count=3, reuse_success=2 / 3)})
        out = asyncio.run(self._handler()(
            self._input(), tenant_id=TENANT, service=_make_service(store)))
        assert "reuse_success" not in out
        assert store.rows["reasoning_decision-general"]["reuse_success"] == \
            pytest.approx(2 / 3)
        assert store.rows["reasoning_decision-general"]["reuse_count"] == 4

    def test_unknown_template_returns_structured_error(self):
        store = FakeStore(rows={"reasoning_decision-general": _seed_row()})
        out = asyncio.run(self._handler()(
            self._input(name="no-such-template"),
            tenant_id=TENANT, service=_make_service(store)))
        assert isinstance(out, dict)
        assert out["error_code"] == "template_not_found"
        # The hint echoes only the caller's own input - no internals.
        assert "no-such-template" in out["hint"]
        assert "Traceback" not in out["hint"]
        assert ".py" not in out["hint"]
        # And nothing was persisted.
        assert store.updates == []

    def test_store_failure_returns_structured_error(self):
        class BoomStore(FakeStore):
            async def update(self, name, tenant_id, values):
                raise RuntimeError("db down")

        store = BoomStore(
            rows={"reasoning_decision-general": _seed_row()})
        out = asyncio.run(self._handler()(
            self._input(), tenant_id=TENANT, service=_make_service(store)))
        assert isinstance(out, dict), (
            "a store failure must degrade to a structured error, never "
            "propagate into the MCP runtime")
        assert out["error_code"] == "skill_template_apply_failed"
        assert out["hint"] == "template apply failed: RuntimeError"

    def test_unexpected_keyerror_is_not_misreported_as_not_found(self):
        class WeirdStore(FakeStore):
            async def update(self, name, tenant_id, values):
                raise KeyError("some other key")

        store = WeirdStore(
            rows={"reasoning_decision-general": _seed_row()})
        out = asyncio.run(self._handler()(
            self._input(), tenant_id=TENANT, service=_make_service(store)))
        assert out["error_code"] == "skill_template_apply_failed"

    def test_injected_service_is_used_when_supplied(self):
        store = FakeStore(rows={"reasoning_decision-general": _seed_row()})
        svc = _make_service(store)
        out = asyncio.run(self._handler()(self._input(), service=svc))
        assert out["reuse_count"] == 1
