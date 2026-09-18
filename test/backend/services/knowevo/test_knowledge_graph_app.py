"""
Tests for apps/knowledge_graph_app.py + the T-05 service additions in
services/knowevo/ontology_service.py (T-05a).

Layer 1 (always runs): endpoint behavior with the auth seam and the store
seam monkeypatched - happy paths, 401 (expired session), 403 (missing
workbench permission), 422 (bad action / reparent without new_parent),
404 (unknown proposal / unknown version), the 40-per-session page cap,
score-ordered queue, review batch state transitions, reparent cycle
refusal, commit_from_queue -> semver bump -> diff round trip, and tenant
isolation between two FakeReviewStore tenants.

No database and no HTTP server: endpoints are called as plain async
functions (same style as test_memory_dreaming_app.py) so the app layer
stays the only thing under test; the service logic is exercised through
the same calls.
"""
import asyncio
import os
import sys
import uuid as uuid_mod
from pathlib import Path

import pytest

# Upstream convention (test/backend/services/knowevo/test_ontology_service.py):
# backend root on sys.path; never add __init__.py under the test tree.
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import HTTPException

from apps import knowledge_graph_app
from services.knowevo.ontology_service import OntologyService

ADMIN = "admin@knowevo.com"
TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


class FakeReviewStore:
    """In-memory stand-in for PgStore's T-05 queue/version surface."""

    def __init__(self):
        self.proposals = {}   # id -> row dict
        self.versions = []    # committed version rows
        self.snapshots = {}  # tenant -> active snapshot

    # queue surface -----------------------------------------------------
    async def list_queue_rows(self, tenant_id, round_id=None, status="pending"):
        rows = []
        for r in self.proposals.values():
            if r["tenant_id"] != tenant_id:
                continue
            if status and r["status"] != status:
                continue
            if round_id is not None and str(r["round_id"]) != str(round_id):
                continue
            rows.append({k: v for k, v in r.items() if k != "tenant_id"})
        return rows

    async def get_proposal_rows(self, tenant_id, ids):
        found = {}
        for i in ids:
            r = self.proposals.get(i)
            if r and r["tenant_id"] == tenant_id:
                found[i] = {k: v for k, v in r.items()
                            if k in ("id", "target", "op", "payload", "status")}
        missing = [i for i in ids if i not in found]
        return found, missing

    async def set_proposal_status(self, tenant_id, ids, status,
                                  reviewed_by=None, reject_reason=None,
                                  new_parent=None):
        n = 0
        for i in ids:
            r = self.proposals.get(i)
            if not r or r["tenant_id"] != tenant_id:
                continue
            r["status"] = status
            r["reviewed_by"] = reviewed_by
            r["reject_reason"] = reject_reason
            if new_parent and r["op"] == "CLS_ADD":
                payload = dict(r.get("payload") or {})
                payload["parent"] = new_parent
                r["payload"] = payload
            n += 1
        return n

    # version surface ---------------------------------------------------
    async def load_active_snapshot(self, tenant_id):
        return self.snapshots.get(tenant_id, {"classes": [], "rel_types": []})

    async def save_version(self, tenant_id, version_row):
        self.versions.append(dict(version_row))
        self.snapshots[tenant_id] = version_row["snapshot"]
        return version_row

    async def list_versions(self, tenant_id):
        return [{"version": v["version"], "status": "published",
                 "applied_ops": v["applied_ops"], "snapshot": v["snapshot"]}
                for v in self.versions]

    async def get_version_row(self, tenant_id, version):
        for v in self.versions:
            if v["version"] == version:
                return {"version": v["version"], "metrics": v.get("metrics"),
                        "snapshot": v["snapshot"],
                        "applied_ops": v["applied_ops"]}
        return None

    async def load_active_version_row(self, tenant_id):
        """Newest committed version row, or None (T-18a active endpoint)."""
        if not self.versions:
            return None
        v = self.versions[-1]
        return {"version": v["version"], "status": "published",
                "snapshot": v["snapshot"], "applied_ops": v["applied_ops"],
                "metrics": v.get("metrics"), "created_at": None}


def _seed_proposal(store, tenant_id=TENANT_A, target="cls:Metformin",
                   op="CLS_ADD", name="Metformin", parent=None,
                   confidence=0.9, impact=6, novelty=0.3, status="pending"):
    pid = str(uuid_mod.uuid4())
    store.proposals[pid] = {
        "id": pid, "tenant_id": tenant_id,
        "round_id": "0fdfd886-ea1b-5316-ba3f-36b5dbe63679",
        "target": target, "op": op,
        "payload": {"name": name, "parent": parent,
                    "evidence_spans": [
                        {"doc": "guide-2020", "text": "二甲双胍为基本用药"},
                        {"doc": "guide-2024", "text": "二甲双胍为基本用药"}]},
        "confidence": confidence, "impact": impact, "novelty": novelty,
        "status": status, "trigger_source": "seed_bootstrap",
    }
    return pid


def _auth_as(monkeypatch, user_id=ADMIN, tenant_id=TENANT_A, role="ADMIN",
             kb_manage=True, graph_manage=False):
    """Patch the two seams the app layer touches: session context and RBAC."""
    monkeypatch.setattr(
        "apps.knowledge_graph_app.get_current_user_context",
        lambda _authorization: (user_id, tenant_id, role))
    calls = []

    def fake_check(user_role, category, ptype, subtype=None):
        calls.append((user_role, category, ptype, subtype))
        if (category, ptype) == ("RESOURCE", "KNOWLEDGE_GRAPH"):
            return graph_manage
        if (category, ptype) == ("RESOURCE", "KB"):
            return kb_manage
        return False

    monkeypatch.setattr(
        "apps.knowledge_graph_app.check_role_permission", fake_check)
    return calls


def _run(coro):
    return asyncio.run(coro)


# ── auth seam: 401 / 403 ─────────────────────────────────────────────

def test_context_expired_session_maps_to_401(monkeypatch):
    import pytest

    from consts.exceptions import TokenExpiredError

    def _expired(_authorization):
        raise TokenExpiredError("Session expired, please log in again")

    monkeypatch.setattr(
        "apps.knowledge_graph_app.get_current_user_context", _expired)
    # The app layer does not swallow TokenExpiredError: it propagates to
    # app_factory's handler which answers 401 TOKEN_EXPIRED.
    with pytest.raises(TokenExpiredError):
        _run(knowledge_graph_app.list_proposals(authorization="Bearer junk"))


def test_context_no_permission_maps_to_403(monkeypatch):
    import pytest
    _auth_as(monkeypatch, kb_manage=False, graph_manage=False)
    with pytest.raises(HTTPException) as denied:
        _run(knowledge_graph_app.list_proposals(authorization="Bearer t"))
    assert denied.value.status_code == 403


def test_context_kb_fallback_allows_admin_until_t08_wiring(monkeypatch):
    calls = _auth_as(monkeypatch, kb_manage=True, graph_manage=False)
    store = FakeReviewStore()
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    result = _run(knowledge_graph_app.list_proposals(authorization="Bearer t"))
    assert result["total"] == 0
    # Both permission probes went through RBAC before falling back.
    assert ("ADMIN", "RESOURCE", "KNOWLEDGE_GRAPH", "MANAGE") in calls
    assert ("ADMIN", "RESOURCE", "KB", "MANAGE") in calls


# ── GET /ontology/proposals ───────────────────────────────────────────

def test_proposals_queue_score_order_and_paging_cap(monkeypatch):
    _auth_as(monkeypatch)
    store = FakeReviewStore()
    high = _seed_proposal(store, confidence=0.95, impact=9, novelty=0.5)
    low = _seed_proposal(store, target="cls:Sulfonylurea", name="Sulfonylurea",
                         confidence=0.4, impact=2, novelty=0.1)
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    res = _run(knowledge_graph_app.list_proposals(authorization="Bearer t"))
    assert res["total"] == 2
    assert res["items"][0]["id"] == high
    assert res["items"][0]["score"] > res["items"][1]["score"]
    assert res["items"][0]["ev_rich"] == 1.0  # two docs
    assert low in [i["id"] for i in res["items"]]

    # 45 asked, 40 granted (MAX_PROPOSALS_PER_SESSION).
    res45 = _run(knowledge_graph_app.list_proposals(
        authorization="Bearer t", page=1, page_size=45))
    assert res45["page_size"] == 40


def test_proposals_tenant_isolation(monkeypatch):
    _auth_as(monkeypatch, tenant_id=TENANT_B)
    store = FakeReviewStore()
    _seed_proposal(store, tenant_id=TENANT_A)
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    res = _run(knowledge_graph_app.list_proposals(authorization="Bearer t"))
    assert res["total"] == 0  # tenant B never sees tenant A's queue


# ── POST /ontology/proposals/review ───────────────────────────────────

def test_review_confirm_happy_path(monkeypatch):
    _auth_as(monkeypatch)
    store = FakeReviewStore()
    pid = _seed_proposal(store)
    svc = OntologyService(store=store)
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service", lambda: svc)
    req = knowledge_graph_app.ReviewActionRequest(
        action="confirm", ids=[uuid_mod.UUID(pid)])
    res = _run(knowledge_graph_app.review_proposals(req, authorization="Bearer t"))
    assert res == {"action": "confirm", "updated": 1,
                   "new_parent": None, "status": "confirmed"}
    assert store.proposals[pid]["status"] == "confirmed"
    assert store.proposals[pid]["reviewed_by"] == ADMIN


def test_review_reject_records_reason(monkeypatch):
    _auth_as(monkeypatch)
    store = FakeReviewStore()
    pid = _seed_proposal(store)
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    req = knowledge_graph_app.ReviewActionRequest(
        action="reject", ids=[uuid_mod.UUID(pid)], reject_reason="dup")
    res = _run(knowledge_graph_app.review_proposals(req, authorization="Bearer t"))
    assert res["status"] == "rejected"
    assert store.proposals[pid]["reject_reason"] == "dup"


def test_review_reparent_rewrites_parent(monkeypatch):
    _auth_as(monkeypatch)
    store = FakeReviewStore()
    store.snapshots[TENANT_A] = {"classes": [
        {"name": "Drug", "parent": None},
        {"name": "Metformin", "parent": "Drug"},
    ], "rel_types": []}
    pid = _seed_proposal(store, target="cls:Sulfonylurea",
                         name="Sulfonylurea", parent="Drug")
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    req = knowledge_graph_app.ReviewActionRequest(
        action="reparent", ids=[uuid_mod.UUID(pid)], new_parent="Antidiabetic")
    res = _run(knowledge_graph_app.review_proposals(req, authorization="Bearer t"))
    assert res["status"] == "confirmed"
    assert res["new_parent"] == "Antidiabetic"
    assert store.proposals[pid]["payload"]["parent"] == "Antidiabetic"


def test_review_reparent_cycle_refused_atomically(monkeypatch):
    _auth_as(monkeypatch)
    store = FakeReviewStore()
    # Antidiabetic's parent is Sulfonylurea; reparenting Sulfonylurea
    # under Antidiabetic closes the cycle.
    store.snapshots[TENANT_A] = {"classes": [
        {"name": "Sulfonylurea", "parent": None},
        {"name": "Antidiabetic", "parent": "Sulfonylurea"},
    ], "rel_types": []}
    pid = _seed_proposal(store, name="Antidiabetic", parent="Sulfonylurea")
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    req = knowledge_graph_app.ReviewActionRequest(
        action="reparent", ids=[uuid_mod.UUID(pid)], new_parent="Antidiabetic")
    import pytest
    with pytest.raises(HTTPException) as denied:
        _run(knowledge_graph_app.review_proposals(req, authorization="Bearer t"))
    assert denied.value.status_code == 403  # PermissionError -> 403 (V1 cycle)
    # Atomic: the batch is untouched.
    assert store.proposals[pid]["status"] == "pending"
    assert store.proposals[pid]["payload"]["parent"] == "Sulfonylurea"


def test_review_unknown_id_404_and_cross_tenant_404(monkeypatch):
    _auth_as(monkeypatch)
    store = FakeReviewStore()
    foreign = _seed_proposal(store, tenant_id=TENANT_B)
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    import pytest
    req = knowledge_graph_app.ReviewActionRequest(
        action="confirm", ids=[uuid_mod.UUID(foreign)])
    with pytest.raises(HTTPException) as err:
        _run(knowledge_graph_app.review_proposals(req, authorization="Bearer t"))
    assert err.value.status_code == 404  # tenant isolation, not a leak


def test_review_bad_action_422(monkeypatch):
    import pytest
    _auth_as(monkeypatch)
    with pytest.raises(HTTPException) as err:
        req = knowledge_graph_app.ReviewActionRequest(
            action="delete", ids=[uuid_mod.uuid4()])
        _run(knowledge_graph_app.review_proposals(req, authorization="Bearer t"))
    assert err.value.status_code == 422


def test_review_reparent_without_parent_422(monkeypatch):
    import pytest
    _auth_as(monkeypatch)
    with pytest.raises(HTTPException) as err:
        req = knowledge_graph_app.ReviewActionRequest(
            action="reparent", ids=[uuid_mod.uuid4()])
        _run(knowledge_graph_app.review_proposals(req, authorization="Bearer t"))
    assert err.value.status_code == 422


# ── POST /ontology/versions + metrics + diff ──────────────────────────

def _confirmed_store():
    """One confirmed proposal (Metformin via confirm) + one still pending
    (Sulfonylurea) - the queue state after a half-finished session."""
    store = FakeReviewStore()
    confirmed = _seed_proposal(store, name="Metformin", parent="Drug")
    svc = OntologyService(store=store)
    _run(svc.review_proposals(
        TENANT_A, [confirmed], "confirm", reviewed_by=ADMIN))
    _seed_proposal(store, target="cls:Sulfonylurea",
                   name="Sulfonylurea", parent="Drug")
    return store, confirmed


def test_commit_version_semver_and_metrics_and_diff_roundtrip(monkeypatch):
    _auth_as(monkeypatch)
    store, pid = _confirmed_store()
    svc = OntologyService(store=store)
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service", lambda: svc)

    req = knowledge_graph_app.VersionCommitRequest(
        confirmed_ids=[uuid_mod.UUID(pid)])
    v1 = _run(knowledge_graph_app.commit_version(req, authorization="Bearer t"))
    assert v1["version"] == "v1.0.0"
    assert v1["folded"] == 1
    names = [c["name"] for c in v1["snapshot"]["classes"]]
    assert "Metformin" in names        # the confirmed one is folded in
    assert "Sulfonylurea" not in names  # pending stays queued, not committed

    # K0 metrics on the committed version
    m = _run(knowledge_graph_app.version_metrics("v1.0.0",
                                                 authorization="Bearer t"))
    assert set(m["metrics"]) == {"cov", "red", "dep", "align"}

    # Commit again (a second class) then diff v1.0.0 -> v1.1.0
    pid2 = _seed_proposal(store, target="cls:Insulin", name="Insulin",
                          parent="Drug")
    _run(svc.review_proposals(TENANT_A, [pid2], "confirm"))
    req2 = knowledge_graph_app.VersionCommitRequest(
        confirmed_ids=[uuid_mod.UUID(pid2)], base_version="v1.0.0")
    v2 = _run(knowledge_graph_app.commit_version(req2, authorization="Bearer t"))
    assert v2["version"] == "v1.1.0"

    d = _run(knowledge_graph_app.ontology_diff(
        authorization="Bearer t", from_version="v1.0.0", to_version="v1.1.0"))
    assert [o["op"] for o in d["ops"]] == ["CLS_ADD"]
    assert d["ops"][0]["payload"]["name"] == "Insulin"


def test_metrics_unknown_version_404(monkeypatch):
    import pytest
    _auth_as(monkeypatch)
    store, _ = _confirmed_store()
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    with pytest.raises(HTTPException) as err:
        _run(knowledge_graph_app.version_metrics("v9.9.9",
                                                 authorization="Bearer t"))
    assert err.value.status_code == 404


def test_commit_with_no_confirmed_ids_folds_whole_session(monkeypatch):
    _auth_as(monkeypatch)
    store, _confirmed = _confirmed_store()
    _seed_proposal(store, target="cls:Insulin", name="Insulin", parent="Drug")
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    req = knowledge_graph_app.VersionCommitRequest()  # no ids -> all confirmed
    res = _run(knowledge_graph_app.commit_version(req, authorization="Bearer t"))
    assert res["folded"] == 1  # only the confirmed one, pending stays queued


# ── GET /ontology/versions/active (T-18a) ─────────────────────────────


def test_active_version_404_before_first_commit(monkeypatch):
    """No published version answers 404 so the client renders "none yet"
    rather than an empty ontology (frontend maps 404 -> null)."""
    import pytest
    _auth_as(monkeypatch)
    store = FakeReviewStore()
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    with pytest.raises(HTTPException) as err:
        _run(knowledge_graph_app.active_version(authorization="Bearer t"))
    assert err.value.status_code == 404


def test_active_version_returns_latest_row_after_commit(monkeypatch):
    """After a commit the endpoint serves the row the tree panel needs:
    version label + snapshot + metrics (not just the bare snapshot)."""
    _auth_as(monkeypatch)
    store, _confirmed = _confirmed_store()
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    _run(knowledge_graph_app.commit_version(
        knowledge_graph_app.VersionCommitRequest(), authorization="Bearer t"))
    row = _run(knowledge_graph_app.active_version(authorization="Bearer t"))
    assert row["version"] == "v1.0.0"
    assert row["status"] == "published"
    assert row["snapshot"]["classes"]  # the committed class is present
    assert "metrics" in row


def test_active_version_requires_workbench_permission(monkeypatch):
    import pytest
    _auth_as(monkeypatch, kb_manage=False, graph_manage=False)
    with pytest.raises(HTTPException) as err:
        _run(knowledge_graph_app.active_version(authorization="Bearer t"))
    assert err.value.status_code == 403


def test_active_version_scopes_to_session_tenant(monkeypatch):
    """The endpoint asks the store for the *session* tenant, never a
    caller-supplied one - the tenant-isolation seam for the active view."""
    _auth_as(monkeypatch, tenant_id=TENANT_B)
    store = FakeReviewStore()
    seen = {}

    async def _record(tenant_id):
        seen["tenant_id"] = tenant_id
        return None

    store.load_active_version_row = _record
    monkeypatch.setattr(
        knowledge_graph_app, "_ontology_service",
        lambda: OntologyService(store=store))
    import pytest
    with pytest.raises(HTTPException) as err:
        _run(knowledge_graph_app.active_version(authorization="Bearer t"))
    assert err.value.status_code == 404
    assert seen["tenant_id"] == TENANT_B


class TestPostgresReviewLoop:
    """Layer 2 (RUN_POSTGRES_INTEGRATION=1): the T-05 confirm loop against
    the real schema - queue listing, batch review with reparent cycle
    refusal, commit_from_queue version bump, version metrics, and tenant
    isolation. Self-cleaning like TestPostgresRound above."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
        reason="set RUN_POSTGRES_INTEGRATION=1 with a reachable PG",
    )
    async def test_review_loop_lands_in_pg(self):
        from uuid import uuid4

        from database.knowevo_db import (
            OntologyChangeProposal as OCP,
        )
        from database.knowevo_db import (
            OntologyVersion as OV,
        )
        from database.knowevo_db import (
            _get_db_session,
        )
        from services.knowevo.ontology_service import PgStore

        with _get_db_session() as session:
            session.query(OCP).filter(
                OCP.tenant_id.in_([TENANT_A, TENANT_B])).delete(
                synchronize_session=False)
            session.query(OV).filter(
                OV.tenant_id.in_([TENANT_A, TENANT_B])).delete(
                synchronize_session=False)

        svc = OntologyService(store=PgStore())
        rows = await svc.store.list_queue_rows(TENANT_A)  # may be empty; fine
        assert isinstance(rows, list)

        # Seed one fresh pending proposal through PgStore. save_proposals
        # takes proposal-shaped objects (ConceptProposal fields), not queue
        # rows - target/op/payload are derived by the PgStore adapter.
        rid = uuid4()
        from services.knowevo.ontology_service import ConceptProposal
        await svc.store.save_proposals(TENANT_A, [ConceptProposal(
            name="Metformin", parent_stable_id="Drug",
            evidence_spans=[{"doc": "d1"}, {"doc": "d2"}],
            confidence=0.9, novelty=0.3, impact=0.6,
        )], rid, "seed_bootstrap")
        queue = await svc.list_review_queue(TENANT_A)
        assert queue["total"] >= 1
        # No next() on a generator inside a coroutine: a StopIteration
        # crossing an await boundary becomes RuntimeError (asyncio rule).
        pid = ""
        for item in queue["items"]:
            if item["payload"].get("name") == "Metformin":
                pid = item["id"]
                break
        assert pid, "seeded Metformin proposal missing from the queue"

        # Tenant B never sees tenant A's queue rows.
        qb = await svc.list_review_queue(TENANT_B)
        assert all(i["payload"].get("name") != "Metformin"
                   for i in qb["items"])

        # Reparent cycle: pending Metformin parent -> Metformin itself.
        import pytest as _pytest
        with _pytest.raises(PermissionError):
            await svc.review_proposals(
                TENANT_A, [pid], "reparent", new_parent="Metformin")

        # Clean confirm -> commit -> metrics -> diff round trip.
        res = await svc.review_proposals(TENANT_A, [pid], "confirm",
                                         reviewed_by="admin@knowevo.com")
        assert res["updated"] == 1
        row = await svc.commit_from_queue(TENANT_A)
        assert row["folded"] >= 1
        assert row["version"] == "v1.0.0"
        m = await svc.version_metrics(TENANT_A, "v1.0.0")
        assert m is not None and set(m) == {"cov", "red", "dep", "align"}

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
        reason="set RUN_POSTGRES_INTEGRATION=1 with a reachable PG",
    )
    async def test_active_version_endpoint_lands_in_pg(self):
        """GET /ontology/versions/active against the real schema: 404 for a
        tenant with no version, then the committed row for the tenant that
        committed one - the row-level read PgStore actually serves."""
        from uuid import uuid4

        from database.knowevo_db import (
            OntologyChangeProposal as OCP,
        )
        from database.knowevo_db import (
            OntologyVersion as OV,
        )
        from database.knowevo_db import (
            _get_db_session,
        )
        from services.knowevo.ontology_service import ConceptProposal, PgStore

        with _get_db_session() as session:
            session.query(OCP).filter(
                OCP.tenant_id.in_([TENANT_A, TENANT_B])).delete(
                synchronize_session=False)
            session.query(OV).filter(
                OV.tenant_id.in_([TENANT_A, TENANT_B])).delete(
                synchronize_session=False)

        svc = OntologyService(store=PgStore())
        assert await svc.get_active_row(TENANT_B) is None

        rid = uuid4()
        await svc.store.save_proposals(TENANT_A, [ConceptProposal(
            name="Metformin", parent_stable_id="Drug",
            evidence_spans=[{"doc": "d1"}],
            confidence=0.9, novelty=0.3, impact=0.6,
        )], rid, "seed_bootstrap")
        queue = await svc.list_review_queue(TENANT_A)
        pid = next(i["id"] for i in queue["items"]
                   if i["payload"].get("name") == "Metformin")
        await svc.review_proposals(TENANT_A, [pid], "confirm",
                                   reviewed_by="admin@knowevo.com")
        await svc.commit_from_queue(TENANT_A)

        row = await svc.get_active_row(TENANT_A)
        assert row is not None
        assert row["version"] == "v1.0.0"
        assert row["status"] == "published"
        assert row["snapshot"]["classes"]
        # created_at is real (not None) on the row-level read - the pin
        # fallback depends on it
        assert row["created_at"]
        # tenant B still sees nothing
        assert await svc.get_active_row(TENANT_B) is None
