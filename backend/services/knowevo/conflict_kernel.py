"""A4 knowledge-conflict reconciliation kernel (spec 05 §3.4 three-layer).

Pure function, stdlib-only: no DB, no LLM, no third-party imports. The
three frozen layers are:

1. detect  - pairwise conflict candidates: same conflict key, disagreeing
   values, overlapping bi-temporal windows;
2. classify - closed attribution vocabulary (EvoTrustRAG-style causality):
   EVOLUTION / SOURCE_AUTHORITY / EXTRACTION_ERROR / SAME_SOURCE;
3. resolve  - authority rank + version clock (bi-temporal), with an
   optional LLM seam that is NEVER called when ``llm=None``.

Every candidate produces one ``ConflictAdjudicationRecord``: traceable and
replayable (deterministic conflict_id + replay_key). The record's
``to_wire()`` matches the frozen ``schemas.ConflictAdjudication`` triple
(conflict_id / type / resolution) so a later wiring step can backfill
decision-card ``conflict_adjudications`` without reshaping the payload.

Window convention matches ``graph_store`` / migration kw_011: half-open
``[valid_at, invalid_at)``. Touching boundaries do NOT overlap; an
instantaneous window ``valid_at == invalid_at`` is empty and never
conflicts. ``authority_level`` follows the platform convention
(1 national standard .. 4 popular science): SMALLER rank wins.

Spec anchor (workspace ``archive/旧计划书/05-项目综合评估与优化路线-独立评审.md``
§3.4, verbatim Chinese): "三层算法 = 冲突检测 / 冲突分类 / 消解策略；输出每条冲突的裁决记录。"
= three layers detect / classify / resolve; emit one adjudication record.

Honest layering: production wiring (persisting records, stamping
``contested``, calling ``graph_store.supersede``) is NOT this module. It is
a pure kernel the service layer will call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# --- Frozen taxonomy (spec §3.4 closed attribution vocabulary) --------------
# Closed vocabulary: classify_conflict returns exactly one of these four.


class ConflictKind(str, Enum):
    """Closed attribution vocabulary for one conflict pair."""

    EVOLUTION = "EVOLUTION"
    SOURCE_AUTHORITY = "SOURCE_AUTHORITY"
    EXTRACTION_ERROR = "EXTRACTION_ERROR"
    SAME_SOURCE = "SAME_SOURCE"


CONFLICT_KINDS = (
    ConflictKind.EVOLUTION.value,
    ConflictKind.SOURCE_AUTHORITY.value,
    ConflictKind.EXTRACTION_ERROR.value,
    ConflictKind.SAME_SOURCE.value,
)

# Fact shapes.
FACT_RELATION = "relation"
FACT_ATTRIBUTE = "attribute"
FACT_TYPES = (FACT_RELATION, FACT_ATTRIBUTE)

# Resolution / policy literals (frozen).
PREFER_AUTHORITY = "authority"
PREFER_VERSION = "version"
PREFER_CHOICES = (PREFER_AUTHORITY, PREFER_VERSION)

RESOLUTION_AUTHORITY_WIN = "authority_win"
RESOLUTION_VERSION_WIN = "version_win"
RESOLUTION_TIE_BREAK_ID = "tie_break_id"
RESOLUTION_REQUEUE_EXTRACTION = "requeue_extraction"
RESOLUTION_HUMAN_REVIEW = "human_review"
RESOLUTION_LLM_SEAM = "llm_seam"

_MIN_DT = datetime.min.replace(tzinfo=UTC)
_MAX_DT = datetime.max.replace(tzinfo=UTC)


# --- Helpers ----------------------------------------------------------------


def _aware(dt: datetime | None) -> datetime | None:
    """Naive datetimes are read as UTC (same policy as version_pin)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _clock_instant(version_clock: Any) -> datetime:
    """Accept a datetime or a VersionClock-like object carrying ``as_of``."""
    if isinstance(version_clock, datetime):
        return _aware(version_clock)  # type: ignore[return-value]
    as_of = getattr(version_clock, "as_of", None)
    if isinstance(as_of, datetime):
        return _aware(as_of)  # type: ignore[return-value]
    raise TypeError(
        "version_clock must be a datetime or expose .as_of as datetime, "
        f"got {version_clock!r}"
    )


def _window_lo(fact: Fact) -> datetime:
    return _aware(fact.valid_at) or _MIN_DT


def _window_hi(fact: Fact) -> datetime:
    return _aware(fact.invalid_at) or _MAX_DT


def windows_overlap(a: Fact, b: Fact) -> bool:
    """Half-open [lo, hi) overlap. Touching endpoints do NOT overlap.

    An instantaneous window (``valid_at == invalid_at``) is empty and never
    overlaps anything, matching the half-open range form on the store.
    """
    alo, ahi = _window_lo(a), _window_hi(a)
    blo, bhi = _window_lo(b), _window_hi(b)
    if alo >= ahi or blo >= bhi:
        return False
    return alo < bhi and blo < ahi


def valid_at_clock(fact: Fact, clock: datetime) -> bool:
    """Is the fact inside the current view at ``clock``? ([lo, hi) containment)."""
    t = _aware(clock)
    return _window_lo(fact) <= t < _window_hi(fact)  # type: ignore[operator]


# --- Input facts -------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    """One asserted knowledge fact with a bi-temporal window and provenance.

    ``fact_type="relation"``: key = (subject, predicate, object); ``value`` is
    the assertion content that can contradict (polarity / qualifier / dosage
    class). ``fact_type="attribute"``: key = (subject, predicate); ``value`` is
    the attribute value. Two facts conflict only when their keys match, their
    values disagree, and their windows overlap.

    ``source_id`` names the asserting document; ``source_group`` names the
    lineage/series (e.g. one guideline across years) and ``version`` the
    edition tag - together they drive the EVOLUTION vs SAME_SOURCE split.
    ``authority_level`` uses the platform scale (1 national std .. 4 popular
    science): smaller is more authoritative.
    """

    fact_id: str
    fact_type: str
    subject: str
    predicate: str
    object: str = ""
    value: str = ""
    source_id: str = ""
    source_group: str = ""
    version: str = ""
    authority_level: int = 3
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    claim: str = ""  # passthrough provenance text; kernel does not consume it

    def __post_init__(self) -> None:
        if not self.fact_id:
            raise ValueError("fact_id must be non-empty")
        if self.fact_type not in FACT_TYPES:
            raise ValueError(
                f"fact_type must be one of {FACT_TYPES}, got {self.fact_type!r}"
            )
        if not self.subject or not self.predicate:
            raise ValueError("subject and predicate must be non-empty")
        if self.fact_type == FACT_RELATION and not self.object:
            raise ValueError("relation facts require a non-empty object")
        lo = _aware(self.valid_at)
        hi = _aware(self.invalid_at)
        if lo is not None and hi is not None and hi < lo:
            raise ValueError(
                f"invalid_at {hi.isoformat()} precedes valid_at {lo.isoformat()}"
            )
        # Normalise to aware-UTC so later comparisons never see mixed kinds.
        object.__setattr__(self, "valid_at", lo)
        object.__setattr__(self, "invalid_at", hi)
        if isinstance(self.authority_level, bool) or not isinstance(
            self.authority_level, int
        ):
            raise TypeError("authority_level must be an int")
        if self.authority_level < 1:
            raise ValueError("authority_level must be >= 1 (platform scale 1..4)")

    def conflict_key(self) -> tuple[str, str, str, str]:
        """Identity key: relation includes the object; attribute does not."""
        if self.fact_type == FACT_RELATION:
            return (FACT_RELATION, self.subject, self.predicate, self.object)
        return (FACT_ATTRIBUTE, self.subject, self.predicate, "")


# --- Detection ---------------------------------------------------------------


@dataclass(frozen=True)
class ConflictCandidate:
    """One disagreeing pair on the same key with overlapping windows.

    ``left.fact_id`` is always lexicographically <= ``right.fact_id`` so the
    candidate (and its conflict_id) is independent of input order.
    """

    left: Fact
    right: Fact
    key: tuple[str, str, str, str]

    @property
    def conflict_id(self) -> str:
        raw = f"{self.key}|{self.left.fact_id}|{self.right.fact_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def detect_conflicts(facts: list[Fact] | tuple[Fact, ...]) -> list[ConflictCandidate]:
    """Pairwise conflict candidates: same key, disagreeing value, overlapping window.

    Deterministic output order: (conflict_key, left.fact_id, right.fact_id).
    Duplicate fact_ids raise ValueError (ids key the adjudication records).
    """
    seen: dict[str, Fact] = {}
    for f in facts:
        if f.fact_id in seen:
            raise ValueError(f"duplicate fact_id {f.fact_id!r}")
        seen[f.fact_id] = f

    groups: dict[tuple[str, str, str, str], list[Fact]] = {}
    for f in seen.values():
        groups.setdefault(f.conflict_key(), []).append(f)

    out: list[ConflictCandidate] = []
    for key, members in groups.items():
        members = sorted(members, key=lambda f: f.fact_id)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                if a.value == b.value:
                    continue
                if not windows_overlap(a, b):
                    continue
                out.append(ConflictCandidate(left=a, right=b, key=key))
    out.sort(key=lambda c: (c.key, c.left.fact_id, c.right.fact_id))
    return out


# --- Classification context --------------------------------------------------


@dataclass(frozen=True)
class ClassifyContext:
    """Caller-supplied attribution signals (all optional).

    ``extraction_error_ids`` is the only cross-fact signal the kernel
    consumes for EXTRACTION_ERROR; same-source / evolution read provenance
    already on the facts (source_id / source_group / version).
    """

    extraction_error_ids: frozenset[str] = field(default_factory=frozenset)


def classify_conflict(
    candidate: ConflictCandidate,
    context: ClassifyContext | None = None,
) -> ConflictKind:
    """Closed-vocabulary attribution. First matching rule wins.

    1. EXTRACTION_ERROR - either side is flagged as a suspect extraction;
    2. SAME_SOURCE - same source_id and same version (self-contradiction);
    3. EVOLUTION - same lineage (source_group) or same source_id, but the
       edition/version tags differ (old vs new knowledge);
    4. SOURCE_AUTHORITY - everything else (guideline vs label, textbook vs
       leaflet, ...): a cross-source disagreement.
    """
    ctx = context or ClassifyContext()
    left, right = candidate.left, candidate.right
    if (
        left.fact_id in ctx.extraction_error_ids
        or right.fact_id in ctx.extraction_error_ids
    ):
        return ConflictKind.EXTRACTION_ERROR

    same_source = bool(left.source_id) and left.source_id == right.source_id
    same_group = bool(left.source_group) and left.source_group == right.source_group
    versions_differ = (left.version or "") != (right.version or "")

    if same_source and not versions_differ:
        return ConflictKind.SAME_SOURCE
    if (same_group or same_source) and versions_differ:
        return ConflictKind.EVOLUTION
    return ConflictKind.SOURCE_AUTHORITY


# --- Resolution --------------------------------------------------------------


@dataclass(frozen=True)
class ConflictAdjudicationRecord:
    """One replayable adjudication record (spec §3.4).

    ``to_wire()`` projects the frozen ``schemas.ConflictAdjudication`` triple
    so decision-card backfill stays a field rename, not a redesign.
    """

    conflict_id: str
    kind: str
    resolution: str
    winner_id: str
    loser_id: str
    winner_source: str
    loser_source: str
    basis: str
    policy: str
    version_clock_iso: str
    winner_authority: int
    loser_authority: int
    winner_valid_at_iso: str
    loser_valid_at_iso: str
    llm_called: bool
    contested: bool
    superseded_ids: tuple[str, ...]
    replay_key: str

    def to_wire(self) -> dict[str, str]:
        """Frozen triple for schemas.ConflictAdjudication (conflict_id/type/resolution)."""
        return {
            "conflict_id": self.conflict_id,
            "type": self.kind,
            "resolution": self.resolution,
        }


def _authority_of(fact: Fact, authority_rank: Mapping[str, int] | None) -> int:
    if authority_rank and fact.source_id in authority_rank:
        return int(authority_rank[fact.source_id])
    return fact.authority_level


def _version_key(fact: Fact, clock: datetime) -> tuple[int, datetime]:
    """Higher key wins under the version axis: current-view first, then newer."""
    return (1 if valid_at_clock(fact, clock) else 0, _window_lo(fact))


def _better_by_version(a: Fact, b: Fact, clock: datetime) -> Fact | None:
    ka, kb = _version_key(a, clock), _version_key(b, clock)
    if ka > kb:
        return a
    if kb > ka:
        return b
    return None


def _better_by_authority(
    a: Fact, b: Fact, authority_rank: Mapping[str, int] | None
) -> Fact | None:
    ra, rb = _authority_of(a, authority_rank), _authority_of(b, authority_rank)
    if ra < rb:
        return a
    if rb < ra:
        return b
    return None


def _by_id(a: Fact, b: Fact) -> Fact:
    return a if a.fact_id <= b.fact_id else b


def _replay_key(
    candidate: ConflictCandidate,
    kind: ConflictKind,
    winner_id: str,
    resolution: str,
    policy: str,
    clock_iso: str,
) -> str:
    payload = {
        "conflict_id": candidate.conflict_id,
        "kind": kind.value,
        "winner_id": winner_id,
        "resolution": resolution,
        "policy": policy,
        "version_clock_iso": clock_iso,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def resolve_conflict(
    candidate: ConflictCandidate,
    *,
    authority_rank: Mapping[str, int] | None,
    version_clock: Any,
    llm: Callable[[ConflictCandidate], Any] | None = None,
    prefer: str | None = None,
    context: ClassifyContext | None = None,
) -> ConflictAdjudicationRecord:
    """Adjudicate one candidate. Authority + bi-temporal clock; LLM is a seam.

    Kind-specific defaults (spec §3.4 resolution policies), overridable via
    ``prefer``:
    - EVOLUTION        -> version first (recency); old fact superseded-but-kept;
    - SOURCE_AUTHORITY -> authority first (smaller rank wins); dissent kept;
    - EXTRACTION_ERROR -> the flagged side loses (requeue extraction);
    - SAME_SOURCE      -> contested + human_review; a provisional winner
      is still recorded so the card has something to show, but ``contested``.

    ``llm`` is invoked ONLY for SAME_SOURCE and ONLY when provided. With
    ``llm=None`` the seam never runs - pure rules, deterministic tie-break by
    fact_id. **The llm return value is ignored** (the seam only records that a
    judge was consulted; it does not un-contest or rewrite the rule winner).
    ``authority_rank`` maps source_id -> rank; smaller wins (platform
    scale). Missing keys fall back to ``Fact.authority_level``.
    """
    if prefer is not None and prefer not in PREFER_CHOICES:
        raise ValueError(f"prefer must be one of {PREFER_CHOICES} or None, got {prefer!r}")
    clock = _clock_instant(version_clock)
    clock_iso = clock.isoformat()
    kind = classify_conflict(candidate, context)
    left, right = candidate.left, candidate.right
    ctx = context or ClassifyContext()

    winner: Fact
    loser: Fact
    resolution: str
    contested = False
    llm_called = False
    policy = prefer or (
        PREFER_VERSION if kind is ConflictKind.EVOLUTION else PREFER_AUTHORITY
    )

    if kind is ConflictKind.EXTRACTION_ERROR:
        left_err = left.fact_id in ctx.extraction_error_ids
        right_err = right.fact_id in ctx.extraction_error_ids
        if left_err and not right_err:
            winner, loser = right, left
        elif right_err and not left_err:
            winner, loser = left, right
        else:
            # Both sides flagged: no trustworthy winner; keep contested.
            winner = _by_id(left, right)
            loser = right if winner is left else left
            contested = True
        resolution = RESOLUTION_REQUEUE_EXTRACTION

    elif kind is ConflictKind.SAME_SOURCE:
        # No principled auto-winner inside one source: provisional + contested.
        primary = (
            _better_by_authority(left, right, authority_rank)
            if policy == PREFER_AUTHORITY
            else _better_by_version(left, right, clock)
        )
        secondary = (
            _better_by_version(left, right, clock)
            if policy == PREFER_AUTHORITY
            else _better_by_authority(left, right, authority_rank)
        )
        if primary is not None:
            winner = primary
        elif secondary is not None:
            winner = secondary
        else:
            winner = _by_id(left, right)
        loser = right if winner is left else left
        contested = True
        resolution = RESOLUTION_HUMAN_REVIEW
        if llm is not None:
            llm(candidate)
            llm_called = True
            resolution = RESOLUTION_LLM_SEAM

    else:
        # EVOLUTION / SOURCE_AUTHORITY: configured two-axis comparison.
        if policy == PREFER_AUTHORITY:
            primary = _better_by_authority(left, right, authority_rank)
            secondary = _better_by_version(left, right, clock)
            primary_res, secondary_res = (
                RESOLUTION_AUTHORITY_WIN,
                RESOLUTION_VERSION_WIN,
            )
        else:
            primary = _better_by_version(left, right, clock)
            secondary = _better_by_authority(left, right, authority_rank)
            primary_res, secondary_res = (
                RESOLUTION_VERSION_WIN,
                RESOLUTION_AUTHORITY_WIN,
            )
        if primary is not None:
            winner, resolution = primary, primary_res
        elif secondary is not None:
            winner, resolution = secondary, secondary_res
        else:
            winner, resolution = _by_id(left, right), RESOLUTION_TIE_BREAK_ID
        loser = right if winner is left else left

    superseded = (loser.fact_id,)
    record = ConflictAdjudicationRecord(
        conflict_id=candidate.conflict_id,
        kind=kind.value,
        resolution=resolution,
        winner_id=winner.fact_id,
        loser_id=loser.fact_id,
        winner_source=winner.source_id,
        loser_source=loser.source_id,
        basis=(
            f"kind={kind.value}; policy={policy}; "
            f"authority {winner.fact_id}={_authority_of(winner, authority_rank)} "
            f"vs {loser.fact_id}={_authority_of(loser, authority_rank)}; "
            f"clock={clock_iso}"
        ),
        policy=policy,
        version_clock_iso=clock_iso,
        winner_authority=_authority_of(winner, authority_rank),
        loser_authority=_authority_of(loser, authority_rank),
        winner_valid_at_iso=(
            winner.valid_at.isoformat() if winner.valid_at is not None else ""
        ),
        loser_valid_at_iso=(
            loser.valid_at.isoformat() if loser.valid_at is not None else ""
        ),
        llm_called=llm_called,
        contested=contested,
        superseded_ids=superseded,
        replay_key=_replay_key(
            candidate, kind, winner.fact_id, resolution, policy, clock_iso
        ),
    )
    return record


# --- Batch -------------------------------------------------------------------


@dataclass(frozen=True)
class ReconcileResult:
    """Full pass over a fact set: every candidate and its adjudication."""

    candidates: tuple[ConflictCandidate, ...]
    records: tuple[ConflictAdjudicationRecord, ...]
    kind_counts: dict[str, int] = field(default_factory=dict)

    @property
    def n_conflicts(self) -> int:
        return len(self.candidates)

    @property
    def n_contested(self) -> int:
        return sum(1 for r in self.records if r.contested)

    @property
    def superseded_fact_ids(self) -> tuple[str, ...]:
        ids = {i for r in self.records for i in r.superseded_ids}
        return tuple(sorted(ids))


def reconcile(
    facts: list[Fact] | tuple[Fact, ...],
    *,
    authority_rank: Mapping[str, int] | None = None,
    version_clock: Any,
    llm: Callable[[ConflictCandidate], Any] | None = None,
    prefer: str | None = None,
    context: ClassifyContext | None = None,
) -> ReconcileResult:
    """Detect + classify + resolve over one fact set. Order-independent.

    Records follow the deterministic candidate order; kind_counts is keyed by
    the frozen vocabulary strings so an empty input yields four zero rows.
    """
    candidates = detect_conflicts(list(facts))
    records = tuple(
        resolve_conflict(
            c,
            authority_rank=authority_rank,
            version_clock=version_clock,
            llm=llm,
            prefer=prefer,
            context=context,
        )
        for c in candidates
    )
    counts = {k: 0 for k in CONFLICT_KINDS}
    for r in records:
        counts[r.kind] += 1
    return ReconcileResult(
        candidates=tuple(candidates), records=records, kind_counts=counts
    )
