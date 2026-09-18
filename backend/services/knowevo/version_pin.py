"""
KnowEvo version-pinned traversal (T-09) - the algorithm core of B2.

Literature gap B2 (02-technical-plan 3.2): no prior work constrains a
multi-hop graph walk to the facts that were valid *as of a named knowledge
version*. This module is where that definition lives in code, on purpose:
the pinned-walk predicate is a pure function over edge time windows so it
can be locked by tests that touch neither a database nor an LLM, and so
the T-10b ablation (pinned on/off) has one clean switch to flip.

Definition (frozen, 02-tech-plan 3.2):
    Given version v (ontology version + fact cutoff t_v), a path
    p = (e0, r1, e1, ..., rk) is *valid under v* iff for every edge r_i:
        valid_at(r_i) <= t_v AND (invalid_at(r_i) IS NULL OR invalid_at(r_i) > t_v)
    A version-pinned query Q_v is answered on the subgraph G_v built from
    only the edges valid under v.

Why a graph-side predicate instead of "just filter in the prompt": recent
work shows cosine similarity barely separates expired facts (AUROC ~0.59),
so retrieval alone is structurally wrong for version-sensitive questions.
Constraining the walk itself is the honest fix.

Contract: knowevo/backend/services/knowevo/decision_service.py.md;
memo 04-K3; algorithm source 02-technical-plan 3.2.
Design inspired by: graphiti's bi-temporal validity windows (attribution
per 03-development-plan 4.2).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from database.knowevo_db import valid_now

# Re-exported so callers have one import for the whole version-pin story
# (the predicate itself stays in knowevo_db where bi-temporal semantics
# live in exactly one place - pitfall #9).
__all__ = [
    "VersionClock",
    "edge_in_version",
    "filter_paths_by_version",
    "path_version_valid",
    "pin_predicate",
    "resolve_version_clock",
]


class VersionClock:
    """A resolved knowledge version: its label plus the fact cutoff t_v.

    ``ontology_version`` is the label that lands in the decision card's
    knowledge_stamp; ``as_of`` is t_v, the instant against which every
    edge's validity window is tested. ``source`` records how t_v was
    derived ("explicit" | "fact_cutoff" | "version_created_at" | "now") so
    the card can be honest about it instead of implying a precision it
    does not have.
    """

    def __init__(self, ontology_version: str | None, as_of: datetime,
                 source: str = "now"):
        self.ontology_version = ontology_version
        self.as_of = _ensure_aware(as_of)
        self.source = source

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (f"VersionClock(version={self.ontology_version!r}, "
                f"as_of={self.as_of.isoformat()}, source={self.source!r})")

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, VersionClock)
                and other.ontology_version == self.ontology_version
                and other.as_of == self.as_of
                and other.source == self.source)


def _ensure_aware(dt: datetime) -> datetime:
    """Normalize naive datetimes to UTC.

    Mixing naive and aware datetimes raises TypeError at comparison time,
    and the ORM/PG round trip can hand back either (a TIMESTAMPTZ column is
    aware, but test fixtures and hand-built dicts often are not). One
    normalization point keeps the comparison below total.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _coerce_datetime(value: Any) -> datetime | None:
    """Accept a datetime or an ISO-8601 string (JSONB round trip).

    A version row's ``fact_cutoff`` lives in the ``metrics`` JSONB column,
    so it comes back as a string; an in-memory row (tests, FakeStore) may
    carry a real datetime. Both must resolve to the same t_v. Malformed
    input returns None so the caller falls through to the next source
    rather than pinning against garbage.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return _ensure_aware(parsed)


def resolve_version_clock(ontology_version: str | None,
                          as_of: datetime | None = None,
                          versions: Sequence[dict[str, Any]] | None = None,
                          ) -> VersionClock:
    """Resolve a version label into a concrete fact cutoff t_v.

    Resolution order, most specific first (T-18b D1 added step 2):
      1. an explicit ``as_of`` wins outright (deterministic demos/tests);
      2. the version row's ``fact_cutoff`` - the business-time upper bound
         of the facts that version covered, recorded when the version was
         committed (T-18b). This is the *correct* t_v: it is the facts'
         own time axis, not the wall-clock moment we published the version;
      3. the ``created_at`` of the matching row in ``versions`` - the
         moment that knowledge version came into being. Kept as the
         fallback for versions committed before fact_cutoff existed;
      4. ``now()`` as the honest fallback (``source="now"``) - callers must
         surface this, because a card claiming version pinning while
         having no version is worse than one admitting it.
    """
    if as_of is not None:
        return VersionClock(ontology_version, as_of, source="explicit")
    if ontology_version and versions:
        for row in versions:
            if row.get("version") != ontology_version:
                continue
            cutoff = _coerce_datetime(row.get("fact_cutoff"))
            if cutoff is not None:
                return VersionClock(ontology_version, cutoff,
                                    source="fact_cutoff")
            created = _coerce_datetime(row.get("created_at"))
            if created is not None:
                return VersionClock(ontology_version, created,
                                    source="version_created_at")
            break
    return VersionClock(ontology_version, datetime.now(UTC), source="now")


def pin_predicate(model, clock: VersionClock | datetime):
    """The pinned-walk predicate as a SQLAlchemy filter expression.

    Delegates to ``valid_now(model, as_of)`` so the bi-temporal comparison
    exists once in the codebase (pitfall #9: the temporal predicate must
    live in one place or the two copies drift). ``model`` is a mapped
    class (KgRelation / KgEntity); ``clock`` is a VersionClock or a raw
    datetime.
    """
    t_v = clock.as_of if isinstance(clock, VersionClock) else clock
    return valid_now(model, as_of=_ensure_aware(t_v))


def edge_in_version(valid_at: datetime | None, invalid_at: datetime | None,
                    clock: VersionClock | datetime) -> bool:
    """Pure predicate: is one edge's validity window open at t_v?

    This is the definition from 02-tech-plan 3.2 transcribed literally, and
    the function the ablation tests pin down. A missing ``valid_at`` means
    "always existed" (the column is NOT NULL with a now() default in the
    schema, so None only appears in hand-built fixtures); ``invalid_at``
    None means "still valid".
    """
    t_v = clock.as_of if isinstance(clock, VersionClock) else _ensure_aware(clock)
    t_v = _ensure_aware(t_v)
    expires_before = invalid_at is not None and _ensure_aware(invalid_at) <= t_v
    starts_after = valid_at is not None and _ensure_aware(valid_at) > t_v
    return not (starts_after or expires_before)


def path_version_valid(edges: Iterable[Any],
                       clock: VersionClock | datetime) -> bool:
    """Is a whole path valid under ``clock``? Every edge must qualify.

    Accepts EdgeCards, plain dicts or any object exposing valid_at /
    invalid_at, because the walk produces EdgeCards while the ablation
    harness replays plain dicts out of the eval set.
    """
    for edge in edges:
        if isinstance(edge, dict):
            valid_at = edge.get("valid_at")
            invalid_at = edge.get("invalid_at")
        else:
            valid_at = getattr(edge, "valid_at", None)
            invalid_at = getattr(edge, "invalid_at", None)
        if not edge_in_version(valid_at, invalid_at, clock):
            return False
    return True


def filter_paths_by_version(paths: Iterable[Any],
                            edges_by_path: dict[int, list[Any]],
                            clock: VersionClock | datetime) -> list[Any]:
    """Keep only the paths whose every edge is valid under ``clock``.

    ``edges_by_path`` maps ``id(path)`` to the path's EdgeCards: the Path
    dataclass carries edge ids (for storage lookup), not the edges
    themselves, so the caller has already resolved ids to cards. Passing
    the mapping in keeps this function pure and free of store access.

    A path that claims hops but has no edge mapping is dropped rather than
    treated as vacuously valid: "we could not check it" must not become
    "we verified it", or the pinned flag stops meaning anything.
    """
    kept = []
    for path in paths:
        edges = edges_by_path.get(id(path))
        entities = getattr(path, "entities", None)
        if not edges and entities is not None and len(entities) > 1:
            continue
        if path_version_valid(edges or [], clock):
            kept.append(path)
    return kept
