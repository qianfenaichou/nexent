import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "../../.."))

consts_const = types.ModuleType("consts.const")
consts_const.EXTERNAL_MEMORY_DEFAULT_ALLOWED_UNIT_TYPES = {
    "agent", "model_output", "tool", "final_answer"
}
sys.modules["consts.const"] = consts_const
sys.modules["consts"] = types.ModuleType("consts")

database_pkg = types.ModuleType("database")
event_log_db = types.ModuleType("database.memory_external_ingest_event_log_db")
event_log_db.insert_event_log = MagicMock(name="insert_event_log")
param_db_mod = types.ModuleType("database.memory_provider_config_param_db")
param_db_mod.get_params = MagicMock(name="get_params")
database_pkg.memory_external_ingest_event_log_db = event_log_db
database_pkg.memory_provider_config_param_db = param_db_mod
sys.modules["database"] = database_pkg
sys.modules["database.memory_external_ingest_event_log_db"] = event_log_db
sys.modules["database.memory_provider_config_param_db"] = param_db_mod

nexent_pkg = types.ModuleType("nexent")
memory_pkg = types.ModuleType("nexent.memory")
memory_pkg.__path__ = []
memory_models = types.ModuleType("nexent.memory.models")


class MemoryIngestRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class MemoryIngestResult:
    def __init__(self, **kwargs):
        self.provider = kwargs.get("provider", "")
        self.status = kwargs.get("status", "")
        self.message = kwargs.get("message", "")
        self.accepted_count = kwargs.get("accepted_count", 0)


class MemoryIngestUnit:
    def __init__(self, **kwargs):
        self.event_id = kwargs.get("event_id", "")
        self.event_type = kwargs.get("event_type", "")
        self.unit_type = kwargs.get("unit_type", "")
        self.unit_content = kwargs.get("unit_content", "")


memory_models.MemoryIngestRequest = MemoryIngestRequest
memory_models.MemoryIngestResult = MemoryIngestResult
memory_models.MemoryIngestUnit = MemoryIngestUnit
memory_pkg.models = memory_models
sys.modules["nexent.memory.models"] = memory_models
nexent_pkg.memory = memory_pkg
sys.modules["nexent"] = nexent_pkg
sys.modules["nexent.memory"] = memory_pkg

services_pkg = types.ModuleType("services")
ext_provider_svc_mod = types.ModuleType("services.memory_external_provider_service")
config_svc_mod = types.ModuleType("services.memory_provider_config_service")
ext_provider_svc_mod.MemoryExternalProviderService = MagicMock
config_svc_mod.MemoryProviderConfigService = MagicMock
sys.modules["services"] = services_pkg
sys.modules["services.memory_external_provider_service"] = ext_provider_svc_mod
sys.modules["services.memory_provider_config_service"] = config_svc_mod

from backend.services.memory_ingestion_event_service import MemoryIngestionEventService


@pytest.fixture
def mock_config_service():
    return MagicMock()


@pytest.fixture
def mock_provider_service():
    return MagicMock()


@pytest.fixture
def service(mock_config_service, mock_provider_service):
    return MemoryIngestionEventService(mock_config_service, mock_provider_service)


def _make_unit(event_id="e1", unit_type="agent"):
    return MemoryIngestUnit(event_id=event_id, unit_type=unit_type, unit_content="content")


@pytest.mark.asyncio
async def test_send_ingest_success(service, mock_config_service, mock_provider_service):
    mock_config_service.get_provider.return_value = {
        "provider_config_id": 1, "provider_name": "mem0", "enabled": True,
    }
    mock_provider_service.ingest = AsyncMock(
        return_value=MemoryIngestResult(provider="mem0", status="ok")
    )

    with patch.object(param_db_mod, "get_params", return_value={"plugin.name": "mem0"}), \
         patch.object(event_log_db, "insert_event_log", return_value=1):
        result = await service.send_ingest(
            1, "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
        )
        assert result.status == "ok"


@pytest.mark.asyncio
async def test_send_ingest_disabled_provider(service, mock_config_service):
    mock_config_service.get_provider.return_value = {
        "provider_config_id": 1, "provider_name": "mem0", "enabled": False,
    }

    result = await service.send_ingest(
        1, "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
    )
    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_send_ingest_provider_not_found(service, mock_config_service):
    mock_config_service.get_provider.return_value = None

    result = await service.send_ingest(
        999, "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
    )
    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_send_ingest_empty_after_filter(service, mock_config_service):
    mock_config_service.get_provider.return_value = {
        "provider_config_id": 1, "provider_name": "mem0", "enabled": True,
    }

    result = await service.send_ingest(
        1, "t1", "u1", "a1", "c1", "memory_stored", "e1",
        [_make_unit(unit_type="unsupported_type")],
    )
    assert result.status == "ok"
    assert result.accepted_count == 0


@pytest.mark.asyncio
async def test_send_ingest_idempotency_key_format(service, mock_config_service, mock_provider_service):
    mock_config_service.get_provider.return_value = {
        "provider_config_id": 1, "provider_name": "mem0", "enabled": True,
    }
    mock_provider_service.ingest = AsyncMock(
        return_value=MemoryIngestResult(provider="mem0", status="ok")
    )

    with patch.object(param_db_mod, "get_params", return_value={"plugin.name": "mem0"}) as m_params, \
         patch.object(event_log_db, "insert_event_log", return_value=1) as m_log:
        await service.send_ingest(
            1, "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
        )
        m_params.assert_called_once_with(1)
        log_data = m_log.call_args.args[0]
        assert log_data["idempotency_key"] == "nexent:t1:a1:u1:c1:memory_stored:e1"


@pytest.mark.asyncio
async def test_send_ingest_event_logging(service, mock_config_service, mock_provider_service):
    mock_config_service.get_provider.return_value = {
        "provider_config_id": 1, "provider_name": "mem0", "enabled": True,
    }
    mock_provider_service.ingest = AsyncMock(
        return_value=MemoryIngestResult(provider="mem0", status="ok", message="success")
    )

    with patch.object(param_db_mod, "get_params", return_value={"plugin.name": "mem0"}), \
         patch.object(event_log_db, "insert_event_log", return_value=1) as m_log:
        await service.send_ingest(
            1, "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
        )
        m_log.assert_called_once()
        log_data = m_log.call_args.args[0]
        assert log_data["provider"] == "mem0"
        assert log_data["response_status"] == "ok"


@pytest.mark.asyncio
async def test_send_ingest_all_enabled_fanout(service, mock_config_service):
    mock_config_service.get_enabled_providers.return_value = [
        {"provider_config_id": 1},
        {"provider_config_id": 2},
    ]

    with patch.object(
        service, "send_ingest", new_callable=AsyncMock,
        return_value=MemoryIngestResult(provider="p", status="ok"),
    ) as m_send:
        results = await service.send_ingest_all_enabled(
            "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
        )
        assert len(results) == 2
        assert m_send.call_count == 2


@pytest.mark.asyncio
async def test_send_ingest_all_enabled_provider_failure_isolation(service, mock_config_service):
    mock_config_service.get_enabled_providers.return_value = [
        {"provider_config_id": 1},
        {"provider_config_id": 2},
    ]

    ok_result = MemoryIngestResult(provider="p2", status="ok")

    with patch.object(
        service, "send_ingest", new_callable=AsyncMock,
        side_effect=[Exception("fail"), ok_result],
    ):
        results = await service.send_ingest_all_enabled(
            "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
        )
        assert len(results) == 2
        assert results[0].status == "error"
        assert results[1].status == "ok"


@pytest.mark.asyncio
async def test_send_ingest_all_enabled_empty(service, mock_config_service):
    mock_config_service.get_enabled_providers.return_value = []
    results = await service.send_ingest_all_enabled(
        "t1", "u1", "a1", "c1", "memory_stored", "e1", [_make_unit()]
    )
    assert results == []


def test_filter_units_allowed_types():
    units = [
        _make_unit(event_id="1", unit_type="agent"),
        _make_unit(event_id="2", unit_type="user"),
        _make_unit(event_id="3", unit_type="unsupported"),
    ]
    filtered = MemoryIngestionEventService._filter_units(units, ["agent", "user"])
    assert len(filtered) == 2
    assert filtered[0].event_id == "1"
    assert filtered[1].event_id == "2"


def test_filter_units_excluded_types():
    units = [_make_unit(unit_type="excluded")]
    filtered = MemoryIngestionEventService._filter_units(units, ["agent"])
    assert filtered == []


def test_build_idempotency_key_format():
    key = MemoryIngestionEventService._build_idempotency_key(
        "t1", "a1", "u1", "c1", "memory_stored", "e1"
    )
    assert key == "nexent:t1:a1:u1:c1:memory_stored:e1"

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
