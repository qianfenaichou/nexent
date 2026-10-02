
import asyncio
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from consts.exceptions import TagManagementNotFoundError, ValidationError
import services.tag_management_service as service_module
from services.tag_management_service import TagManagementService
from services.tag_resource_adapters import (
    AuthenticatedCaller,
    ResolvedTagResource,
    ResourceCapabilities,
    _encode_document_resource_id,
)


def _caller():
    return AuthenticatedCaller(user_id="user-1", authenticated_tenant_id="tenant-a", role="ADMIN")


class _ReadableRegistry:
    async def resolve(self, reference, caller):
        return ResolvedTagResource(
            found=True,
            identity=None,
            display=None,
            capabilities=ResourceCapabilities(can_read=True, can_edit=False),
        )


def _install_batch_dependencies(monkeypatch, states=None):
    monkeypatch.setattr(
        TagManagementService, "resource_adapter_registry", _ReadableRegistry()
    )
    db = MagicMock()
    monkeypatch.setattr(service_module, "TagManagementDB", db)
    projection_db = types.ModuleType("database.document_tag_projection_db")
    projection_db.list_projection_states_for_knowledge_base = MagicMock(
        return_value=states or {}
    )
    monkeypatch.setitem(
        sys.modules, "database.document_tag_projection_db", projection_db
    )
    if "database" in sys.modules:
        monkeypatch.setattr(sys.modules["database"], "document_tag_projection_db", projection_db, raising=False)
    return db


def test_document_batch_status_is_read_scoped_and_applies_predicates(monkeypatch):
    db = _install_batch_dependencies(monkeypatch)
    db.count_resource_assignments_by_ids.side_effect = (
        lambda tenant_id, resource_type, encoded_ids: {encoded_ids[0]: 3, encoded_ids[1]: 1}
    )
    monkeypatch.setattr(
        TagManagementService,
        "filter_authorized_resource_ids",
        lambda tenant_id, resource_type, encoded_ids, predicates: [encoded_ids[1]],
    )
    states = {
        "doc-b": {
            "status": "synced",
            "version": 2,
            "payload": [{"definition_id": 11, "value_id": 21}],
            "retry_count": 0,
            "last_error": None,
            "last_attempt_at": None,
            "next_attempt_at": None,
            "update_time": None,
        }
    }
    sys.modules["database.document_tag_projection_db"].list_projection_states_for_knowledge_base.return_value = states
    fake_projection_module = types.ModuleType("services.tag_document_projection")
    fake_projection_module.document_projection_status_dict = lambda state: {
        "status": state["status"],
        "version": state["version"],
        "tag_count": 1,
    }
    monkeypatch.setitem(sys.modules, "services.tag_document_projection", fake_projection_module)

    results = asyncio.run(
        TagManagementService.get_document_tag_batch_status(
            _caller(),
            "local",
            "kb-1",
            ["doc-a", "doc-b"],
            predicates=[SimpleNamespace(definition_id=11, value_ids=[21])],
        )
    )

    assert results == [
        {
            "document_id": "doc-b",
            "assignment_count": 1,
            "projection_status": {"status": "synced", "version": 2, "tag_count": 1},
        }
    ]


def test_document_batch_status_requires_provider_and_knowledge_base(monkeypatch):
    with pytest.raises(ValidationError, match="provider is required"):
        asyncio.run(
            TagManagementService.get_document_tag_batch_status(
                _caller(), "", "kb-1", ["doc-a"]
            )
        )
    with pytest.raises(ValidationError, match="knowledge_base_id is required"):
        asyncio.run(
            TagManagementService.get_document_tag_batch_status(
                _caller(), "local", " ", ["doc-a"]
            )
        )


class _ForbiddenRegistry:
    async def resolve(self, reference, caller):
        return ResolvedTagResource(
            found=True,
            identity=None,
            display=None,
            capabilities=ResourceCapabilities(can_read=False, can_edit=False),
        )


def test_document_batch_status_fails_closed_when_knowledge_base_is_unreadable(monkeypatch):
    monkeypatch.setattr(
        TagManagementService, "resource_adapter_registry", _ForbiddenRegistry()
    )
    with pytest.raises(TagManagementNotFoundError, match="Knowledge base not found"):
        asyncio.run(
            TagManagementService.get_document_tag_batch_status(
                _caller(), "local", "kb-1", ["doc-a"]
            )
        )


def test_document_batch_status_encoding_matches_resource_adapter(monkeypatch):
    db = _install_batch_dependencies(monkeypatch)
    captured = {}

    def fake_count(tenant_id, resource_type, encoded_ids):
        captured["encoded_ids"] = encoded_ids
        return {}

    db.count_resource_assignments_by_ids.side_effect = fake_count

    results = asyncio.run(
        TagManagementService.get_document_tag_batch_status(
            _caller(), "local", "kb-1", ["doc-a"]
        )
    )

    assert captured["encoded_ids"] == [
        _encode_document_resource_id("local", "kb-1", "doc-a")
    ]
    assert results == [{"document_id": "doc-a", "assignment_count": 0, "projection_status": None}]

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
