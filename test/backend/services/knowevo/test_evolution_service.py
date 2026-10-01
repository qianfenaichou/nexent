"""
Unit tests for services/knowevo/evolution_service.py - the 
evolution-round orchestrator that joins the change-detection end
(alignment_service / diff_guidelines) to the controlled-supersede end
(graph_store.supersede), which ``kg_service.ingest_new_version`` had left
as a NotImplementedError stub.

This file is the acceptance anchor named by the frozen contract
``knowevo/backend/services/knowevo/evolution_service.py.md`` §验收锚点:

* standard-update full chain with the three downstream services mocked,
* settle atomicity (a failure mid-settle must not leave a half ledger),
* rollback restoring the current view (edges un-invalidated, cards
  un-flagged) without deleting any history,
* timeline aggregation.

Layer 1 only: an in-memory FakeStore plus scripted fake downstream
services. No Postgres, no network, no LLM - nothing here is fabricated,
and no Layer-2 fixture is needed because the module performs no DDL.
"""
import asyncio
import sys
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.evolution_service import (
    STATUS_ROLLED_BACK,
    STATUS_RUNNING,
    STATUS_SETTLED,
    TRIGGER_KINDS,
    EvolutionService,
    RoundReport,
    Trigger,
)

TENANT = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# test doubles
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory stand-in for PgEvolutionStore.

    Mirrors the production seam method-for-method so a behaviour proven
    here is a behaviour the orchestrator actually exercises. ``fail_updates``
    injects a write failure to prove settle atomicity.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.eval_runs: dict[str, dict] = {}
        self.edge_restores: list[tuple[str, list[str]]] = []
        self.valid_edges: set[str] = set()
        self.needs_rerun: dict[str, bool] = {}
        self.fail_updates = False
        self.calls: list[str] = []

    async def create_round(self, *, tenant_id, trigger_source,
                           trigger_ref=None, ops_summary=None, cost=None,
                           rollback_of=None, created_by=None):
        self.calls.append("create_round")
        rid = str(uuid_mod.uuid4())
        self.rows[rid] = {
            "id": rid,
            "tenant_id": tenant_id,
            "trigger_source": trigger_source,
            "trigger_ref": trigger_ref,
            "ops_summary": dict(ops_summary or {}),
            "cost": dict(cost or {}),
            "eval_delta": None,
            "rollback_of": rollback_of,
            "created_at": datetime.now(UTC).isoformat(),
            "created_by": created_by,
        }
        return rid

    async def get_round(self, round_id):
        self.calls.append("get_round")
        row = self.rows.get(str(round_id))
        return dict(row) if row else None

    async def update_round(self, round_id, *, ops_summary=None, cost=None,
                           eval_delta=None):
        self.calls.append("update_round")
        if self.fail_updates:
            raise RuntimeError("injected write failure")
        row = self.rows.get(str(round_id))
        if row is None:
            return False
        if ops_summary is not None:
            row["ops_summary"] = dict(ops_summary)
        if cost is not None:
            row["cost"] = dict(cost)
        if eval_delta is not None:
            row["eval_delta"] = dict(eval_delta)
        return True

    async def list_rounds(self, tenant_id, since=None, limit=100):
        self.calls.append("list_rounds")
        rows = [dict(r) for r in self.rows.values()
                if r["tenant_id"] == tenant_id]
        if since is not None:
            rows = [r for r in rows
                    if r["created_at"] and datetime.fromisoformat(
                        r["created_at"]) >= since]
        rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
        return rows[:limit]

    async def get_eval_run(self, eval_run_id):
        self.calls.append("get_eval_run")
        return self.eval_runs.get(str(eval_run_id))

    async def mark_cards_needs_rerun(self, tenant_id, card_ids,
                                     rerun_of=None):
        self.calls.append("mark_cards_needs_rerun")
        for c in card_ids:
            self.needs_rerun[str(c)] = True
        return len(card_ids)

    async def clear_cards_needs_rerun(self, tenant_id, card_ids):
        self.calls.append("clear_cards_needs_rerun")
        for c in card_ids:
            self.needs_rerun[str(c)] = False
        return len(card_ids)

    async def restore_edges(self, tenant_id, edge_ids):
        self.calls.append("restore_edges")
        self.edge_restores.append((tenant_id, list(edge_ids)))
        restored = [e for e in edge_ids if str(e) in self.valid_edges]
        for e in restored:
            self.valid_edges.discard(str(e))
        return len(restored)


class FakeAlignment:
    def __init__(self, cards=("c-1", "c-2"), entities=("e-1",),
                 proposals=(1, 2, 3), ontology_ops=("CLS_ADD",)):
        self._scope = {
            "changed_spans": ["s1", "s2"],
            "affected_decision_ids": list(cards),
            "affected_entity_ids": list(entities),
            "pending": list(proposals),
            "ontology_ops": list(ontology_ops),
        }

    async def detect_doc_change(self, round_id):
        return {"changed_spans": self._scope["changed_spans"]}

    async def impact_scope(self, scope):
        return self._scope

    async def propose_updates(self, round_id):
        return self._scope


class FakeOntology:
    def __init__(self):
        self.committed: list[tuple] = []
        self.deprecated: list[str] = []

    async def commit_version(self, ops, trigger_source=""):
        self.committed.append((tuple(ops), trigger_source))
        return "v1.0.1"

    async def mark_version_deprecated(self, round_id):
        self.deprecated.append(str(round_id))


class FakeKg:
    """Returns the ids of the edges the round invalidated."""

    def __init__(self, edge_ids=("edge-a", "edge-b")):
        self.edge_ids = list(edge_ids)
        self.calls: list[tuple] = []

    async def ingest_new_version(self, old_doc, new_doc, changed_spans):
        self.calls.append((old_doc, new_doc, list(changed_spans)))
        return {"superseded_edge_ids": list(self.edge_ids)}


class StubKg:
    """A kg owner whose endpoint raises: the step must surface as failed."""

    async def ingest_new_version(self, old_doc, new_doc, changed_spans):
        raise RuntimeError("kg owner unavailable")


def _service(store, **kw):
    return EvolutionService(TENANT, store=store, **kw)


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


class TestTrigger:
    def test_kinds_are_the_frozen_set(self):
        assert TRIGGER_KINDS == ("new_docs", "standard_update", "manual",
                                 "correction")

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            Trigger(kind="not-a-kind")

    def test_trigger_ref_prefers_the_new_doc(self):
        assert Trigger(kind="standard_update", old_doc="a",
                       new_doc="b").trigger_ref == "b"
        assert Trigger(kind="new_docs", doc_ids=["d1", "d2"]).trigger_ref == "d1"
        assert Trigger(kind="manual").trigger_ref is None


# ---------------------------------------------------------------------------
# start_round
# ---------------------------------------------------------------------------


class TestStartRound:
    def test_creates_running_row_with_zeroed_cost(self):
        store = FakeStore()
        svc = _service(store)
        rid = asyncio.run(svc.start_round(
            Trigger(kind="standard_update", old_doc="d-old", new_doc="d-new")))

        row = store.rows[rid]
        assert row["trigger_source"] == "standard_update"
        assert row["ops_summary"]["_status"] == STATUS_RUNNING
        assert row["cost"] == {"tokens": 0, "cny": 0.0, "human_minutes": 0.0}

    def test_requires_tenant(self):
        with pytest.raises(ValueError):
            EvolutionService("")

    def test_requires_a_trigger_instance(self):
        svc = _service(FakeStore())
        with pytest.raises(TypeError):
            asyncio.run(svc.start_round("standard_update"))


# ---------------------------------------------------------------------------
# run_standard_update
# ---------------------------------------------------------------------------


class TestStandardUpdateChain:
    def test_full_chain_with_all_downstreams(self):
        store, kg, ontology = FakeStore(), FakeKg(), FakeOntology()
        svc = _service(store, alignment=FakeAlignment(), ontology=ontology,
                       kg=kg)
        rid = asyncio.run(svc.start_round(
            Trigger(kind="standard_update", new_doc="d2")))
        report = asyncio.run(svc.run_standard_update(rid))

        assert isinstance(report, RoundReport)
        # every documented step is present, in order
        assert [s.name for s in report.steps] == [
            "detect_doc_change", "impact_scope", "propose_updates",
            "human_confirm", "ingest_new_version", "commit_ontology_version",
            "mark_decisions_needs_rerun", "settle_cost"]

        # the kg owner really was asked, with the contract's three arguments.
        # This scope carries no document pair, so both ends are None.
        assert kg.calls == [(None, None, ["s1", "s2"])]
        # ontology version committed from the ontology-level ops
        assert ontology.committed == [(("CLS_ADD",), "standard_update")]
        # affected cards flagged, and remembered for rollback
        assert store.needs_rerun == {"c-1": True, "c-2": True}
        assert report.edges_superseded_ids == ["edge-a", "edge-b"]
        assert store.rows[rid]["ops_summary"]["_affected_card_ids"] == \
            ["c-1", "c-2"]

    def test_ingest_new_version_forwards_the_document_pair(self):
        """When the scope names the old/new document, both travel on."""
        kg = FakeKg()
        svc = _service(FakeStore(), kg=kg)
        scope = {"old_doc": "d1", "new_doc": "d2", "changed_spans": ["s1"]}
        asyncio.run(svc._ingest_new_version(scope, "round-1"))
        assert kg.calls == [("d1", "d2", ["s1"])]

    def test_missing_downstreams_degrade_to_skipped_not_success(self):
        store = FakeStore()
        svc = _service(store)          # no alignment / ontology / kg at all
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        report = asyncio.run(svc.run_standard_update(rid))

        statuses = {s.name: s.status for s in report.steps}
        assert statuses["detect_doc_change"] == "skipped"
        assert statuses["impact_scope"] == "skipped"
        assert statuses["propose_updates"] == "skipped"
        assert statuses["ingest_new_version"] == "skipped"
        assert statuses["commit_ontology_version"] == "skipped"
        # the round is still inspectable rather than lost
        assert store.rows[rid] is not None
        # and no step claims an outcome it could not have produced
        assert all(s.status == "skipped" for s in report.steps
                   if s.name != "mark_decisions_needs_rerun"
                   and s.name != "settle_cost")

    def test_failing_ingest_is_recorded_as_failed(self):
        """A raising kg owner must surface as a failure, not empty success."""
        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=StubKg())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        report = asyncio.run(svc.run_standard_update(rid))

        step = report.step("ingest_new_version")
        assert step is not None and step.status == "failed"
        assert "unavailable" in step.detail
        assert report.edges_superseded_ids == []

    def test_alignment_failure_does_not_abort_the_round(self):
        class ExplodingAlignment:
            async def detect_doc_change(self, round_id):
                raise RuntimeError("upstream 429")

            async def impact_scope(self, scope):
                raise RuntimeError("upstream 429")

            async def propose_updates(self, round_id):
                raise RuntimeError("upstream 429")

        store = FakeStore()
        svc = _service(store, alignment=ExplodingAlignment(), kg=FakeKg())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        report = asyncio.run(svc.run_standard_update(rid))

        assert report.step("detect_doc_change").status == "failed"
        assert report.step("impact_scope").status == "failed"
        # the later step still ran - a failed front end is not a dead round
        assert report.step("ingest_new_version").status == "ok"

    def test_unknown_round_raises(self):
        svc = _service(FakeStore())
        with pytest.raises(KeyError):
            asyncio.run(svc.run_standard_update(str(uuid_mod.uuid4())))


# ---------------------------------------------------------------------------
# settle
# ---------------------------------------------------------------------------


class TestSettle:
    def test_settle_closes_the_round(self):
        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=FakeKg())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.run_standard_update(rid))
        asyncio.run(svc.settle(rid))

        assert store.rows[rid]["ops_summary"]["_status"] == STATUS_SETTLED

    def test_settle_is_atomic_on_write_failure(self):
        """A failure must leave the previous ledger values, not a half row."""
        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=FakeKg())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.run_standard_update(rid))

        before = dict(store.rows[rid]["ops_summary"])
        store.fail_updates = True
        with pytest.raises(RuntimeError):
            asyncio.run(svc.settle(rid))

        after = store.rows[rid]["ops_summary"]
        assert after == before
        assert after["_status"] == STATUS_RUNNING
        assert store.rows[rid]["eval_delta"] is None

    def test_settle_records_eval_delta_from_a_known_run(self):
        store = FakeStore()
        store.eval_runs["run-1"] = {"testset_hash": "abc",
                                    "acc_before": 0.70, "acc_after": 0.78}
        svc = _service(store)
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.settle(rid, eval_run_id="run-1"))

        assert store.rows[rid]["eval_delta"] == {
            "testset_hash": "abc", "acc_before": 0.70, "acc_after": 0.78}

    def test_settle_does_not_invent_a_delta_for_an_unknown_run(self):
        store = FakeStore()
        svc = _service(store)
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.settle(rid, eval_run_id="never-existed"))

        assert store.rows[rid]["eval_delta"] is None
        assert store.rows[rid]["ops_summary"]["_status"] == STATUS_SETTLED

    def test_unmeasured_human_cost_stays_zero(self):
        """human_minutes is recorded only when a confirmation session measured
        it; the orchestrator must not estimate it."""
        store = FakeStore()
        svc = _service(store)
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.settle(rid))
        assert store.rows[rid]["cost"]["human_minutes"] == 0.0


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def _run_round(self):
        store, ontology = FakeStore(), FakeOntology()
        store.valid_edges = {"edge-a", "edge-b"}
        kg = FakeKg()
        svc = _service(store, alignment=FakeAlignment(), ontology=ontology,
                       kg=kg)
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.run_standard_update(rid))
        return store, ontology, svc, rid

    def test_rollback_restores_the_current_view(self):
        store, ontology, svc, rid = self._run_round()
        assert store.needs_rerun == {"c-1": True, "c-2": True}
        assert store.valid_edges == {"edge-a", "edge-b"}

        new_id = asyncio.run(svc.rollback(rid))

        # edges are restored (invalid_at cleared), never deleted
        assert store.edge_restores == [(TENANT, ["edge-a", "edge-b"])]
        assert store.valid_edges == set()
        # cards un-flagged
        assert store.needs_rerun == {"c-1": False, "c-2": False}
        # a NEW corrective round is recorded; nothing was removed
        assert new_id != rid
        assert store.rows[new_id]["trigger_source"] == "correction"
        assert store.rows[new_id]["rollback_of"] == rid
        assert len(store.rows) == 2
        # the ontology version is deprecated, not deleted
        assert ontology.deprecated == [rid]

    def test_rollback_marks_the_original_round(self):
        store, _ontology, svc, rid = self._run_round()
        asyncio.run(svc.rollback(rid))
        assert store.rows[rid]["ops_summary"]["_status"] == STATUS_ROLLED_BACK

    def test_rollback_is_not_repeatable(self):
        store, _ontology, svc, rid = self._run_round()
        asyncio.run(svc.rollback(rid))
        with pytest.raises(ValueError):
            asyncio.run(svc.rollback(rid))

    def test_rollback_survives_a_missing_ontology_service(self):
        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=FakeKg())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.run_standard_update(rid))
        new_id = asyncio.run(svc.rollback(rid))
        assert store.rows[new_id]["rollback_of"] == rid

    def test_rollback_records_the_ontology_step_it_could_not_take(self):
        """A half-happened rollback must be visible, never silently swallowed."""
        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=FakeKg())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.run_standard_update(rid))
        new_id = asyncio.run(svc.rollback(rid))
        assert "not deprecated" in store.rows[new_id]["ops_summary"]["ontology"]

    def test_rollback_records_a_failing_deprecation(self):
        class FlakyOntology:
            async def mark_version_deprecated(self, round_id):
                raise RuntimeError("version tree locked")

        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=FakeKg(),
                       ontology=FlakyOntology())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.run_standard_update(rid))
        new_id = asyncio.run(svc.rollback(rid))
        note = store.rows[new_id]["ops_summary"]["ontology"]
        assert "deprecate failed" in note and "version tree locked" in note


# ---------------------------------------------------------------------------
# timeline / round_detail
# ---------------------------------------------------------------------------


class TestTimeline:
    def test_timeline_is_newest_first_and_tenant_scoped(self):
        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=FakeKg())
        ids = []
        for i in range(3):
            rid = asyncio.run(svc.start_round(
                Trigger(kind="manual", note=f"round {i}")))
            store.rows[rid]["created_at"] = (
                datetime.now(UTC) + timedelta(minutes=i)).isoformat()
            asyncio.run(svc.settle(rid))
            ids.append(rid)

        # another tenant's round must never leak into the timeline
        asyncio.run(FakeStore().create_round(
            tenant_id="22222222-2222-2222-2222-222222222222",
            trigger_source="manual"))

        rows = asyncio.run(svc.timeline(TENANT))
        assert [r.round_id for r in rows] == list(reversed(ids))
        assert all(r.status == STATUS_SETTLED for r in rows)

    def test_timeline_honours_since(self):
        store = FakeStore()
        svc = _service(store)
        old = asyncio.run(svc.start_round(Trigger(kind="manual")))
        store.rows[old]["created_at"] = (
            datetime.now(UTC) - timedelta(days=3)).isoformat()
        new = asyncio.run(svc.start_round(Trigger(kind="manual")))

        rows = asyncio.run(svc.timeline(
            TENANT, since=datetime.now(UTC) - timedelta(days=1)))
        assert [r.round_id for r in rows] == [new]

    def test_round_detail_returns_the_ledger(self):
        store = FakeStore()
        svc = _service(store, alignment=FakeAlignment(), kg=FakeKg())
        rid = asyncio.run(svc.start_round(Trigger(kind="standard_update")))
        asyncio.run(svc.run_standard_update(rid))
        detail = asyncio.run(svc.round_detail(rid))

        assert detail.round_id == rid
        assert detail.trigger_source == "standard_update"
        assert detail.edges_superseded_ids == ["edge-a", "edge-b"]

    def test_round_detail_rejects_unknown_round(self):
        svc = _service(FakeStore())
        with pytest.raises(KeyError):
            asyncio.run(svc.round_detail(str(uuid_mod.uuid4())))
