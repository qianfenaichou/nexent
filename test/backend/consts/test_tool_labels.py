"""Tests for the built-in tool labels constants module."""

import importlib
import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)

import pytest


def _load_builtin_label_map():
    """Load the real BUILTIN_LABEL_MAP even if consts has been mocked."""
    # Ensure the real consts.tool_labels module is loaded
    for key in list(sys.modules):
        if key.startswith('consts'):
            del sys.modules[key]
    from consts.tool_labels import BUILTIN_LABEL_MAP
    return BUILTIN_LABEL_MAP


class TestBuiltinLabelMap:
    """Verify the BUILTIN_LABEL_MAP integrity."""

    def test_builtin_label_map_is_dict(self):
        """BUILTIN_LABEL_MAP is a non-empty dict."""
        m = _load_builtin_label_map()
        assert isinstance(m, dict)
        assert len(m) > 0

    def test_builtin_label_map_has_expected_categories(self):
        """BUILTIN_LABEL_MAP contains expected tool categories."""
        m = _load_builtin_label_map()
        assert m["mysql_database"] == ["database"]
        assert m["read_file"] == ["file"]
        assert m["tavily_search"] == ["search"]
        assert m["send_email"] == ["email"]
        assert m["terminal"] == ["terminal"]

    def test_builtin_label_map_labels_are_lists(self):
        """Every value in BUILTIN_LABEL_MAP is a list of strings."""
        m = _load_builtin_label_map()
        for tool_name, labels in m.items():
            assert isinstance(tool_name, str)
            assert isinstance(labels, list)
            for label in labels:
                assert isinstance(label, str)

    def test_keep_in_sync_reference_in_docstring(self):
        """Docstring references the correct migration SQL path."""
        for key in list(sys.modules):
            if key.startswith('consts'):
                del sys.modules[key]
        import consts.tool_labels
        doc = consts.tool_labels.__doc__
        assert doc is not None
        assert "deploy/sql/migrations/v2.3_merged_migrations.sql" in doc

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
