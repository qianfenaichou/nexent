import os
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import unittest
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

test_dir = os.path.dirname(__file__)
backend_dir = os.path.abspath(os.path.join(test_dir, "../../../backend"))
sys.path.insert(0, backend_dir)


class _CasAuthenticationError(Exception):
    pass


_MODULES_TO_RESTORE = ["services.cas_service"]
_ORIGINAL_MODULES = {name: sys.modules.get(name) for name in _MODULES_TO_RESTORE}

cas_service_mock = MagicMock()
cas_service_mock.CAS_SERVER_URL = "https://cas.example.com"
cas_service_mock.CasAuthenticationError = _CasAuthenticationError
cas_service_mock.get_cas_config = MagicMock(
    return_value={
        "enabled": True,
        "login_mode": "button",
        "heartbeat_url": "https://cas.example.com/heartbeat",
        "heartbeat_interval_seconds": 300,
        "heartbeat_cookie_name": "AUTH_TOKEN",
        "renew_before_seconds": 300,
        "renew_timeout_seconds": 10,
        "display_name": "CAS",
    }
)
cas_service_mock.build_login_url = MagicMock(return_value="https://cas.example.com/login?service=x")
cas_service_mock.build_renew_url = MagicMock(return_value="https://cas.example.com/login?gateway=true")
cas_service_mock.login_with_ticket = AsyncMock(
    return_value={
        "user": {"id": "user-1", "email": "u@example.com", "role": "USER"},
        "session": {"access_token": "jwt", "expires_at": 1779780000, "expires_in_seconds": 3600},
        "redirect_url": "/chat",
    }
)
cas_service_mock.renew_with_ticket = AsyncMock(
    return_value={
        "user": {"id": "user-1", "email": "u@example.com", "role": "USER"},
        "session": {"access_token": "jwt2", "expires_at": 1779780300, "expires_in_seconds": 3600},
        "redirect_url": "/",
        "renew": True,
    }
)
cas_service_mock.revoke_from_logout_request = MagicMock(
    return_value={"revoked": 1, "cas_user_id": "cas-user-1", "session_index": "ST-1"}
)
sys.modules["services.cas_service"] = cas_service_mock

from apps.cas_app import router  # noqa: E402

for _name, _module in _ORIGINAL_MODULES.items():
    if _module is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _module

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestCasApp(unittest.TestCase):
    def tearDown(self):
        cas_service_mock.build_login_url.side_effect = None
        cas_service_mock.build_login_url.return_value = "https://cas.example.com/login?service=x"
        cas_service_mock.build_renew_url.side_effect = None
        cas_service_mock.build_renew_url.return_value = "https://cas.example.com/login?gateway=true"
        cas_service_mock.login_with_ticket.side_effect = None
        cas_service_mock.revoke_from_logout_request.reset_mock()

    def test_config_returns_public_cas_settings(self):
        response = client.get("/user/cas/config")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["message"], "success")
        self.assertTrue(data["data"]["enabled"])
        self.assertEqual(data["data"]["login_mode"], "button")
        self.assertEqual(data["data"]["heartbeat_url"], "https://cas.example.com/heartbeat")
        self.assertEqual(data["data"]["heartbeat_interval_seconds"], 300)
        self.assertEqual(data["data"]["heartbeat_cookie_name"], "AUTH_TOKEN")

    def test_login_redirects_to_cas_server(self):
        response = client.get("/user/cas/login?redirect=/chat", follow_redirects=False)

        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertEqual(response.headers["location"], "https://cas.example.com/login?service=x")
        cas_service_mock.build_login_url.assert_called_with("/chat")

    def test_login_returns_400_when_cas_not_configured(self):
        cas_service_mock.build_login_url.side_effect = _CasAuthenticationError("CAS is not configured")

        response = client.get("/user/cas/login")

        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.json()["detail"], "CAS login is not available")
        self.assertNotIn("CAS is not configured", response.text)

    def test_login_rejects_redirect_url_outside_configured_cas_server(self):
        cas_service_mock.build_login_url.return_value = "https://evil.example.com/login?service=x"

        response = client.get("/user/cas/login?redirect=/chat", follow_redirects=False)

        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.json()["detail"], "CAS login is not available")

    def test_callback_returns_session_payload(self):
        response = client.get("/user/cas/callback?ticket=ST-1&redirect=/chat")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["message"], "CAS login successful")
        self.assertEqual(data["data"]["session"]["access_token"], "jwt")
        cas_service_mock.login_with_ticket.assert_awaited()

    def test_callback_returns_401_for_invalid_ticket(self):
        cas_service_mock.login_with_ticket.side_effect = _CasAuthenticationError("bad ticket")

        response = client.get("/user/cas/callback?ticket=bad")

        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(response.json()["detail"], "CAS authentication failed")
        self.assertNotIn("bad ticket", response.text)

    def test_renew_does_not_expose_cas_configuration_exception(self):
        cas_service_mock.build_renew_url.side_effect = _CasAuthenticationError("internal CAS config path")

        response = client.get("/user/cas/renew")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIn("cas-renew-failed", response.text)
        self.assertIn("CAS renew failed", response.text)
        self.assertNotIn("internal CAS config path", response.text)

    def test_renew_callback_without_ticket_posts_failure_to_iframe_parent(self):
        response = client.get("/user/cas/renew_callback")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("cas-renew-failed", response.text)

    def test_logout_callback_accepts_cas_form_body(self):
        xml = """
        <samlp:LogoutRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
          xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
          <saml:NameID>cas-user-1</saml:NameID>
          <samlp:SessionIndex>ST-1</samlp:SessionIndex>
        </samlp:LogoutRequest>
        """

        response = client.post(
            "/user/cas/logout_callback",
            data={"logoutRequest": xml},
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json()["data"]["revoked"], 1)
        cas_service_mock.revoke_from_logout_request.assert_called_once_with(xml)

    def test_callback_post_accepts_cas_single_logout_request(self):
        xml = """
        <samlp:LogoutRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
          xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
          <saml:NameID>cas-user-1</saml:NameID>
          <samlp:SessionIndex>ST-1</samlp:SessionIndex>
        </samlp:LogoutRequest>
        """

        response = client.post(
            "/user/cas/callback",
            data={"logoutRequest": xml},
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json()["data"]["revoked"], 1)
        cas_service_mock.revoke_from_logout_request.assert_called_once_with(xml)


if __name__ == "__main__":
    unittest.main()

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
