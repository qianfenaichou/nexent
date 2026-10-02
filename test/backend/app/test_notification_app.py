"""Unit tests for apps.notification_app."""
import os
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "../../../backend"))
sys.path.insert(0, backend_dir)

sys.modules.setdefault("services.notification_service", MagicMock())
sys.modules.setdefault("utils.auth_utils", MagicMock())

from apps.notification_app import router
from consts.exceptions import NotFoundException, UnauthorizedError

app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestListNotifications:
    def test_list_notifications_success(self, mocker):
        mock_result = {
            "items": [{"receiver_id": 1, "event_type": "repository_review_approved"}],
            "pagination": {"page": 1, "page_size": 10, "total": 1, "total_pages": 1},
        }
        mock_user = mocker.patch("apps.notification_app.get_current_user_id")
        mock_list = mocker.patch("apps.notification_app.list_notifications")
        mock_user.return_value = ("user-1", "tenant-a")
        mock_list.return_value = mock_result

        response = client.get("/notifications?only_unread=true&page=1&page_size=10")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == {"message": "OK", "data": mock_result}
        mock_list.assert_called_once_with(
            "user-1",
            only_unread=True,
            page=1,
            page_size=10,
        )

    def test_list_notifications_unauthorized(self, mocker):
        mock_user = mocker.patch("apps.notification_app.get_current_user_id")
        mock_user.side_effect = UnauthorizedError("bad token")

        response = client.get("/notifications")

        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert "bad token" in response.json()["detail"]


class TestMarkNotificationsRead:
    def test_mark_read_success(self, mocker):
        mock_user = mocker.patch("apps.notification_app.get_current_user_id")
        mock_mark = mocker.patch("apps.notification_app.mark_notifications_read")
        mock_user.return_value = ("user-1", "tenant-a")
        mock_mark.return_value = {"updated_count": 1}

        response = client.post(
            "/notifications/read",
            json={"mark_all": False, "receiver_id": 11},
        )

        assert response.status_code == HTTPStatus.OK
        assert response.json() == {"message": "OK", "data": {"updated_count": 1}}
        mock_mark.assert_called_once_with(
            "user-1",
            mark_all=False,
            receiver_id=11,
        )

    def test_mark_read_unauthorized(self, mocker):
        mock_user = mocker.patch("apps.notification_app.get_current_user_id")
        mock_user.side_effect = UnauthorizedError("bad token")

        response = client.post(
            "/notifications/read",
            json={"mark_all": True},
        )

        assert response.status_code == HTTPStatus.UNAUTHORIZED

    def test_mark_read_not_found(self, mocker):
        mock_user = mocker.patch("apps.notification_app.get_current_user_id")
        mock_mark = mocker.patch("apps.notification_app.mark_notifications_read")
        mock_user.return_value = ("user-1", "tenant-a")
        mock_mark.side_effect = NotFoundException("Notification receiver 99 not found")

        response = client.post(
            "/notifications/read",
            json={"receiver_id": 99},
        )

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert "99" in response.json()["detail"]

    def test_mark_read_bad_request(self, mocker):
        mock_user = mocker.patch("apps.notification_app.get_current_user_id")
        mock_mark = mocker.patch("apps.notification_app.mark_notifications_read")
        mock_user.return_value = ("user-1", "tenant-a")
        mock_mark.side_effect = ValueError("receiver_id is required when mark_all is false")

        response = client.post(
            "/notifications/read",
            json={"mark_all": False},
        )

        assert response.status_code == HTTPStatus.BAD_REQUEST

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
