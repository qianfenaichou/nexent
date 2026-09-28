"""K5 evolution-round orchestration and version ledger (L4) - T-11.

Contract (frozen, authoritative): ``knowevo/backend/services/knowevo/
evolution_service.py.md``. This module is the missing implementation of
that contract; where the two disagree the contract wins and the mismatch
is reported rather than papered over.

Why this module exists
----------------------
The project already had both ends of the "knowledge evolution" chain but
nothing joining them:

* the **detection** end - ``alignment_service`` / ``pipeline/diff_guidelines``
  turn a guideline revision into change items, impact scope and proposals;
* the **action** end - ``graph_store.supersede`` stamps ``invalid_at`` +
  ``supersede_reason`` on the affected edges and never deletes a row.

``kg_service.ingest_new_version`` was left as
``NotImplementedError("ingest_new_version belongs to T-11")`` - i.e. the
编排 step in the middle was never written. Without it the official
"可进化" story is a description of two disconnected halves rather than a
replayable action, and ``evolution_round_t`` (whose DDL has existed since
``v2.5.5_kw_001``) stays empty.

Scope discipline (contract: "本服务不直接做算法，只做编排与台账")
-----------------------------------------------------------------
Every algorithm lives in its owner service. This module only:

1. opens/closes a round row (``evolution_round_t``),
2. calls the injected downstream services in the documented order,
3. records per-step outcome **including the ones it could not run**,
4. settles cost (tokens + ``human_minutes``) and the optional eval delta,
5. reverses a round (``rollback``) without ever deleting history.

Honest degradation, not silent success
--------------------------------------
Downstream services are injected and may legitimately be absent (a unit
test, a dry run, a deployment without an evaluator). A missing dependency
records the step as ``skipped`` with a reason instead of pretending it
ran - the same discipline the rest of ``services/knowevo`` follows. A
round can therefore be *inspected* even when it is incomplete, which is
exactly what a ledger is for.

Where the round status lives (and why no migration was added)
-------------------------------------------------------------
The frozen DDL of ``evolution_round_t`` (``10-A2 §2`` table 10) has no
``status`` column, yet the contract specifies ``start_round`` "creates
evolution_round_t row (status=running)" and ``rollback`` must mark the
reversed round. Adding a column would need a new migration and a change
to the ``knowevo_models`` contract for one bookkeeping flag, so the
status is kept **inside ``ops_summary``** under the reserved ``_status``
key (reserved keys are ``_``-prefixed so they can never collide with the
op-count namespace ``{CLS_ADD: 2, REL_UPD: 5, ...}``). The same reserved
block carries ``_edges_superseded_ids``, which is what makes ``rollback``
possible at all - there is no reverse index from a round to the edges it
invalidated, so the round has to remember them.

Attribution: bi-temporal supersede/invalidate semantics follow
``03-development-plan 4.2`` (graphiti's edge-invalidation model), the
same attribution the rest of the graph layer uses.
"""
from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "RESERVED_KEY_PREFIX",
    "STATUS_FAILED",
    "STATUS_ROLLED_BACK",
    "STATUS_RUNNING",
    "STATUS_SETTLED",
    "TRIGGER_KINDS",
    "EvolutionService",
    "PgEvolutionStore",
    "RoundReport",
    "RoundSummary",
    "StepRecord",
    "Trigger",
]

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------

#: Trigger kinds, frozen by the contract:
#: ``new_docs(ids)`` | ``standard_update(old,new)`` | ``manual`` | ``correction``.
TRIGGER_KINDS = ("new_docs", "standard_update", "manual", "correction")

STATUS_RUNNING = "running"
STATUS_SETTLED = "settled"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_FAILED = "failed"

#: Reserved ``ops_summary`` keys. Op counts never start with ``_``, so the
#: two namespaces cannot collide.
RESERVED_KEY_PREFIX = "_"
_K_STATUS = f"{RESERVED_KEY_PREFIX}status"
_K_EDGES = f"{RESERVED_KEY_PREFIX}edges_superseded_ids"
_K_CARDS = f"{RESERVED_KEY_PREFIX}affected_card_ids"
_K_ROLLBACK_OF = f"{RESERVED_KEY_PREFIX}rollback_of"
_K_STEPS = f"{RESERVED_KEY_PREFIX}steps"

#: The documented orchestration order (contract ``run_standard_update``).
#: Kept as a module constant so the report can show a step that never ran
#: as ``pending`` rather than omitting it - an omitted step is
#: indistinguishable from a step nobody implemented.
STANDARD_UPDATE_STEPS = (
    "detect_doc_change",
    "impact_scope",
    "propose_updates",
    "human_confirm",
    "ingest_new_version",
    "commit_ontology_version",
    "mark_decisions_needs_rerun",
    "settle_cost",
)


# ---------------------------------------------------------------------------
# value objects (the contract names these but does not freeze their shape)
# ---------------------------------------------------------------------------


@dataclass
class Trigger:
    """What started a round. ``kind`` must be one of :data:`TRIGGER_KINDS`.

    ``doc_ids`` is the ``new_docs`` payload; ``old_doc``/``new_doc`` are the
    ``standard_update`` payload. ``trigger_ref`` is what lands in the
    ``evolution_round_t.trigger_ref`` column (doc_asset_t.id / proposal.id)
    - derived here so callers never have to keep the two in sync.
    """

    kind: str
    doc_ids: list[str] = field(default_factory=list)
    old_doc: str | None = None
    new_doc: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in TRIGGER_KINDS:
            raise ValueError(
                f"trigger kind must be one of {TRIGGER_KINDS}, got {self.kind!r}")

    @property
    def trigger_ref(self) -> str | None:
        if self.kind == "new_docs" and self.doc_ids:
            return str(self.doc_ids[0])
        if self.kind == "standard_update":
            return str(self.new_doc or self.old_doc or "") or None
        return None


@dataclass
class StepRecord:
    """One orchestration step outcome. ``status`` ∈ ok | skipped | failed.

    ``detail`` is free text aimed at a human reading the ledger; it must
    name *why* a step did not run, never imply it did.
    """

    name: str
    status: str
    detail: str = ""


@dataclass
class RoundReport:
    """Full report of one round: ledger row + per-step trace."""

    round_id: str
    tenant_id: str
    trigger_source: str
    status: str
    steps: list[StepRecord] = field(default_factory=list)
    ops_summary: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    eval_delta: dict[str, Any] | None = None
    edges_superseded_ids: list[str] = field(default_factory=list)
    rollback_of: str | None = None
    created_at: str | None = None

    def step(self, name: str) -> StepRecord | None:
        for s in self.steps:
            if s.name == name:
                return s
        return None


@dataclass
class RoundSummary:
    """Timeline row (L5 dashboard / ``kg_evolution_trace`` share this)."""

    round_id: str
    at: str | None
    trigger_source: str
    status: str
    ops_summary: dict[str, Any] = field(default_factory=dict)
    eval_delta: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# PG store seam
# ---------------------------------------------------------------------------


class PgEvolutionStore:
    """``evolution_round_t`` persistence + the two DML helpers rollback needs.

    Read side: ``evolution_round_t``, plus ``eval_run_t`` for the optional
    eval delta. Write side: INSERT on ``evolution_round_t``, UPDATE on
    ``evolution_round_t`` and ``decision_card_t``, and the edge-restoration
    UPDATE used by ``rollback``. **No DELETE, no DDL** - history is never
    removed, matching the bi-temporal "invalidate, do not delete" rule.
    """

    _ROUND_FIELDS = ("id", "tenant_id", "trigger_source", "trigger_ref",
                     "ops_summary", "cost", "eval_delta", "rollback_of",
                     "created_at", "created_by")

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "tenant_id": str(row.tenant_id),
            "trigger_source": row.trigger_source,
            "trigger_ref": (str(row.trigger_ref) if row.trigger_ref else None),
            "ops_summary": dict(row.ops_summary or {}),
            "cost": dict(row.cost or {}),
            "eval_delta": (dict(row.eval_delta) if row.eval_delta else None),
            "rollback_of": (str(row.rollback_of) if row.rollback_of else None),
            "created_at": (row.created_at.isoformat()
                           if row.created_at is not None else None),
            "created_by": row.created_by,
        }

    async def create_round(self, *, tenant_id: str, trigger_source: str,
                           trigger_ref: str | None = None,
                           ops_summary: dict[str, Any] | None = None,
                           cost: dict[str, Any] | None = None,
                           rollback_of: str | None = None,
                           created_by: str | None = None) -> str:
        from database.knowevo_db import EvolutionRound, _get_db_session

        new_id = uuid_mod.uuid4()
        with _get_db_session() as session:
            session.add(EvolutionRound(
                id=new_id,
                tenant_id=tenant_id,
                trigger_source=trigger_source,
                trigger_ref=(uuid_mod.UUID(str(trigger_ref))
                             if trigger_ref else None),
                ops_summary=dict(ops_summary or {}),
                cost=dict(cost or {}),
                rollback_of=(uuid_mod.UUID(str(rollback_of))
                             if rollback_of else None),
                created_by=created_by,
            ))
            session.flush()
        return str(new_id)

    async def get_round(self, round_id: str) -> dict[str, Any] | None:
        from database.knowevo_db import EvolutionRound, _get_db_session

        with _get_db_session() as session:
            row = (session.query(EvolutionRound)
                   .filter(EvolutionRound.id == uuid_mod.UUID(str(round_id)))
                   .first())
            return self._row_to_dict(row) if row is not None else None

    async def update_round(self, round_id: str, *,
                           ops_summary: dict[str, Any] | None = None,
                           cost: dict[str, Any] | None = None,
                           eval_delta: dict[str, Any] | None = None) -> bool:
        """Single-row UPDATE: the unit of atomicity ``settle`` relies on.

        One statement per call means a failure can never leave a half-written
        ledger row - either the new values landed or the old ones stand.
        """
        from database.knowevo_db import EvolutionRound, _get_db_session

        with _get_db_session() as session:
            row = (session.query(EvolutionRound)
                   .filter(EvolutionRound.id == uuid_mod.UUID(str(round_id)))
                   .first())
            if row is None:
                return False
            if ops_summary is not None:
                row.ops_summary = dict(ops_summary)
            if cost is not None:
                row.cost = dict(cost)
            if eval_delta is not None:
                row.eval_delta = dict(eval_delta)
            session.flush()
        return True

    async def list_rounds(self, tenant_id: str, since: datetime | None = None,
                          limit: int = 100) -> list[dict[str, Any]]:
        from database.knowevo_db import EvolutionRound, _get_db_session

        with _get_db_session() as session:
            q = (session.query(EvolutionRound)
                 .filter(EvolutionRound.tenant_id == tenant_id))
            if since is not None:
                q = q.filter(EvolutionRound.created_at >= since)
            rows = q.order_by(EvolutionRound.created_at.desc()).limit(limit).all()
            return [self._row_to_dict(r) for r in rows]

    async def get_eval_run(self, eval_run_id: str) -> dict[str, Any] | None:
        """``eval_run_t`` row reduced to the K4 §3.1 eval-delta shape."""
        from database.knowevo_db import EvalRun, _get_db_session

        with _get_db_session() as session:
            row = (session.query(EvalRun)
                   .filter(EvalRun.id == uuid_mod.UUID(str(eval_run_id)))
                   .first())
            if row is None:
                return None
            metrics = dict(row.metrics or {})
            return {
                "testset_hash": row.testset_hash,
                "acc_before": metrics.get("acc_before"),
                "acc_after": metrics.get("acc_after", metrics.get("acc")),
            }

    async def mark_cards_needs_rerun(self, tenant_id: str,
                                     card_ids: list[str],
                                     rerun_of: str | None = None) -> int:
        """Flag the affected decision cards. Returns how many rows changed."""
        if not card_ids:
            return 0
        from database.knowevo_db import DecisionCard, _get_db_session

        ids = [uuid_mod.UUID(str(c)) for c in card_ids]
        with _get_db_session() as session:
            rows = (session.query(DecisionCard)
                    .filter(DecisionCard.tenant_id == tenant_id,
                            DecisionCard.id.in_(ids))
                    .all())
            for row in rows:
                row.needs_rerun = True
                if rerun_of:
                    row.rerun_of = uuid_mod.UUID(str(rerun_of))
            session.flush()
            return len(rows)

    async def clear_cards_needs_rerun(self, tenant_id: str,
                                      card_ids: list[str]) -> int:
        """Reverse of the above; used by ``rollback``. Never deletes rows."""
        if not card_ids:
            return 0
        from database.knowevo_db import DecisionCard, _get_db_session

        ids = [uuid_mod.UUID(str(c)) for c in card_ids]
        with _get_db_session() as session:
            rows = (session.query(DecisionCard)
                    .filter(DecisionCard.tenant_id == tenant_id,
                            DecisionCard.id.in_(ids))
                    .all())
            for row in rows:
                row.needs_rerun = False
                row.rerun_of = None
            session.flush()
            return len(rows)

    async def restore_edges(self, tenant_id: str,
                            edge_ids: list[str]) -> int:
        """Clear ``invalid_at`` on the edges a round stamped.

        This is the reverse of ``graph_store.supersede`` and is DML-only:
        the edge row is *restored*, never deleted, and ``props`` keeps the
        original ``supersede_reason`` so the reversal stays auditable. A
        dedicated ``GraphStore`` API would be the tidier home for this; it
        lives here because the contract puts ``rollback`` in this service
        and the graph seam has no restore method.
        """
        if not edge_ids:
            return 0
        from database.knowevo_db import KgRelation, _get_db_session

        ids = [uuid_mod.UUID(str(e)) for e in edge_ids]
        with _get_db_session() as session:
            rows = (session.query(KgRelation)
                    .filter(KgRelation.tenant_id == tenant_id,
                            KgRelation.id.in_(ids))
                    .all())
            for row in rows:
                row.invalid_at = None
                row.superseded_at = None
            session.flush()
            return len(rows)


# ---------------------------------------------------------------------------
# the orchestrator
# ---------------------------------------------------------------------------


class EvolutionService:
    """Round orchestration over injected owner services (contract T-11).

    ``alignment``, ``ontology`` and ``kg`` are the owner services; each is
    optional and a missing one degrades the corresponding step to
    ``skipped`` (see the module docstring). ``store`` is the persistence
    seam: :class:`PgEvolutionStore` in production, an in-memory fake in
    tests. ``retest`` is the optional K4 hook used by ``settle`` to obtain
    an eval delta from an ``eval_run_t`` id.
    """

    def __init__(self, tenant_id: str, store: Any = None,
                 alignment: Any = None, ontology: Any = None, kg: Any = None,
                 retest: Any = None, actor: str = ""):
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self.tenant_id = str(tenant_id)
        self.store = store
        self.alignment = alignment
        self.ontology = ontology
        self.kg = kg
        self.retest = retest
        self.actor = actor
        # Per-round accumulated state, keyed by str(round_id). Kept in
        # memory only for the live round; the ledger row is the durable
        # record (an interrupted process must be able to resume from the
        # row, so nothing load-bearing lives here).
        self._rounds: dict[str, RoundReport] = {}

    # ── store plumbing (lazy real store; kg_service seam style) ────────

    def _get_store(self):
        if self.store is None:
            self.store = PgEvolutionStore()
        return self.store

    # ── round lifecycle ───────────────────────────────────────────────

    async def start_round(self, trigger: Trigger) -> str:
        """Create the round row with ``_status=running``; return its id."""
        if not isinstance(trigger, Trigger):
            raise TypeError("trigger must be a Trigger")
        ops = {
            _K_STATUS: STATUS_RUNNING,
            _K_STEPS: [],
        }
        if trigger.note:
            ops[f"{RESERVED_KEY_PREFIX}note"] = trigger.note
        cost = {"tokens": 0, "cny": 0.0, "human_minutes": 0.0}
        round_id = await self._get_store().create_round(
            tenant_id=self.tenant_id,
            trigger_source=trigger.kind,
            trigger_ref=trigger.trigger_ref,
            ops_summary=ops,
            cost=cost,
            created_by=self.actor or None,
        )
        self._rounds[round_id] = RoundReport(
            round_id=round_id, tenant_id=self.tenant_id,
            trigger_source=trigger.kind, status=STATUS_RUNNING,
            ops_summary=ops, cost=cost,
        )
        return round_id

    async def run_standard_update(self, round_id: str) -> RoundReport:
        """Run the K5 chain for a ``standard_update`` round.

        Order and owner service per the contract:
        ``detect_doc_change`` → ``impact_scope`` → ``propose_updates`` →
        ``human_confirm`` → ``ingest_new_version`` + ``commit_ontology_version``
        → ``mark_decisions_needs_rerun`` → ``settle_cost``.

        The run is deliberately *not* all-or-nothing: each step's outcome is
        recorded and the round keeps whatever the earlier steps produced, so
        a failure halfway still leaves a truthful ledger. The caller decides
        whether to ``rollback``.
        """
        record = await self._load(round_id)
        steps: list[StepRecord] = []

        # 1-3: detection / impact scope / proposals - all owned by alignment.
        scope = None
        if self.alignment is None:
            steps.append(StepRecord(
                "detect_doc_change", "skipped",
                "alignment service not injected"))
            steps.append(StepRecord(
                "impact_scope", "skipped", "alignment service not injected"))
            steps.append(StepRecord(
                "propose_updates", "skipped", "alignment service not injected"))
        else:
            scope, change_steps = await self._detect_scope_and_propose(
                record.round_id)
            steps.extend(change_steps)

        # 4: human confirmation. The proposals queue is the confirmation
        # surface; a round never auto-advances past it, so the step records
        # the pending count rather than claiming approval.
        pending = self._pending_count(scope)
        steps.append(StepRecord(
            "human_confirm",
            "ok" if self.alignment is not None else "skipped",
            (f"{pending} proposal(s) queued for human confirmation"
             if self.alignment is not None
             else "alignment service not injected")))

        # 5: controlled supersede of the affected facts (kg owner).
        superseded_ids: list[str] = []
        if self.kg is None:
            steps.append(StepRecord(
                "ingest_new_version", "skipped", "kg service not injected"))
        else:
            try:
                superseded_ids = await self._ingest_new_version(scope, round_id)
                steps.append(StepRecord(
                    "ingest_new_version", "ok",
                    f"{len(superseded_ids)} edge(s) superseded"))
            except NotImplementedError as exc:
                # The historical stub raised this; surface it as a real
                # failure rather than an empty success.
                steps.append(StepRecord(
                    "ingest_new_version", "failed", str(exc)))

        # 6: commit the ontology version when the round produced ops.
        if self.ontology is None:
            steps.append(StepRecord(
                "commit_ontology_version", "skipped",
                "ontology service not injected"))
        else:
            committed, detail = await self._commit_ontology(scope, round_id)
            steps.append(StepRecord(
                "commit_ontology_version", "ok" if committed else "skipped",
                detail))

        # 7: mark the affected decision cards for re-run.
        card_ids = self._affected_card_ids(scope)
        if card_ids:
            changed = await self._get_store().mark_cards_needs_rerun(
                self.tenant_id, card_ids, rerun_of=round_id)
            steps.append(StepRecord(
                "mark_decisions_needs_rerun", "ok",
                f"{changed} card(s) flagged needs_rerun"))
        else:
            steps.append(StepRecord(
                "mark_decisions_needs_rerun", "ok",
                "no affected decision card in scope"))

        # 8: cost settlement is done by settle(); record that it is pending.
        steps.append(StepRecord(
            "settle_cost", "ok" if record.status == STATUS_SETTLED
            else "pending", "call settle() to close the round"))

        await self._persist_steps(round_id, steps, superseded_ids, card_ids)
        record = await self._load(round_id)
        record.steps = steps
        record.edges_superseded_ids = superseded_ids
        return record

    async def settle(self, round_id: str,
                     eval_run_id: str | None = None) -> None:
        """Close a round: write ops_summary + cost (+ eval_delta) atomically.

        Atomicity is structural - one single-row UPDATE - so a failure
        leaves the previous ledger values untouched rather than a half
        written round ("失败中断不留半账", the acceptance anchor).

        ``human_minutes`` is whatever the confirmation session accumulated;
        it is carried in the round's cost dict by the callers that own the
        confirmation UI. This method never invents it: an unmeasured
        human cost stays 0 and is reported as 0, not as an estimate.
        """
        record = await self._load(round_id)
        eval_delta = None
        if eval_run_id is not None:
            fetch = self.retest or self._get_store().get_eval_run
            eval_delta = await fetch(eval_run_id)
            if eval_delta is None:
                # Do not fabricate a delta for an unknown run.
                eval_delta = None

        ops = dict(record.ops_summary or {})
        ops[_K_STATUS] = STATUS_SETTLED
        merged_delta = eval_delta if eval_delta is not None else record.eval_delta

        ok = await self._get_store().update_round(
            round_id, ops_summary=ops, cost=dict(record.cost or {}),
            eval_delta=merged_delta)
        if not ok:
            raise KeyError(f"unknown round {round_id}")
        record.status = STATUS_SETTLED
        record.ops_summary = ops
        record.eval_delta = merged_delta

    async def rollback(self, round_id: str) -> str:
        """Reverse a round without deleting anything; return the new round id.

        Three effects, all restoration rather than deletion:

        1. the edges the round superseded get ``invalid_at`` cleared;
        2. the affected cards' ``needs_rerun`` flag is cleared;
        3. a **new** round with ``trigger=correction`` is recorded and
           ``rollback_of`` points at the reversed round, which is marked
           ``rolled_back``.

        The ontology version is *not* deleted - the contract says the child
        version is marked deprecated, which is the ontology service's call
        (it owns the version tree), so this method asks it and records
        ``skipped`` when it is absent.
        """
        record = await self._load(round_id)
        if record.status == STATUS_ROLLED_BACK:
            raise ValueError(f"round {round_id} is already rolled back")

        restored = await self._get_store().restore_edges(
            self.tenant_id, record.edges_superseded_ids)
        card_ids = [str(c) for c in (record.ops_summary.get(_K_CARDS) or [])]
        cleared = await self._get_store().clear_cards_needs_rerun(
            self.tenant_id, card_ids)

        # The version tree belongs to the ontology service, so the reversal
        # of the ontology side is asked for rather than done here. A failure
        # is recorded in the corrective round instead of being swallowed:
        # a rollback that half-happened must be visible in the ledger.
        ontology_note = "ontology service absent; child version not deprecated"
        deprecate = getattr(self.ontology, "mark_version_deprecated", None)
        if deprecate is not None:
            try:
                await deprecate(round_id)
                ontology_note = "child ontology version marked deprecated"
            except Exception as exc:  # noqa: BLE001 - rollback must not abort
                ontology_note = (f"deprecate failed: {type(exc).__name__}: "
                                 f"{exc}")

        new_id = await self._get_store().create_round(
            tenant_id=self.tenant_id,
            trigger_source="correction",
            trigger_ref=round_id,
            ops_summary={
                _K_STATUS: STATUS_RUNNING,
                _K_ROLLBACK_OF: round_id,
                "edges_restored": restored,
                "cards_cleared": cleared,
                "ontology": ontology_note,
            },
            cost={"tokens": 0, "cny": 0.0, "human_minutes": 0.0},
            rollback_of=round_id,
            created_by=self.actor or None,
        )

        ops = dict(record.ops_summary or {})
        ops[_K_STATUS] = STATUS_ROLLED_BACK
        await self._get_store().update_round(round_id, ops_summary=ops)
        record.status = STATUS_ROLLED_BACK
        return new_id

    # ── timeline surface (L5 dashboard + kg_evolution_trace share this) ──

    async def timeline(self, tenant_id: str,
                       since: datetime | None = None) -> list[RoundSummary]:
        """Rounds of ``tenant_id`` newest-first, reduced to timeline rows."""
        rows = await self._get_store().list_rounds(tenant_id, since=since)
        out: list[RoundSummary] = []
        for row in rows:
            ops = dict(row.get("ops_summary") or {})
            out.append(RoundSummary(
                round_id=row["id"],
                at=row.get("created_at"),
                trigger_source=row.get("trigger_source") or "",
                status=str(ops.get(_K_STATUS) or STATUS_RUNNING),
                ops_summary=ops,
                eval_delta=row.get("eval_delta"),
            ))
        return out

    async def round_detail(self, round_id: str) -> RoundReport:
        return await self._load(round_id)

    # ── internals ─────────────────────────────────────────────────────

    async def _load(self, round_id: str) -> RoundReport:
        cached = self._rounds.get(round_id)
        row = await self._get_store().get_round(round_id)
        if row is None:
            raise KeyError(f"unknown round {round_id}")
        ops = dict(row.get("ops_summary") or {})
        report = RoundReport(
            round_id=row["id"],
            tenant_id=row.get("tenant_id") or self.tenant_id,
            trigger_source=row.get("trigger_source") or "",
            status=str(ops.get(_K_STATUS) or STATUS_RUNNING),
            steps=(cached.steps if cached is not None else []),
            ops_summary=ops,
            cost=dict(row.get("cost") or {}),
            eval_delta=row.get("eval_delta"),
            edges_superseded_ids=[
                str(x) for x in (ops.get(_K_EDGES) or [])],
            rollback_of=row.get("rollback_of"),
            created_at=row.get("created_at"),
        )
        self._rounds[round_id] = report
        return report

    async def _persist_steps(self, round_id: str, steps: list[StepRecord],
                             superseded_ids: list[str],
                             card_ids: list[str] | None = None) -> None:
        record = self._rounds.get(round_id) or await self._load(round_id)
        ops = dict(record.ops_summary or {})
        ops[_K_STEPS] = [
            {"name": s.name, "status": s.status, "detail": s.detail}
            for s in steps
        ]
        if superseded_ids:
            ops[_K_EDGES] = list(superseded_ids)
            ops["edges_superseded"] = len(superseded_ids)
        # The affected card ids are persisted, not re-derived: rollback must
        # be able to clear exactly the flags this round set, and parsing
        # them back out of a human-readable step detail would break the
        # moment the wording changed.
        ops[_K_CARDS] = list(card_ids or [])
        await self._get_store().update_round(round_id, ops_summary=ops)
        record.ops_summary = ops

    async def _detect_scope_and_propose(
            self, round_id: str) -> tuple[Any, list[StepRecord]]:
        """Call the alignment owner in its documented order.

        Each call is optional at the seam level too: an alignment service
        that predates a method is recorded as skipped rather than crashing
        the round, because a partially capable deployment is a real
        deployment.
        """
        steps: list[StepRecord] = []
        scope: Any = None

        detect = getattr(self.alignment, "detect_doc_change", None)
        if detect is None:
            steps.append(StepRecord("detect_doc_change", "skipped",
                                    "alignment.detect_doc_change absent"))
        else:
            try:
                changes = await detect(round_id)
                scope = changes
                steps.append(StepRecord(
                    "detect_doc_change", "ok",
                    f"{self._len(changes)} change item(s)"))
            except Exception as exc:  # noqa: BLE001 - record, do not abort
                steps.append(StepRecord("detect_doc_change", "failed",
                                        f"{type(exc).__name__}: {exc}"))

        impact = getattr(self.alignment, "impact_scope", None)
        if impact is None:
            steps.append(StepRecord("impact_scope", "skipped",
                                    "alignment.impact_scope absent"))
        else:
            try:
                impact_result = await impact(scope)
                if impact_result is not None:
                    scope = impact_result
                steps.append(StepRecord(
                    "impact_scope", "ok",
                    f"{self._len(self._affected_card_ids(scope))} card(s), "
                    f"{self._len(self._affected_entity_ids(scope))} entity(ies)"))
            except Exception as exc:  # noqa: BLE001
                steps.append(StepRecord("impact_scope", "failed",
                                        f"{type(exc).__name__}: {exc}"))

        propose = getattr(self.alignment, "propose_updates", None)
        if propose is None:
            steps.append(StepRecord("propose_updates", "skipped",
                                    "alignment.propose_updates absent"))
        else:
            try:
                proposals = await propose(round_id)
                if proposals is not None:
                    scope = proposals
                steps.append(StepRecord(
                    "propose_updates", "ok",
                    f"{self._pending_count(proposals)} proposal(s)"))
            except Exception as exc:  # noqa: BLE001
                steps.append(StepRecord("propose_updates", "failed",
                                        f"{type(exc).__name__}: {exc}"))
        return scope, steps

    async def _ingest_new_version(self, scope: Any, round_id: str) -> list[str]:
        """Delegate the controlled supersede to the kg owner.

        ``kg_service.ingest_new_version`` is the contract's endpoint for
        this. Its historical stub raised ``NotImplementedError``; that is
        propagated so the step is recorded as *failed* rather than as an
        empty success. The returned value is the list of edge ids the round
        invalidated, which the ledger must remember for ``rollback``.
        """
        ingest = getattr(self.kg, "ingest_new_version", None)
        if ingest is None:
            raise NotImplementedError(
                "kg service exposes no ingest_new_version")
        changed = self._changed_spans(scope)
        report = await ingest(round_id, changed)
        return self._edge_ids(report)

    async def _commit_ontology(self, scope: Any,
                               round_id: str) -> tuple[bool, str]:
        commit = getattr(self.ontology, "commit_version", None)
        if commit is None:
            return False, "ontology.commit_version absent"
        ops = self._ontology_ops(scope)
        if not ops:
            return False, "no ontology-level op in scope (nothing to commit)"
        version = await commit(ops, trigger_source="standard_update")
        return True, f"ontology version {version} committed"

    # -- scope shape adapters -------------------------------------------------
    # The alignment owner may return a dataclass, a dict or a list. These
    # adapters keep the orchestration readable without forcing a shape onto
    # a service this module does not own; every accessor degrades to empty
    # rather than raising, because "no cards" and "cards we could not read"
    # must not be silently confused - the step detail carries the count.

    @staticmethod
    def _pick(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _len(self, value: Any) -> int:
        try:
            return len(value or [])
        except TypeError:
            return 0

    def _pending_count(self, scope: Any) -> int:
        for key in ("pending", "proposals", "pending_proposals"):
            value = self._pick(scope, key)
            if value is not None:
                return self._len(value)
        return self._len(scope)

    def _affected_card_ids(self, scope: Any) -> list[str]:
        for key in ("affected_decision_ids", "affected_cards",
                    "decision_ids", "D_aff"):
            value = self._pick(scope, key)
            if value:
                return [str(v) for v in value]
        return []

    def _affected_entity_ids(self, scope: Any) -> list[str]:
        for key in ("affected_entity_ids", "affected_entities", "E_aff"):
            value = self._pick(scope, key)
            if value:
                return [str(v) for v in value]
        return []

    def _changed_spans(self, scope: Any) -> list[Any]:
        for key in ("changed_spans", "spans", "changes"):
            value = self._pick(scope, key)
            if value is not None:
                return list(value)
        return list(scope) if isinstance(scope, (list, tuple)) else []

    def _ontology_ops(self, scope: Any) -> list[Any]:
        for key in ("ontology_ops", "ops", "ontology_proposals"):
            value = self._pick(scope, key)
            if value:
                return list(value)
        return []

    def _edge_ids(self, report: Any) -> list[str]:
        for key in ("superseded_edge_ids", "edge_ids", "superseded"):
            value = self._pick(report, key)
            if value is not None:
                return [str(v) for v in value]
        return []
