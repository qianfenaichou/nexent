"""T-08 wiring guardrails.

Layer 1 (always runs) - the wiring that T-05/T-07 deferred to T-08:
1. ``knowevo_router`` is mounted in both the config and runtime apps and
   resolves at ``/api/knowevo/...`` (create_app's root_path supplies the
   ``/api`` prefix; the router itself must NOT repeat it - the T-05
   ``prefix="/api/knowevo"`` bug produced ``/api/api/knowevo/...`` and
   every request 404'd).
2. The same five endpoints are reachable, answering 403/405 (auth and
   method gates) rather than 404.
3. ``kg_search``/``kg_stats`` are mounted on the Local MCP service.

No database: endpoints are only probed for routing, so the auth failure
(403) is the expected proof that a route exists.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

# (path, method) - the routing proof is "not 404". The exact status
# (403 auth / 405 wrong method / 500 unpatched auth path) depends on the
# test environment, which is not what this guardrail checks.
KNOWEVO_ROUTES = [
    ("/api/knowevo/ontology/proposals", "get"),
    ("/api/knowevo/ontology/proposals/review", "post"),
    ("/api/knowevo/ontology/versions", "post"),
    ("/api/knowevo/ontology/versions/v1/metrics", "get"),
    ("/api/knowevo/ontology/diff?from=v0&to=v1", "get"),
]


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path,method", KNOWEVO_ROUTES)
def test_knowevo_routes_mounted_in_config_app(path, method):
    from apps.config_app import app

    response = getattr(_client(app), method)(path)
    assert response.status_code != 404, (
        f"{method.upper()} {path} -> 404; the router prefix is doubled "
        "(T-05 bug: '/api' repeated) or the router is unmounted")


@pytest.mark.parametrize("path,method", KNOWEVO_ROUTES[:1] + KNOWEVO_ROUTES[3:])
def test_knowevo_routes_mounted_in_runtime_app(path, method):
    from apps.runtime_app import app

    response = getattr(_client(app), method)(path)
    assert response.status_code != 404, (
        f"{method.upper()} {path} -> 404; the router prefix is doubled "
        "or the router is unmounted")


def test_router_prefix_is_not_doubled():
    """The app prefix is supplied by create_app(root_path="/api"); the
    router must stay bare or every endpoint resolves under /api/api."""
    from apps.knowledge_graph_app import router

    assert router.prefix == "/knowevo"


def test_kg_tools_mounted_on_local_mcp():
    from tool_collection.mcp.local_mcp_service import local_mcp_service

    mounted = {
        ms.prefix: ms for ms in local_mcp_service._mounted_servers
    }
    assert "knowevo" in mounted
    names = set(mounted["knowevo"].server._tool_manager._tools)
    assert {"kg_search", "kg_stats"} <= names