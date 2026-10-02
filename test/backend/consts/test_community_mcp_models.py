"""Unit tests for MCP community request models and content validators."""

import sys
_STUB_MODULE_SNAPSHOT = dict(sys.modules)
import types
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

# Stub optional SDK deps and agent model imports used by consts.model.
sys.modules.setdefault("boto3", MagicMock())
sys.modules.setdefault("botocore", MagicMock())
sys.modules.setdefault("botocore.client", MagicMock())
sys.modules.setdefault("botocore.exceptions", MagicMock())

_agent_model_mod = types.ModuleType("nexent.core.agents.agent_model")
_agent_model_mod.AgentVerificationConfig = MagicMock()
_agent_model_mod.ToolConfig = MagicMock()
sys.modules.setdefault("nexent", MagicMock())
sys.modules.setdefault("nexent.core", MagicMock())
sys.modules.setdefault("nexent.core.agents", MagicMock())
sys.modules["nexent.core.agents.agent_model"] = _agent_model_mod

from backend.consts.model import (
    CommunityPublishRequest,
    CommunityReviewActionRequest,
    CommunityStatusUpdateRequest,
    CommunityUpdateRequest,
    SkillRepositoryListingCreateRequest,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  looks good  ", "looks good"),
        ("   ", None),
        ("", None),
        (None, None),
    ],
)
def test_community_review_action_content_validator(raw, expected):
    req = CommunityReviewActionRequest(review_id=1, content=raw)
    assert req.content == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  needs fixes  ", "needs fixes"),
        ("   ", None),
        ("", None),
        (None, None),
    ],
)
def test_community_status_update_content_validator(raw, expected):
    req = CommunityStatusUpdateRequest(status="rejected", content=raw)
    assert req.content == expected


def test_community_publish_request_strips_content():
    req = CommunityPublishRequest(mcp_id=1, content="  listing note  ")
    assert req.content == "listing note"

    blank = CommunityPublishRequest(mcp_id=1, content="   ")
    assert blank.content is None

    omitted = CommunityPublishRequest(mcp_id=1, content=None, name=None)
    assert omitted.content is None
    assert omitted.name is None


def test_community_update_request_strips_content():
    req = CommunityUpdateRequest(market_id=1, content="  resubmit note  ")
    assert req.content == "resubmit note"

    blank = CommunityUpdateRequest(market_id=1, content="")
    assert blank.content is None

    omitted = CommunityUpdateRequest(market_id=1, content=None, description=None)
    assert omitted.content is None
    assert omitted.description is None


def test_skill_repository_listing_create_request_accepts_content():
    req = SkillRepositoryListingCreateRequest(content="please review", icon="🔧")
    assert req.content == "please review"
    assert req.icon == "🔧"


def test_community_review_action_requires_positive_review_id():
    with pytest.raises(ValidationError):
        CommunityReviewActionRequest(review_id=0, content="x")

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
