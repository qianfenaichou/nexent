"""UT for backend/services/evaluation_maintenance.py — background scheduler."""

import importlib.util as _ilu
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

_REPO = Path(__file__).resolve().parents[3]
_BACKEND = _REPO / "backend"
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _register_package(name):
    pkg = types.ModuleType(name)
    sys.modules[name] = pkg
    return pkg


# ---- stub chain: database.agent_evaluation_db / client / db_models ----
database_pkg = _register_package("database")
agent_evaluation_db = MagicMock(name="database.agent_evaluation_db")
client = MagicMock(name="database.client")
db_models = MagicMock(name="database.db_models")
db_models.AgentEvaluation = MagicMock(name="AgentEvaluation")
database_pkg.agent_evaluation_db = agent_evaluation_db
database_pkg.client = client
database_pkg.db_models = db_models
sys.modules["database.agent_evaluation_db"] = agent_evaluation_db
sys.modules["database.client"] = client
sys.modules["database.db_models"] = db_models

if "evaluation_maintenance" in sys.modules:
    del sys.modules["evaluation_maintenance"]
_spec = _ilu.spec_from_file_location(
    "evaluation_maintenance", str(_BACKEND / "services" / "evaluation_maintenance.py"))
assert _spec is not None and _spec.loader is not None
em = _ilu.module_from_spec(_spec)
sys.modules["evaluation_maintenance"] = em
_spec.loader.exec_module(em)


@pytest.fixture(autouse=True)
def _reset_running():
    em._running = False
    em.list_dispatchable_pending_runs.return_value = []
    yield
    em._running = False


def test_run_tenant_task_logs_info(mocker):
    logger = mocker.patch.object(em.logger, "info")
    task = MagicMock(return_value=3)
    em._run_tenant_task([("t1",)], task, "Reaped %d for %s", "reap")
    task.assert_called_once_with("t1")
    logger.assert_called_once_with("Reaped %d for %s", 3, "t1")


def test_run_tenant_task_skips_zero_count(mocker):
    logger = mocker.patch.object(em.logger, "info")
    em._run_tenant_task([("t1",), ("t2",)], MagicMock(return_value=0), "x %d %s", "y")
    logger.assert_not_called()


def test_run_tenant_task_swallows_error(mocker):
    logger = mocker.patch.object(em.logger, "warning")
    task = MagicMock(side_effect=RuntimeError("boom"))
    em._run_tenant_task([("t1",)], task, "x %d %s", "reap_stale_runs")
    logger.assert_called_once()
    _args = logger.call_args.args
    assert _args[:3] == ("%s failed for tenant %s: %s", "reap_stale_runs", "t1")
    assert isinstance(_args[3], RuntimeError)


def _fake_session_rows(rows):
    """Wire ``with get_db_session() as session: session.query(...).distinct().all()``.

    ``with`` calls ``__enter__`` which (for MagicMock) returns a child mock,
    so the query chain must be configured on ``__enter__.return_value``.
    """
    session = client.get_db_session.return_value.__enter__.return_value
    session.query.return_value.distinct.return_value.all.return_value = rows
    return session


def test_run_loop_reaps_and_cleans_up(mocker):
    _fake_session_rows([("t1",), ("t2",)])
    pending_runs = [{"agent_evaluation_id": 7}]
    em.list_dispatchable_pending_runs.return_value = pending_runs
    dispatch = mocker.patch.object(em, "_dispatch_pending_runs")
    reap = mocker.patch("evaluation_maintenance.reap_stale_runs", return_value=2)
    cleanup = mocker.patch("evaluation_maintenance.cleanup_aged_evaluations", return_value=5)
    mocker.patch("evaluation_maintenance.time.sleep",
                 side_effect=lambda *a: setattr(em, "_running", False))
    info = mocker.patch.object(em.logger, "info")

    em._running = True
    em._run_loop()

    dispatch.assert_called_once_with(pending_runs)
    assert reap.call_args_list == [call("t1"), call("t2")]
    assert cleanup.call_args_list == [call("t1"), call("t2")]
    info.assert_any_call("Reaped %d stale RUNNING evaluations for tenant %s", 2, "t1")
    info.assert_any_call("Cleaned up %d aged evaluations for tenant %s", 5, "t1")


def test_dispatch_pending_runs_reuses_runtime_dispatcher(mocker, monkeypatch):
    dispatcher = MagicMock()
    runtime_proxy = types.ModuleType("services.runtime_proxy_service")
    runtime_proxy.dispatch_agent_evaluation_run = dispatcher
    monkeypatch.setitem(sys.modules, "services.runtime_proxy_service", runtime_proxy)

    em._dispatch_pending_runs(
        [
            {
                "agent_evaluation_id": 7,
                "tenant_id": "tenant-1",
                "created_by": "user-1",
            }
        ]
    )

    dispatcher.assert_called_once_with(
        agent_evaluation_id=7,
        user_id="user-1",
        tenant_id="tenant-1",
    )


def test_dispatch_pending_runs_returns_immediately_when_empty(monkeypatch):
    monkeypatch.delitem(sys.modules, "services.runtime_proxy_service", raising=False)

    em._dispatch_pending_runs([])

    assert "services.runtime_proxy_service" not in sys.modules


def test_dispatch_pending_runs_skips_incomplete_identity(mocker, monkeypatch):
    dispatcher = MagicMock()
    runtime_proxy = types.ModuleType("services.runtime_proxy_service")
    runtime_proxy.dispatch_agent_evaluation_run = dispatcher
    monkeypatch.setitem(sys.modules, "services.runtime_proxy_service", runtime_proxy)
    warning = mocker.patch.object(em.logger, "warning")

    em._dispatch_pending_runs(
        [
            {
                "agent_evaluation_id": 7,
                "tenant_id": "tenant-1",
                "created_by": None,
            }
        ]
    )

    dispatcher.assert_not_called()
    warning.assert_called_once_with(
        "Cannot redispatch pending evaluation %s without tenant and creator",
        7,
    )


def test_dispatch_pending_runs_keeps_failed_dispatch_pending(mocker, monkeypatch):
    dispatcher = MagicMock(side_effect=RuntimeError("runtime unavailable"))
    runtime_proxy = types.ModuleType("services.runtime_proxy_service")
    runtime_proxy.dispatch_agent_evaluation_run = dispatcher
    monkeypatch.setitem(sys.modules, "services.runtime_proxy_service", runtime_proxy)
    warning = mocker.patch.object(em.logger, "warning")

    em._dispatch_pending_runs(
        [
            {
                "agent_evaluation_id": 7,
                "tenant_id": "tenant-1",
                "created_by": "user-1",
            }
        ]
    )

    warning.assert_called_once()


def test_run_loop_skips_cleanup_until_interval(mocker, monkeypatch):
    _fake_session_rows([("t1",)])
    reap = mocker.patch("evaluation_maintenance.reap_stale_runs", return_value=0)
    cleanup = mocker.patch("evaluation_maintenance.cleanup_aged_evaluations")
    mocker.patch("evaluation_maintenance.time.sleep",
                 side_effect=lambda *a: setattr(em, "_running", False))
    mocker.patch.object(em, "AGED_CLEANUP_INTERVAL", 10**12)

    monkeypatch.setattr(em, "_running", True)
    em._run_loop()

    reap.assert_called_once_with("t1")
    cleanup.assert_not_called()


def test_run_loop_handles_error_and_backs_off(mocker, monkeypatch):
    monkeypatch.setattr(
        client.get_db_session, "side_effect", RuntimeError("db down")
    )
    error_log = mocker.patch.object(em.logger, "error")
    mocker.patch("evaluation_maintenance.time.sleep",
                 side_effect=lambda *a: setattr(em, "_running", False))

    em._running = True
    em._run_loop()

    error_log.assert_called_once()
    _args = error_log.call_args.args
    assert _args[0] == "Evaluation maintenance loop error: %s"
    assert isinstance(_args[1], RuntimeError)


def test_start_starts_thread(mocker):
    thread_cls = mocker.patch("evaluation_maintenance.threading.Thread")
    info = mocker.patch.object(em.logger, "info")

    em.start()

    assert em._running is True
    thread_cls.assert_called_once_with(
        target=em._run_loop, daemon=True, name="eval-maintenance")
    thread_cls.return_value.start.assert_called_once_with()
    info.assert_called_once()


def test_start_idempotent(mocker):
    thread_cls = mocker.patch("evaluation_maintenance.threading.Thread")
    em._running = True
    em.start()
    thread_cls.assert_not_called()


def test_stop_sets_running_false(mocker, monkeypatch):
    info = mocker.patch.object(em.logger, "info")
    monkeypatch.setattr(em, "_running", True)
    em.stop()
    assert em._running is False
    info.assert_called_once()

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
