import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from unittest.mock import MagicMock

import pytest


# Ensure backend imports resolve
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "../../.."))


# Stub database.client
client_mod = types.ModuleType("database.client")
client_mod.get_db_session = MagicMock(name="get_db_session")
client_mod.filter_property = MagicMock(name="filter_property")
sys.modules["database.client"] = client_mod
sys.modules["backend.database.client"] = client_mod


# Stub db_models
db_models_mod = types.ModuleType("database.db_models")

class MemoryUserConfig:
    user_id = MagicMock(name="MemoryUserConfig.user_id")
    delete_flag = MagicMock(name="MemoryUserConfig.delete_flag")
    config_id = MagicMock(name="MemoryUserConfig.config_id")


db_models_mod.MemoryUserConfig = MemoryUserConfig
sys.modules["database.db_models"] = db_models_mod
sys.modules["backend.database.db_models"] = db_models_mod


from backend.database.memory_config_db import soft_delete_all_configs_by_user_id


@pytest.fixture
def mock_session_ctx():
    session = MagicMock(name="session")
    ctx = MagicMock(name="ctx")
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = None
    return session, ctx


def test_soft_delete_all_configs_by_user_id_success(monkeypatch, mock_session_ctx):
    session, ctx = mock_session_ctx
    # Build query().filter().update(). commit() chain
    mock_query = MagicMock()
    mock_filter = MagicMock()
    mock_filter.update.return_value = 5
    mock_query.filter.return_value = mock_filter
    session.query.return_value = mock_query

    monkeypatch.setattr("backend.database.memory_config_db.get_db_session", lambda: ctx)

    ok = soft_delete_all_configs_by_user_id("user-1", actor="user-1")

    assert ok is True
    session.query.assert_called_once()
    mock_filter.update.assert_called_once()
    session.commit.assert_called_once()


def test_soft_delete_all_configs_by_user_id_failure(monkeypatch, mock_session_ctx):
    session, ctx = mock_session_ctx
    mock_query = MagicMock()
    mock_filter = MagicMock()
    # Simulate exception from update
    mock_filter.update.side_effect = Exception("db error")
    mock_query.filter.return_value = mock_filter
    session.query.return_value = mock_query

    monkeypatch.setattr("backend.database.memory_config_db.get_db_session", lambda: ctx)

    ok = soft_delete_all_configs_by_user_id("user-2", actor="user-2")

    assert ok is False
    session.rollback.assert_called_once()

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
