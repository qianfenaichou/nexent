"""
Test LLM integration for knowledge base summarization
"""

import pytest
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import os
import types
from unittest.mock import patch, MagicMock

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

# Mock database.client and MinioClient before any imports to avoid MinIO initialization
class _MinioClient:
    pass

if "database.client" not in sys.modules:
    database_client_mod = types.ModuleType("database.client")
    database_client_mod.MinioClient = _MinioClient
    sys.modules["database.client"] = database_client_mod

# Mock backend.database.client as well
if "backend.database.client" not in sys.modules:
    backend_db_client_mod = types.ModuleType("backend.database.client")
    backend_db_client_mod.MinioClient = _MinioClient
    sys.modules["backend.database.client"] = backend_db_client_mod

# Ensure database module exists as a package (needs __path__ attribute)
if "database" not in sys.modules:
    database_mod = types.ModuleType("database")
    database_mod.__path__ = []  # Make it a package
    sys.modules["database"] = database_mod

# Mock database.model_management_db module to avoid MinIO initialization
if "database.model_management_db" not in sys.modules:
    model_mgmt_db_mod = types.ModuleType("database.model_management_db")
    model_mgmt_db_mod.get_model_by_model_id = MagicMock(return_value=None)
    sys.modules["database.model_management_db"] = model_mgmt_db_mod
    setattr(sys.modules["database"], "model_management_db", model_mgmt_db_mod)

# Mock database.tenant_config_db to avoid import errors
if "database.tenant_config_db" not in sys.modules:
    tenant_config_db_mod = types.ModuleType("database.tenant_config_db")
    # Mock all functions that config_utils imports
    tenant_config_db_mod.delete_config_by_tenant_config_id = MagicMock()
    tenant_config_db_mod.get_all_configs_by_tenant_id = MagicMock()
    tenant_config_db_mod.get_single_config_info = MagicMock()
    tenant_config_db_mod.insert_config = MagicMock()
    tenant_config_db_mod.update_config_by_tenant_config_id_and_data = MagicMock()
    sys.modules["database.tenant_config_db"] = tenant_config_db_mod
    setattr(sys.modules["database"], "tenant_config_db", tenant_config_db_mod)

from utils.document_vector_utils import summarize_document, summarize_cluster


class TestLLMIntegration:
    """Test LLM integration functionality"""
    
    def test_summarize_document_without_llm(self):
        """Test document summarization without LLM (fallback mode)"""
        content = "This is a test document with some content about machine learning and AI."
        filename = "test_doc.txt"
        
        result = summarize_document(content, filename, language="zh", max_words=50)
        
        # Should return placeholder when no model_id/tenant_id provided
        assert "[Document Summary: test_doc.txt]" in result
        assert "max 50 words" in result
        assert "Content:" in result
    
    def test_summarize_document_with_llm_params_no_config(self):
        """Test document summarization with LLM parameters but no model config"""
        content = "This is a test document with some content about machine learning and AI."
        filename = "test_doc.txt"
        
        # Mock get_model_by_model_id to return None (no config found)
        # Use the already mocked module and just ensure it returns None
        import database.model_management_db as model_mgmt_db
        model_mgmt_db.get_model_by_model_id = MagicMock(return_value=None)
        
        # Test with model_id and tenant_id but no actual LLM call (will fallback due to missing config)
        result = summarize_document(
            content, filename, language="zh", max_words=50, 
            model_id=1, tenant_id="test_tenant"
        )
        
        # Should return placeholder summary when model config not found (fallback behavior)
        assert "[Document Summary: test_doc.txt]" in result
        assert "max 50 words" in result
        assert "Content:" in result
    
    def test_summarize_cluster_without_llm(self):
        """Test cluster summarization without LLM (fallback mode)"""
        document_summaries = [
            "Document 1 is about machine learning algorithms.",
            "Document 2 discusses neural networks and deep learning.",
            "Document 3 covers AI applications in healthcare."
        ]
        
        result = summarize_cluster(document_summaries, language="zh", max_words=100)
        
        # Should return placeholder when no model_id/tenant_id provided
        assert "[Cluster Summary]" in result
        assert "max 100 words" in result
        assert "Based on 3 documents" in result
    
    def test_summarize_cluster_with_llm_params_no_config(self):
        """Test cluster summarization with LLM parameters but no model config"""
        document_summaries = [
            "Document 1 is about machine learning algorithms.",
            "Document 2 discusses neural networks and deep learning."
        ]
        
        # Mock get_model_by_model_id to return None (no config found)
        # Use the already mocked module and just ensure it returns None
        import database.model_management_db as model_mgmt_db
        model_mgmt_db.get_model_by_model_id = MagicMock(return_value=None)
        
        result = summarize_cluster(
            document_summaries, language="zh", max_words=100,
            model_id=1, tenant_id="test_tenant"
        )
        
        # Should return placeholder summary when model config not found (fallback behavior)
        assert "[Cluster Summary]" in result
        assert "max 100 words" in result
        assert "Based on 2 documents" in result
    
    def test_summarize_document_english(self):
        """Test document summarization in English"""
        content = "This is a test document with some content about machine learning and AI."
        filename = "test_doc.txt"
        
        result = summarize_document(content, filename, language="en", max_words=50)
        
        # Should return placeholder when no model_id/tenant_id provided
        assert "[Document Summary: test_doc.txt]" in result
        assert "max 50 words" in result
        assert "Content:" in result
    
    def test_summarize_cluster_english(self):
        """Test cluster summarization in English"""
        document_summaries = [
            "Document 1 is about machine learning algorithms.",
            "Document 2 discusses neural networks and deep learning."
        ]
        
        result = summarize_cluster(document_summaries, language="en", max_words=100)
        
        # Should return placeholder when no model_id/tenant_id provided
        assert "[Cluster Summary]" in result
        assert "max 100 words" in result
        assert "Based on 2 documents" in result

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
