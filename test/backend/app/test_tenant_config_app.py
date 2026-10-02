import unittest
import json
import os
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
from unittest.mock import MagicMock
from http import HTTPStatus

# Add backend path to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, "../../../backend"))
sys.path.insert(0, backend_dir)

# Mock all external dependencies before any imports
database_client_mock = MagicMock()
database_client_mock.MinioClient = MagicMock()
database_client_mock.get_db_session = MagicMock()
database_client_mock.db_client = MagicMock()
sys.modules['database.client'] = database_client_mock

botocore_client_mock = MagicMock()
sys.modules['botocore.client'] = botocore_client_mock

sys.modules['database.tenant_config_db'] = MagicMock()

# Create mock functions
mock_get_current_user_id = MagicMock()
mock_get_selected_knowledge_list = MagicMock()
mock_update_selected_knowledge = MagicMock()

# Create mocked service modules
services_mock = MagicMock()
services_mock.get_selected_knowledge_list = mock_get_selected_knowledge_list
services_mock.update_selected_knowledge = mock_update_selected_knowledge

auth_mock = MagicMock()
auth_mock.get_current_user_id = mock_get_current_user_id

const_mock = MagicMock()
const_mock.DEPLOYMENT_VERSION = 'test_version'
const_mock.APP_VERSION = 'v1.2.3'
const_mock.ENABLE_AIDP_KNOWLEDGE = False

sys.modules['services.tenant_config_service'] = services_mock
sys.modules['utils.auth_utils'] = auth_mock
sys.modules['consts.const'] = const_mock

# Now import FastAPI components and the router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.tenant_config_app import router

# Import the module to directly replace functions
import apps.tenant_config_app as tenant_app

class TestTenantConfigApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Set up test client and mocks"""
        # Create FastAPI app and test client
        cls.app = FastAPI()
        cls.app.include_router(router)
        cls.client = TestClient(cls.app)

        # Store references to mocks for easy access
        cls.mock_get_user_id = mock_get_current_user_id
        cls.mock_get_knowledge_list = mock_get_selected_knowledge_list
        cls.mock_update_knowledge = mock_update_selected_knowledge

        # Replace functions in the imported module directly
        tenant_app.get_current_user_id = cls.mock_get_user_id
        tenant_app.get_selected_knowledge_list = cls.mock_get_knowledge_list
        tenant_app.update_selected_knowledge = cls.mock_update_knowledge

        # Set up default mock returns
        cls.mock_get_user_id.return_value = ("test_user", "test_tenant")
        cls.mock_get_knowledge_list.return_value = [
            {
                "index_name": "kb1",
                "embedding_model_name": "embedding-model-1",
                "knowledge_sources": ["source1", "source2"]
            },
            {
                "index_name": "kb2",
                "embedding_model_name": "embedding-model-2",
                "knowledge_sources": ["source3"]
            }
        ]
        cls.mock_update_knowledge.return_value = True

    def setUp(self):
        """Reset mocks before each test"""
        # Reset all mocks to default state
        self.mock_get_user_id.reset_mock()
        self.mock_get_knowledge_list.reset_mock()
        self.mock_update_knowledge.reset_mock()

        # Clear any side effects
        self.mock_get_user_id.side_effect = None
        self.mock_get_knowledge_list.side_effect = None
        self.mock_update_knowledge.side_effect = None

        # Set up default returns
        self.mock_get_user_id.return_value = ("test_user", "test_tenant")
        self.mock_get_knowledge_list.return_value = [
            {
                "index_name": "kb1",
                "embedding_model_name": "embedding-model-1",
                "knowledge_sources": ["source1", "source2"]
            },
            {
                "index_name": "kb2",
                "embedding_model_name": "embedding-model-2",
                "knowledge_sources": ["source3"]
            }
        ]
        self.mock_update_knowledge.return_value = True

    def test_get_deployment_version_success(self):
        """Test successful retrieval of deployment version"""
        response = self.client.get("/tenant_config/deployment_version")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("deployment_version", data)
        self.assertIn("app_version", data)
        self.assertIn("enable_aidp_knowledge", data)
        self.assertEqual(len(data.keys()), 4)


if __name__ == '__main__':
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
