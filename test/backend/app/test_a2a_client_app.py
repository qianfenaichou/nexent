"""Unit tests for backend.apps.a2a_client_app runtime metadata chat flow."""
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

# =============================================================================
# Stub heavy service / auth modules BEFORE importing the app module, following
# the repository's established sys.modules pattern (see test_agent_db.py).
# The exception classes must be REAL so that `except AgentCallError` clauses
# in the app behave correctly.
# =============================================================================


class _AgentCallError(Exception):
    pass


class _AgentDiscoveryError(Exception):
    pass


client_service_mock = MagicMock()
client_service_mock.a2a_client_service = MagicMock()
client_service_mock.AgentCallError = _AgentCallError
client_service_mock.AgentDiscoveryError = _AgentDiscoveryError
sys.modules["services.a2a_client_service"] = client_service_mock

server_service_mock = MagicMock()
server_service_mock.a2a_server_service = MagicMock()
sys.modules["services.a2a_server_service"] = server_service_mock

sys.modules["database.a2a_agent_db"] = MagicMock()

auth_utils_mock = MagicMock()
auth_utils_mock.get_current_user_info = MagicMock(return_value=("user_1", "tenant_1", None))
sys.modules["utils.auth_utils"] = auth_utils_mock
sys.modules["backend.utils.auth_utils"] = auth_utils_mock

from consts.error_code import RuntimeMetadataValidationCode  # noqa: E402
from consts.exceptions import (  # noqa: E402
    AppException,
    RuntimeMetadataValidationError,
)

from apps.a2a_client_app import router  # noqa: E402

app = FastAPI()
app.include_router(router)

@app.exception_handler(AppException)
async def _app_exception_handler(_request, exc):
    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_dict(),
    )

client = TestClient(app)

CHAT_URL = "/a2a/client/agents/7/chat"


@pytest.fixture(autouse=True)
def _patch_auth():
    """Stub identity resolution so tests focus on the chat endpoint logic."""
    with patch("apps.a2a_client_app.get_current_user_info", return_value=("user_1", "tenant_1", None)):
        yield


@pytest.fixture(autouse=True)
def _mock_call_agent():
    """Default successful external agent call."""
    with patch(
        "apps.a2a_client_app.a2a_client_service.call_agent",
        new=AsyncMock(return_value={"reply": "hello"}),
    ) as mock_call:
        yield mock_call


def _chat(payload=None, **kwargs):
    return client.post(
        CHAT_URL,
        json={"message": "hello", **(payload or {})},
        **kwargs,
    )


# =============================================================================
# Runtime metadata: validation pass / fail paths
# =============================================================================

def test_chat_without_metadata_omits_metadata_key(_mock_call_agent):
    """metadata=None must skip validation and omit the message metadata key."""
    resp = _chat()
    assert resp.status_code == 200

    called = _mock_call_agent.await_args
    assert called is not None
    message = called.kwargs["message"]
    assert "metadata" not in message
    assert _mock_call_agent.await_args.kwargs["external_agent_id"] == 7
    assert _mock_call_agent.await_args.kwargs["tenant_id"] == "tenant_1"


def test_chat_with_metadata_embeds_metadata(_mock_call_agent):
    """Valid metadata must be embedded into the A2A message and forwarded."""
    metadata = {"session_id": "s-1", "language": "zh"}
    resp = _chat({"metadata": metadata})
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "data": {"reply": "hello"}}

    message = _mock_call_agent.await_args.kwargs["message"]
    assert message["metadata"] == metadata


def test_chat_metadata_too_large_returns_413(_mock_call_agent):
    """METADATA_TOO_LARGE validation errors map to HTTP 413."""
    with patch(
        "apps.a2a_client_app.validate_runtime_metadata",
        side_effect=RuntimeMetadataValidationError(
            RuntimeMetadataValidationCode.METADATA_TOO_LARGE,
            "too large",
        ),
    ):
        resp = _chat({"metadata": {"big": "x"}})
    assert resp.status_code == 413
    _mock_call_agent.assert_not_awaited()
    assert resp.json()["code"] == "010107"
    assert resp.json()["message"] == "Runtime metadata exceeds the maximum allowed size."
    assert resp.json()["details"] == {"reason": "METADATA_TOO_LARGE"}


def test_chat_metadata_invalid_returns_422(_mock_call_agent):
    """Other validation errors map to HTTP 422."""
    with patch(
        "apps.a2a_client_app.validate_runtime_metadata",
        side_effect=RuntimeMetadataValidationError(
            RuntimeMetadataValidationCode.INVALID_METADATA_TYPE,
            "must be object",
        ),
    ):
        resp = _chat({"metadata": {"invalid": "value"}})
    assert resp.status_code == 422
    _mock_call_agent.assert_not_awaited()
    assert resp.json()["code"] == "010106"
    assert resp.json()["message"] == "Runtime metadata is invalid."
    assert resp.json()["details"] == {"reason": "INVALID_METADATA_TYPE"}


def test_chat_metadata_real_validator_too_large_returns_413(_mock_call_agent):
    """A genuinely oversized payload is rejected by the real validator as 413."""
    oversized = {"payload": "x" * (64 * 1024 + 1)}
    resp = _chat({"metadata": oversized})
    assert resp.status_code == 413
    _mock_call_agent.assert_not_awaited()


def test_chat_metadata_real_validator_invalid_type_returns_422(_mock_call_agent):
    """A non-object metadata payload is rejected by the real validator as 422."""
    resp = _chat({"metadata": ["not", "an", "object"]})
    assert resp.status_code == 422
    _mock_call_agent.assert_not_awaited()


# =============================================================================
# Pre-existing chat flow behavior (regression guards)
# =============================================================================

def test_chat_empty_message_returns_400():
    """Empty or whitespace-only messages must be rejected."""
    resp = _chat({"message": "   "})
    assert resp.status_code == 400


def test_chat_agent_call_error_returns_400():
    """AgentCallError is mapped to HTTP 400."""
    with patch(
        "apps.a2a_client_app.a2a_client_service.call_agent",
        new=AsyncMock(side_effect=_AgentCallError("boom")),
    ):
        resp = _chat()
    assert resp.status_code == 400


def test_chat_agent_discovery_error_returns_404():
    """AgentDiscoveryError is mapped to HTTP 404."""
    with patch(
        "apps.a2a_client_app.a2a_client_service.call_agent",
        new=AsyncMock(side_effect=_AgentDiscoveryError("not found")),
    ):
        resp = _chat()
    assert resp.status_code == 404


def test_chat_generic_error_returns_500():
    """Unexpected errors are mapped to HTTP 500."""
    with patch(
        "apps.a2a_client_app.a2a_client_service.call_agent",
        new=AsyncMock(side_effect=RuntimeError("unexpected")),
    ):
        resp = _chat()
    assert resp.status_code == 500

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
