"""
Unit tests for backend monitoring API endpoints.

Verifies that:
- _query_model_metrics_from_db does not filter by model_type
- list_models_endpoint does not accept a model_type query parameter
"""

import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "../../..")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BACKEND_ROOT = os.path.join(PROJECT_ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

storage_client_mock = MagicMock()
minio_client_mock = MagicMock()
patch(
    "nexent.storage.storage_client_factory.create_storage_client_from_config",
    return_value=storage_client_mock,
).start()
patch(
    "nexent.storage.minio_config.MinIOStorageConfig.validate", lambda self: None
).start()
patch("backend.database.client.MinioClient",
      return_value=minio_client_mock).start()


class TestQueryModelMetrics:
    """Verify _query_model_metrics_from_db does not filter by model_type."""

    @patch("apps.monitoring_app.get_monitoring_db_session")
    def test_sql_has_no_model_type_filter(self, mock_session_fn):
        """Generated SQL must not contain 'model_type' as a WHERE condition."""
        from apps.monitoring_app import _query_model_metrics_from_db

        mock_session = MagicMock()
        mock_session_fn.return_value.__enter__ = MagicMock(
            return_value=mock_session)
        mock_session_fn.return_value.__exit__ = MagicMock(return_value=None)
        mock_session.execute.return_value.fetchall.return_value = []

        _query_model_metrics_from_db("24h", tenant_id="t-1")

        call_args = mock_session.execute.call_args
        sql_text = str(call_args[0][0])

        assert "model_type" not in sql_text.lower().split("where")[
            1].split("group")[0]

    @patch("apps.monitoring_app.get_monitoring_db_session")
    def test_return_format(self, mock_session_fn):
        """Returned dicts contain expected keys with correct types."""
        from apps.monitoring_app import _query_model_metrics_from_db

        mock_row = MagicMock()
        mock_row.model_id = 1
        mock_row.model_name = "test-model"
        mock_row.model_type = "llm"
        mock_row.display_name = "Test Model"
        mock_row.request_count = 42
        mock_row.error_rate = 0.5
        mock_row.avg_duration = 120.3
        mock_row.avg_ttft = 50.1
        mock_row.token_generation_rate = 15.2
        mock_row.total_tokens = 1000

        mock_session = MagicMock()
        mock_session_fn.return_value.__enter__ = MagicMock(
            return_value=mock_session)
        mock_session_fn.return_value.__exit__ = MagicMock(return_value=None)
        mock_session.execute.return_value.fetchall.return_value = [mock_row]

        result = _query_model_metrics_from_db("24h", tenant_id="t-1")

        assert len(result) == 1
        record = result[0]
        assert record["model_name"] == "test-model"
        assert isinstance(record["error_rate"], float)
        assert isinstance(record["total_tokens"], int)


class TestListModelsEndpoint:
    """Verify list_models_endpoint does not accept model_type parameter."""

    @pytest.fixture
    def client(self, mocker):
        mocker.patch("boto3.client")
        mocker.patch("backend.database.client.MinioClient")

        import types

        if "management.services.knowledge_base.service" not in sys.modules:
            mod = types.ModuleType("management.services.knowledge_base.service")
            mod.get_vector_db_core = lambda: object()
            sys.modules["management.services.knowledge_base.service"] = mod

        from apps.monitoring_app import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_endpoint_signature_has_no_model_type(self):
        """The endpoint function must not declare a model_type Query parameter."""
        from apps.monitoring_app import list_models_endpoint

        import inspect

        sig = inspect.signature(list_models_endpoint)
        assert "model_type" not in sig.parameters

    @patch("apps.monitoring_app._query_model_metrics_from_db", return_value=[])
    @patch("apps.monitoring_app.get_current_user_id", return_value=("u-1", "t-1"))
    def test_endpoint_returns_success(self, mock_auth, mock_query, client):
        """GET /monitoring/models returns code 0 on success."""
        response = client.get(
            "/monitoring/models",
            params={"time_range": "24h"},
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0

    @patch("apps.monitoring_app._query_model_metrics_from_db", return_value=[])
    @patch("apps.monitoring_app.get_current_user_id", return_value=("u-1", "t-1"))
    def test_endpoint_returns_empty_data(self, mock_auth, mock_query, client):
        response = client.get(
            "/monitoring/models",
            params={"time_range": "24h"},
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"] == []

    @patch("apps.monitoring_app._query_model_metrics_from_db", side_effect=Exception("db down"))
    @patch("apps.monitoring_app.get_current_user_id", return_value=("u-1", "t-1"))
    def test_endpoint_returns_500_on_exception(self, mock_auth, mock_query, client):
        response = client.get(
            "/monitoring/models",
            params={"time_range": "24h"},
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 500


class TestMonitoringStatus:
    """Verify monitoring status endpoint used by the frontend top bar."""

    def test_dashboard_url_comes_from_configuration(self, monkeypatch):
        from apps.monitoring_app import get_monitoring_status

        monkeypatch.setattr("apps.monitoring_app.ENABLE_TELEMETRY", True)
        monkeypatch.setattr("apps.monitoring_app.MONITORING_PROVIDER", "grafana")
        monkeypatch.setattr(
            "apps.monitoring_app.MONITORING_DASHBOARD_URL",
            "http://localhost:3002/d/nexent-llm-agent/nexent-agent-trace-monitoring?orgId=1",
        )

        status = get_monitoring_status()

        assert status["telemetry_enabled"] is True
        assert status["provider"] == "grafana"
        assert (
            status["dashboard_url"]
            == "http://localhost:3002/d/nexent-llm-agent/nexent-agent-trace-monitoring?orgId=1"
        )
        assert status["dashboard_port"] is None
        assert status["dashboard_path"] is None

    def test_otlp_provider_status_has_no_ui(self, monkeypatch):
        from apps.monitoring_app import get_monitoring_status

        monkeypatch.setattr("apps.monitoring_app.ENABLE_TELEMETRY", True)
        monkeypatch.setattr("apps.monitoring_app.MONITORING_PROVIDER", "otlp")
        monkeypatch.setattr("apps.monitoring_app.MONITORING_DASHBOARD_URL", "")

        status = get_monitoring_status()

        assert status["telemetry_enabled"] is True
        assert status["dashboard_url"] is None
        assert status["dashboard_port"] is None
        assert status["dashboard_path"] is None

    def test_zipkin_provider_status_uses_configured_url(self, monkeypatch):
        from apps.monitoring_app import get_monitoring_status

        monkeypatch.setattr("apps.monitoring_app.ENABLE_TELEMETRY", True)
        monkeypatch.setattr("apps.monitoring_app.MONITORING_PROVIDER", "zipkin")
        monkeypatch.setattr(
            "apps.monitoring_app.MONITORING_DASHBOARD_URL",
            "http://localhost:9411",
        )

        status = get_monitoring_status()

        assert status["telemetry_enabled"] is True
        assert status["provider"] == "zipkin"
        assert status["dashboard_url"] == "http://localhost:9411"
        assert status["dashboard_port"] is None
        assert status["dashboard_path"] is None

    def test_langsmith_provider_status_has_no_local_ui(self, monkeypatch):
        from apps.monitoring_app import get_monitoring_status

        monkeypatch.setattr("apps.monitoring_app.ENABLE_TELEMETRY", True)
        monkeypatch.setattr("apps.monitoring_app.MONITORING_PROVIDER", "langsmith")
        monkeypatch.setattr("apps.monitoring_app.MONITORING_DASHBOARD_URL", "")

        status = get_monitoring_status()

        assert status["telemetry_enabled"] is True
        assert status["provider"] == "langsmith"
        assert status["dashboard_url"] is None
        assert status["dashboard_port"] is None
        assert status["dashboard_path"] is None

    def test_unsupported_provider_has_no_ui(self, monkeypatch):
        from apps.monitoring_app import get_monitoring_status

        monkeypatch.setattr("apps.monitoring_app.ENABLE_TELEMETRY", True)
        monkeypatch.setattr("apps.monitoring_app.MONITORING_PROVIDER", "unsupported")
        monkeypatch.setattr("apps.monitoring_app.MONITORING_DASHBOARD_URL", "")

        status = get_monitoring_status()

        assert status["provider"] == "unsupported"
        assert status["dashboard_url"] is None
        assert status["dashboard_port"] is None
        assert status["dashboard_path"] is None

    def test_status_endpoint_returns_success(self, monkeypatch):
        from apps.monitoring_app import router

        monkeypatch.setattr("apps.monitoring_app.ENABLE_TELEMETRY", True)
        monkeypatch.setattr("apps.monitoring_app.MONITORING_PROVIDER", "phoenix")
        monkeypatch.setattr(
            "apps.monitoring_app.MONITORING_DASHBOARD_URL",
            "http://localhost:6006",
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        response = client.get("/monitoring/status")

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["dashboard_url"] == "http://localhost:6006"

    @patch("apps.monitoring_app.get_current_user_id")
    def test_endpoint_returns_401_on_token_expired(self, mock_auth):
        """Expired token maps to 401 for /monitoring/models."""
        from apps.monitoring_app import router
        from consts.exceptions import TokenExpiredError

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_auth.side_effect = TokenExpiredError("expired")
        response = client.get(
            "/monitoring/models",
            params={"time_range": "24h"},
            headers={"Authorization": "Bearer expired"},
        )
        assert response.status_code == 401

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
