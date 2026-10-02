"""Unit tests for repository import precheck."""

import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# sqlalchemy must look like a package so submodule imports succeed.
_sqlalchemy_mock = types.ModuleType("sqlalchemy")
_sqlalchemy_mock.__path__ = []
sys.modules.setdefault("sqlalchemy", _sqlalchemy_mock)
sys.modules.setdefault("sqlalchemy.orm", MagicMock())
sys.modules.setdefault("sqlalchemy.dialects", MagicMock())
sys.modules.setdefault("sqlalchemy.dialects.postgresql", MagicMock())

sys.modules.setdefault("database.skill_db", MagicMock())
sys.modules.setdefault("database.knowledge_db", MagicMock())
sys.modules.setdefault("database.model_management_db", MagicMock())
sys.modules.setdefault("database.remote_mcp_db", MagicMock())
sys.modules.setdefault("database.tool_db", MagicMock())
sys.modules.setdefault("database.group_db", MagicMock())
sys.modules.setdefault("database.user_tenant_db", MagicMock())
sys.modules.setdefault("database.client", MagicMock())

_consts_model = types.ModuleType("consts.model")


class _RepositoryImportRequirementType(str):
    MODEL = "model"
    KB = "knowledge_base"
    MCP = "mcp"
    SKILL = "skill"
    TOOL = "tool"


class _ModelConnectStatusEnum(Enum):
    AVAILABLE = "available"

    @classmethod
    def get_value(cls, status):
        return status or cls.AVAILABLE.value


class _RepositoryImportRequirementItem(BaseModel):
    type: str
    key: str
    name: str
    description: str | None = None
    available: bool
    reason_code: str | None = None
    suggested_new_name: str | None = None


class _RepositoryImportPrecheckResponse(BaseModel):
    agent_repository_id: int
    display_name: str
    total_count: int
    available_count: int
    percent: int
    has_abnormal: bool
    items: list


class _ToolSourceEnum(Enum):
    LOCAL = "local"
    MCP = "mcp"
    LANGCHAIN = "langchain"
    BUILTIN = "builtin"


_consts_model.ModelConnectStatusEnum = _ModelConnectStatusEnum
_consts_model.RepositoryImportRequirementType = _RepositoryImportRequirementType
_consts_model.RepositoryImportRequirementItem = _RepositoryImportRequirementItem
_consts_model.RepositoryImportPrecheckResponse = _RepositoryImportPrecheckResponse
_consts_model.ToolSourceEnum = _ToolSourceEnum
sys.modules["consts.model"] = _consts_model

# Keep model availability real while isolating adapter construction and config.
sys.modules.setdefault("services.model_gateway_service", MagicMock())
sys.modules.setdefault("utils.config_utils", MagicMock())

from services.repository_import_precheck import build_repository_import_precheck


def _snapshot(
    *,
    model_name: str = "gpt-5-mini",
    tools: list | None = None,
    mcp_info: list | None = None,
    skills: list | None = None,
):
    return SimpleNamespace(
        agent_id=1,
        agent_info={
            "1": {
                "agent_id": 1,
                "name": "writer",
                "description": "desc",
                "business_description": "biz",
                "max_steps": 5,
                "provide_run_summary": False,
                "enabled": True,
                "tools": tools or [],
                "managed_agents": [],
                "model_name": model_name,
                "skill_names": [],
            }
        },
        mcp_info=mcp_info or [],
        skills=skills,
    )


@patch("services.repository_import_precheck.skill_db.list_skills")
@patch("services.repository_import_precheck.query_all_tools")
@patch("services.repository_import_precheck.get_mcp_server_by_name_and_tenant")
@patch("services.repository_import_precheck.get_knowledge_record")
@patch("services.repository_import_precheck.get_knowledge_name_map_by_index_names")
@patch("services.repository_import_precheck.get_model_by_model_id")
@patch("services.repository_import_precheck.get_model_id_by_display_name")
def test_build_precheck_all_available(
    mock_get_model_id,
    mock_get_model_by_id,
    mock_kb_name_map,
    mock_kb_record,
    mock_mcp_url,
    mock_query_tools,
    mock_list_skills,
):
    mock_get_model_id.return_value = 7
    mock_get_model_by_id.return_value = {"connect_status": "available"}
    mock_kb_name_map.return_value = {"kb_index": "Brand KB"}
    mock_kb_record.return_value = {"knowledge_describe": "Brand tone reference"}
    mock_mcp_url.return_value = "http://mcp.local"
    mock_query_tools.return_value = [{
        "class_name": "KnowledgeBaseSearchTool",
        "source": "local",
        "is_available": True,
    }]
    mock_list_skills.return_value = []

    snapshot = _snapshot(
        tools=[{
            "class_name": "KnowledgeBaseSearchTool",
            "source": "local",
            "name": "knowledge_base_search",
            "params": {"index_names": ["kb_index"]},
        }],
        mcp_info=[SimpleNamespace(
            mcp_server_name="search-mcp",
            mcp_url="http://x",
        )],
        skills=[SimpleNamespace(skill_name="copy-skill", skill_zip_base64="e30=")],
    )

    result = build_repository_import_precheck(
        agent_repository_id=42,
        display_name="Writer",
        snapshot=snapshot,
        tenant_id="tenant_a",
    )

    assert result.percent == 100
    assert result.has_abnormal is False
    assert result.available_count == result.total_count


@patch("services.repository_import_precheck.skill_db.list_skills")
@patch("services.repository_import_precheck.query_all_tools")
@patch("services.repository_import_precheck.get_model_by_model_id")
@patch("services.repository_import_precheck.get_model_id_by_display_name")
def test_build_precheck_model_unavailable(
    mock_get_model_id,
    mock_get_model_by_id,
    mock_query_tools,
    mock_list_skills,
):
    mock_get_model_id.return_value = None
    mock_query_tools.return_value = []
    mock_list_skills.return_value = []

    result = build_repository_import_precheck(
        agent_repository_id=1,
        display_name="Writer",
        snapshot=_snapshot(),
        tenant_id="tenant_a",
    )

    model_items = [item for item in result.items if item.type == "model"]
    assert len(model_items) == 1
    assert model_items[0].available is False
    assert model_items[0].reason_code == "model_unavailable"


@patch("services.repository_import_precheck.skill_db.list_skills")
@patch("services.repository_import_precheck.query_all_tools")
@patch("services.repository_import_precheck.get_knowledge_record")
@patch("services.repository_import_precheck.get_knowledge_name_map_by_index_names")
@patch("services.repository_import_precheck.get_model_by_model_id")
@patch("services.repository_import_precheck.get_model_id_by_display_name")
def test_build_precheck_kb_not_found(
    mock_get_model_id,
    mock_get_model_by_id,
    mock_kb_name_map,
    mock_kb_record,
    mock_query_tools,
    mock_list_skills,
):
    mock_get_model_id.return_value = 1
    mock_get_model_by_id.return_value = {"connect_status": "available"}
    mock_kb_name_map.return_value = {"missing_index": "Missing KB"}
    mock_kb_record.return_value = {}
    mock_query_tools.return_value = [{
        "class_name": "KnowledgeBaseSearchTool",
        "source": "local",
        "is_available": True,
    }]
    mock_list_skills.return_value = []

    snapshot = _snapshot(tools=[{
        "class_name": "KnowledgeBaseSearchTool",
        "source": "local",
        "name": "knowledge_base_search",
        "params": {"index_names": ["missing_index"]},
    }])

    result = build_repository_import_precheck(
        agent_repository_id=1,
        display_name="Writer",
        snapshot=snapshot,
        tenant_id="tenant_a",
    )

    kb_items = [item for item in result.items if item.type == "knowledge_base"]
    assert len(kb_items) == 1
    assert kb_items[0].available is False
    assert kb_items[0].reason_code == "kb_not_found"


@patch("services.repository_import_precheck.skill_db.list_skills")
@patch("services.repository_import_precheck.query_all_tools")
@patch("services.repository_import_precheck.get_mcp_server_by_name_and_tenant")
@patch("services.repository_import_precheck.get_model_by_model_id")
@patch("services.repository_import_precheck.get_model_id_by_display_name")
def test_build_precheck_mcp_not_found(
    mock_get_model_id,
    mock_get_model_by_id,
    mock_mcp_url,
    mock_query_tools,
    mock_list_skills,
):
    mock_get_model_id.return_value = 1
    mock_get_model_by_id.return_value = {"connect_status": "available"}
    mock_mcp_url.return_value = None
    mock_query_tools.return_value = []
    mock_list_skills.return_value = []

    snapshot = _snapshot(mcp_info=[SimpleNamespace(
        mcp_server_name="missing-mcp",
        mcp_url="http://x",
    )])

    result = build_repository_import_precheck(
        agent_repository_id=1,
        display_name="Writer",
        snapshot=snapshot,
        tenant_id="tenant_a",
    )

    mcp_items = [item for item in result.items if item.type == "mcp"]
    assert len(mcp_items) == 1
    assert mcp_items[0].available is False
    assert mcp_items[0].reason_code == "mcp_not_found"


@patch("services.repository_import_precheck.skill_db.list_skills")
@patch("services.repository_import_precheck.query_all_tools")
@patch("services.repository_import_precheck.get_model_by_model_id")
@patch("services.repository_import_precheck.get_model_id_by_display_name")
def test_build_precheck_skill_duplicate(
    mock_get_model_id,
    mock_get_model_by_id,
    mock_query_tools,
    mock_list_skills,
):
    mock_get_model_id.return_value = 1
    mock_get_model_by_id.return_value = {"connect_status": "available"}
    mock_query_tools.return_value = []
    mock_list_skills.return_value = [{"name": "dup-skill"}]

    snapshot = _snapshot(
        skills=[SimpleNamespace(skill_name="dup-skill", skill_zip_base64="e30=")],
    )

    result = build_repository_import_precheck(
        agent_repository_id=1,
        display_name="Writer",
        snapshot=snapshot,
        tenant_id="tenant_a",
    )

    skill_items = [item for item in result.items if item.type == "skill"]
    assert len(skill_items) == 1
    assert skill_items[0].available is False
    assert skill_items[0].reason_code == "skill_duplicate"

# --- The doubles installed above are scoped to this module --------------------------
# Registering fakes in ``sys.modules`` is what lets the modules under test import
# without their real collaborators, but a hand-built ``types.ModuleType`` has no
# ``__file__`` and no package ``__path__``. Left in place it makes every later
# ``from <package>.<module> import ...`` in the same run fail with
# "ModuleNotFoundError: ... '<package>' is not a package", so the registrations are
# dropped again as soon as this module finishes importing. ``setup_module`` replays
# them for this module's own tests, which keeps patching by module path working, and
# ``teardown_module`` takes them back out. Entries that were already present are
# restored to their previous value rather than deleted.

import types as _scope_types
from unittest.mock import Mock as _scope_mock_base


def _is_import_double(module):
    """Tell a hand-made test double from a genuinely imported module."""
    if isinstance(module, _scope_mock_base):
        return True
    if type(module) is not _scope_types.ModuleType:
        return False
    if getattr(module, "__file__", None):
        return False
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None) if spec is not None else None
    return not origin or origin in ("namespace", "built-in", "frozen")


def _collect_import_doubles(snapshot):
    """Map every double this module installed to the entry it displaced."""
    doubles = {}
    for name, module in list(sys.modules.items()):
        if snapshot.get(name) is module:
            continue
        if _is_import_double(module):
            doubles[name] = (snapshot.get(name), module)
    return doubles


_IMPORT_DOUBLES = _collect_import_doubles(_STUB_MODULE_SNAPSHOT)
_SAVED_IMPORT_MODULES = {}


def _install_import_doubles():
    """Register this module's doubles, returning what has to be put back."""
    saved = {}
    for name, (_previous, double) in _IMPORT_DOUBLES.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = double
    return saved


def _restore_modules(saved):
    """Undo :func:`_install_import_doubles`, deleting entries that were absent."""
    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _eject_import_doubles():
    """Take the doubles out again once this module has finished importing."""
    for name, (previous, _double) in _IMPORT_DOUBLES.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def setup_module(_module=None):
    """Re-register the doubles for the duration of this module's tests."""
    global _SAVED_IMPORT_MODULES
    _SAVED_IMPORT_MODULES = _install_import_doubles()


def teardown_module(_module=None):
    """Drop the doubles again so the next module sees the real packages."""
    _restore_modules(_SAVED_IMPORT_MODULES)


_eject_import_doubles()
