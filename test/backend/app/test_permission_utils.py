"""Direct unit tests for backend/apps/permission_utils.py.

This module is the FastAPI adapter layer that translates domain exceptions raised
by ``ElasticSearchService.require_knowledge_base_{edit,read}_permission`` into the
correct ``HTTPException`` status codes required by the web framework:

    ``ValueError``       -> ``404 NOT_FOUND``  (KB record does not exist in DB)
    ``PermissionError``  -> ``403 FORBIDDEN``  (user lacks the required permission)
    no exception         -> normal return      (permission check passed)

The tests below exercise ALL three code paths for BOTH public functions in the module.
"""

import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Module-level stubbing (mirrors the pattern used across the test suite so
# that backend packages import successfully without a live venv/runtime):
# - backend/ and backend/apps/ are added to sys.path
# - management.services.knowledge_base.service is stubbed (no DB / ES dependency)
# The real ``apps.permission_utils`` module is then imported and tested
# against the stubbed ``ElasticSearchService``.
# ---------------------------------------------------------------------------

_BACKEND_DIR = "D:/work/public/nexent/backend"
_APPS_DIR = f"{_BACKEND_DIR}/apps"
for _path in (_BACKEND_DIR, _APPS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

sys.modules.setdefault("management.services.knowledge_base.service", MagicMock())  # noqa: SIM117

with MagicMock() as _stub_es:
    # Expose the two static methods we patch in individual tests.
    _stub_es.require_knowledge_base_edit_permission = MagicMock(return_value="EDIT")
    _stub_es.require_knowledge_base_read_permission = MagicMock(return_value="READ_ONLY")
    sys.modules["management.services.knowledge_base.service"].ElasticSearchService = _stub_es

from apps import permission_utils  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_es_mocks():
    """Reset ElasticSearchService stubs between tests to avoid cross-test leakage."""
    yield
    permission_utils.ElasticSearchService.require_knowledge_base_edit_permission = MagicMock(
        return_value="EDIT"
    )
    permission_utils.ElasticSearchService.require_knowledge_base_read_permission = MagicMock(
        return_value="READ_ONLY"
    )


# ===========================================================================
# require_knowledge_base_edit_permission
# ===========================================================================


class TestRequireKnowledgeBaseEditPermission:
    """Tests for the edit-permission FastAPI adapter."""

    def test_edit_happy_path_passes_arguments_through(self):
        """When ES layer returns successfully, the adapter returns None and forwards args."""
        mock = permission_utils.ElasticSearchService.require_knowledge_base_edit_permission
        mock.return_value = "EDIT"

        result = permission_utils.require_knowledge_base_edit_permission(
            index_name="kb-1", user_id="user-1", tenant_id="tenant-1"
        )

        assert result is None
        mock.assert_called_once_with(
            index_name="kb-1", user_id="user-1", tenant_id="tenant-1"
        )

    def test_edit_value_error_maps_to_404(self, monkeypatch):
        """KB missing in DB -> ValueError from ES layer -> 404 NOT_FOUND."""

        def raise_missing(**_kwargs):
            raise ValueError("Knowledge base 'missing-kb' not found")

        monkeypatch.setattr(
            permission_utils.ElasticSearchService,
            "require_knowledge_base_edit_permission",
            raise_missing,
        )

        with pytest.raises(HTTPException) as exc_info:
            permission_utils.require_knowledge_base_edit_permission(
                "missing-kb", "user-1", "tenant-1"
            )

        assert exc_info.value.status_code == HTTPStatus.NOT_FOUND
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Knowledge base 'missing-kb' not found"

    def test_edit_permission_error_maps_to_403(self, monkeypatch):
        """User lacks edit permission -> PermissionError -> 403 FORBIDDEN."""

        def raise_forbidden(**_kwargs):
            raise PermissionError("No permission to modify this knowledge base")

        monkeypatch.setattr(
            permission_utils.ElasticSearchService,
            "require_knowledge_base_edit_permission",
            raise_forbidden,
        )

        with pytest.raises(HTTPException) as exc_info:
            permission_utils.require_knowledge_base_edit_permission(
                "kb-1", "user-1", "tenant-1"
            )

        assert exc_info.value.status_code == HTTPStatus.FORBIDDEN
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "No permission to modify this knowledge base"


# ===========================================================================
# require_knowledge_base_read_permission
# ===========================================================================


class TestRequireKnowledgeBaseReadPermission:
    """Tests for the read-permission FastAPI adapter (Issue #3339)."""

    def test_read_happy_path_passes_arguments_through(self):
        """When ES layer returns successfully with a read-level permission, adapter returns None."""
        mock = permission_utils.ElasticSearchService.require_knowledge_base_read_permission
        mock.return_value = "READ_ONLY"

        result = permission_utils.require_knowledge_base_read_permission(
            index_name="kb-readonly", user_id="user-2", tenant_id="tenant-2"
        )

        assert result is None
        mock.assert_called_once_with(
            index_name="kb-readonly", user_id="user-2", tenant_id="tenant-2"
        )

    def test_read_value_error_maps_to_404(self, monkeypatch):
        """KB missing in DB -> ValueError -> 404 NOT_FOUND."""

        def raise_missing(**_kwargs):
            raise ValueError("Knowledge base 'ghost' not found")

        monkeypatch.setattr(
            permission_utils.ElasticSearchService,
            "require_knowledge_base_read_permission",
            raise_missing,
        )

        with pytest.raises(HTTPException) as exc_info:
            permission_utils.require_knowledge_base_read_permission(
                "ghost", "user-2", "tenant-2"
            )

        assert exc_info.value.status_code == HTTPStatus.NOT_FOUND
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Knowledge base 'ghost' not found"

    def test_read_permission_error_maps_to_403(self, monkeypatch):
        """User lacks any read permission -> PermissionError -> 403 FORBIDDEN."""

        def raise_forbidden(**_kwargs):
            raise PermissionError("No permission to access this knowledge base")

        monkeypatch.setattr(
            permission_utils.ElasticSearchService,
            "require_knowledge_base_read_permission",
            raise_forbidden,
        )

        with pytest.raises(HTTPException) as exc_info:
            permission_utils.require_knowledge_base_read_permission(
                "kb-1", "user-2", "tenant-2"
            )

        assert exc_info.value.status_code == HTTPStatus.FORBIDDEN
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "No permission to access this knowledge base"

    def test_read_detail_preserves_exception_message(self, monkeypatch):
        """HTTPException.detail preserves the full message from the underlying exception."""
        custom_message = "Custom read-permission denial with unicode: 没有读取权限"

        def raise_detailed(**_kwargs):
            raise PermissionError(custom_message)

        monkeypatch.setattr(
            permission_utils.ElasticSearchService,
            "require_knowledge_base_read_permission",
            raise_detailed,
        )

        with pytest.raises(HTTPException) as exc_info:
            permission_utils.require_knowledge_base_read_permission(
                "kb-x", "user-9", "tenant-9"
            )

        # The message must be preserved byte-for-byte in the detail field.
        assert exc_info.value.detail == custom_message
        assert "没有读取权限" in exc_info.value.detail

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
