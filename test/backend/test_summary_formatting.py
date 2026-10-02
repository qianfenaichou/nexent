"""
Test summary formatting and display
"""

import pytest
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import os
from unittest.mock import MagicMock, patch

# Mock consts module before patching backend.database.client to avoid ImportError
# backend.database.client imports from consts.const, so we need to mock it first
consts_mock = MagicMock()
consts_const_mock = MagicMock()
# Set required constants that backend.database.client might use
consts_const_mock.MINIO_ENDPOINT = "http://localhost:9000"
consts_const_mock.MINIO_ACCESS_KEY = "test_access_key"
consts_const_mock.MINIO_SECRET_KEY = "test_secret_key"
consts_const_mock.MINIO_REGION = "us-east-1"
consts_const_mock.MINIO_DEFAULT_BUCKET = "test-bucket"
consts_const_mock.POSTGRES_HOST = "localhost"
consts_const_mock.POSTGRES_USER = "test_user"
consts_const_mock.NEXENT_POSTGRES_PASSWORD = "test_password"
consts_const_mock.POSTGRES_DB = "test_db"
consts_const_mock.POSTGRES_PORT = 5432
consts_const_mock.LANGUAGE = {"ZH": "zh", "EN": "en"}
consts_const_mock.MESSAGE_ROLE = {"USER": "user", "ASSISTANT": "assistant", "SYSTEM": "system"}
consts_const_mock.THINK_START_PATTERN = "<think>"
consts_const_mock.THINK_END_PATTERN = "</think>"
consts_mock.const = consts_const_mock
# Mock consts.error_code and consts.exceptions
consts_error_code_mock = MagicMock()
consts_error_code_mock.ErrorCode = MagicMock()
consts_exceptions_mock = MagicMock()
consts_exceptions_mock.AppException = Exception
consts_prompt_template_mock = MagicMock()
consts_prompt_template_mock.PROMPT_GENERATE_TEMPLATE_FIELD_ALIAS_MAP = {
    "duty_system_prompt": "DUTY_SYSTEM_PROMPT",
    "constraint_system_prompt": "CONSTRAINT_SYSTEM_PROMPT",
    "few_shots_system_prompt": "FEW_SHOTS_SYSTEM_PROMPT",
    "agent_variable_name_system_prompt": "AGENT_VARIABLE_NAME_SYSTEM_PROMPT",
    "agent_display_name_system_prompt": "AGENT_DISPLAY_NAME_SYSTEM_PROMPT",
    "agent_description_system_prompt": "AGENT_DESCRIPTION_SYSTEM_PROMPT",
    "user_prompt": "USER_PROMPT",
    "agent_name_regenerate_system_prompt": "AGENT_NAME_REGENERATE_SYSTEM_PROMPT",
    "agent_name_regenerate_user_prompt": "AGENT_NAME_REGENERATE_USER_PROMPT",
    "agent_display_name_regenerate_system_prompt": "AGENT_DISPLAY_NAME_REGENERATE_SYSTEM_PROMPT",
    "agent_display_name_regenerate_user_prompt": "AGENT_DISPLAY_NAME_REGENERATE_USER_PROMPT",
}
consts_prompt_template_mock.PROMPT_GENERATE_TEMPLATE_FIELDS = tuple(
    consts_prompt_template_mock.PROMPT_GENERATE_TEMPLATE_FIELD_ALIAS_MAP.keys()
)
sys.modules['consts'] = consts_mock
sys.modules['consts.const'] = consts_const_mock
sys.modules['consts.error_code'] = consts_error_code_mock
sys.modules['consts.exceptions'] = consts_exceptions_mock
sys.modules['consts.prompt_template'] = consts_prompt_template_mock

# Add backend to path before patching backend modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

# Patch storage factory and MinIO config validation to avoid errors during initialization
# These patches must be started before any imports that use MinioClient
storage_client_mock = MagicMock()
minio_client_mock = MagicMock()
patch('nexent.storage.storage_client_factory.create_storage_client_from_config', return_value=storage_client_mock).start()
patch('nexent.storage.minio_config.MinIOStorageConfig.validate', lambda self: None).start()
patch('backend.database.client.MinioClient', return_value=minio_client_mock).start()

from utils.document_vector_utils import merge_cluster_summaries


class TestSummaryFormatting:
    """Test summary formatting functionality"""
    
    def test_merge_cluster_summaries_with_html_separators(self):
        """Test that cluster summaries are properly wrapped in HTML paragraph tags"""
        cluster_summaries = {
            0: "这是第一个簇的总结，包含关于机器学习和人工智能的内容。",
            1: "这是第二个簇的总结，包含关于深度学习和神经网络的内容。",
            2: "这是第三个簇的总结，包含关于自然语言处理的内容。"
        }
        
        result = merge_cluster_summaries(cluster_summaries)
        
        # Should contain HTML paragraph tags
        assert "<p>" in result
        assert "</p>" in result
        assert result.count("<p>") == 3  # Should have 3 paragraph tags for 3 clusters
        
        # Should contain all cluster summaries
        assert "第一个簇的总结" in result
        assert "第二个簇的总结" in result
        assert "第三个簇的总结" in result
        
        # Should be properly formatted with paragraph tags
        assert "<p>这是第一个簇的总结" in result
        assert "<p>这是第二个簇的总结" in result
        assert "<p>这是第三个簇的总结" in result
    
    def test_merge_cluster_summaries_single_cluster(self):
        """Test merging with single cluster (wrapped in paragraph tag)"""
        cluster_summaries = {
            0: "这是唯一的簇总结。"
        }
        
        result = merge_cluster_summaries(cluster_summaries)
        
        # Should be wrapped in paragraph tag
        assert "<p>" in result
        assert "</p>" in result
        assert result == "<p>这是唯一的簇总结。</p>"
    
    def test_merge_cluster_summaries_empty(self):
        """Test merging with empty input"""
        result = merge_cluster_summaries({})
        assert result == ""
    
    def test_merge_cluster_summaries_order(self):
        """Test that clusters are merged in correct order"""
        cluster_summaries = {
            2: "第三个簇",
            0: "第一个簇", 
            1: "第二个簇"
        }
        
        result = merge_cluster_summaries(cluster_summaries)
        
        # Should be in cluster ID order
        lines = result.split('\n')
        content_lines = [line for line in lines if line.strip() and '<p>' in line]
        
        assert "第一个簇" in content_lines[0]
        assert "第二个簇" in content_lines[1] 
        assert "第三个簇" in content_lines[2]

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
