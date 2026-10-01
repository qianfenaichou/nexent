"""Additive production seam for the A4 conflict kernel.

Maps graph rows (``kg_relation_t`` + document provenance) onto
:class:`~services.knowevo.conflict_kernel.Fact`, runs the frozen
detect/classify/resolve pipeline, and projects each record onto the frozen
``schemas.ConflictAdjudication`` wire triple. Default callers that never
import this module see zero behaviour change.

Design: ``competition/docs/tech-optimization-2026-09-28/
a4-wire-assessment-2026-09-30.md``. Contract:
``knowevo/backend/services/knowevo/conflict_adapter.py.md``.

Hard boundaries (unchanged from the kernel):
- pure mapping + pure reconcile; no DB, no LLM, no ``graph_store.supersede``;
- adjudication records are for the decision card / audit trail only —
  production write-back remains an explicit later step;
- ``value`` is caller-filled. For real-corpus E10 use
  :func:`polarity_value` (A+B heuristic: rel_type vocab + claim
  keywords) so complementary multi-claim groups do not disagree.
  Passing raw ``claim`` as value over-reports and is reserved for
  kg_service CONTRA identity tests only.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from services.knowevo.conflict_kernel import (
    Fact,
    ReconcileResult,
    reconcile,
)
from services.knowevo.schemas import ConflictAdjudication

__all__ = [
    "CLAIM_NEG_TOKENS",
    "CLAIM_POS_TOKENS",
    "POLARITY_NEG",
    "POLARITY_NEUTRAL",
    "POLARITY_POS",
    "REL_TYPE_POLARITY",
    "facts_from_relation_rows",
    "lineage_key_from_title",
    "make_relation_conflict_observer",
    "polarity_value",
    "reconcile_relation_rows",
    "records_to_adjudications",
    "relation_row_to_fact",
    "value_from_claim",
    "version_tag_from_title",
]


# --- A+B polarity heuristic (e10-input-prep 2026-09-30 §3.2) ---------------
# Real-corpus value proxy: rel_type polarity vocabulary (A) refined by claim
# keywords (B). Not a gold label - multi-claim groups stay groups.

POLARITY_POS = "pos"
POLARITY_NEG = "neg"
POLARITY_NEUTRAL = "neutral"

# A: rel_type closed vocabulary (clinical assertion direction).
REL_TYPE_POLARITY: dict[str, str] = {
    # positive / recommended
    "indicated_for": POLARITY_POS,
    "indicated_after": POLARITY_POS,
    "indicated_for_population": POLARITY_POS,
    "recommended_for": POLARITY_POS,
    "preferred_for": POLARITY_POS,
    "first_line": POLARITY_POS,
    "approved_for": POLARITY_POS,
    "treats": POLARITY_POS,
    "treated_by": POLARITY_POS,
    "treated_with": POLARITY_POS,
    "prevents": POLARITY_POS,
    "used_for": POLARITY_POS,
    "used_for_diagnosis_of": POLARITY_POS,
    "improves": POLARITY_POS,
    "improves_outcome": POLARITY_POS,
    "improves_outcome_for": POLARITY_POS,
    "improves_indicator": POLARITY_POS,
    "benefits": POLARITY_POS,
    "reduces_risk": POLARITY_POS,
    "reduces_risk_of": POLARITY_POS,
    "decreases_risk": POLARITY_POS,
    "lowers": POLARITY_POS,
    "reduces": POLARITY_POS,
    # negative / contraindicated
    "contraindicated_for": POLARITY_NEG,
    "contraindicated_with": POLARITY_NEG,
    "contraindicated_in": POLARITY_NEG,
    "not_recommended_for": POLARITY_NEG,
    "not_indicated_for": POLARITY_NEG,
    "has_adverse_effect": POLARITY_NEG,
    "avoid": POLARITY_NEG,
    "suspend": POLARITY_NEG,
    "discontinue_concurrent_drug": POLARITY_NEG,
    "caution": POLARITY_NEG,
    "caution_for": POLARITY_NEG,
    "caution_indicated_for": POLARITY_NEG,
    "precaution": POLARITY_NEG,
    "increases_risk": POLARITY_NEG,
    "increases": POLARITY_NEG,
    "has_risk_of": POLARITY_NEG,
    "risk_factor_for": POLARITY_NEG,
    "has_risk_factor": POLARITY_NEG,
    "has_risk_group": POLARITY_NEG,
    "predisposes_to": POLARITY_NEG,
    "exacerbates": POLARITY_NEG,
    "may_cause": POLARITY_NEG,
    "not_causes": POLARITY_NEG,
    "does_not_increase_risk_of": POLARITY_NEG,
    "no_effect": POLARITY_NEG,
    "has_no_effect_on": POLARITY_NEG,
}

# B: claim keywords. Negative hits dominate when both fire (conservative).
CLAIM_NEG_TOKENS = (
    "禁忌", "停用", "禁用", "不可", "不宜", "不应", "避免", "不推荐",
    "慎用", "禁止", "不得", "严禁", "不建议", "停药", "暂停",
)
CLAIM_POS_TOKENS = (
    "首选", "推荐", "适用", "一线", "可作为", "可用", "应用", "获益",
    "有效", "建议", "采用", "治疗", "使用",
)


def polarity_value(rel_type: str, claim: str) -> str:
    """A+B polarity proxy for ``Fact.value`` (real-corpus E10 only).

    Returns one of ``pos`` / ``neg`` / ``neutral``. Rule order:
    1. claim keyword hit (neg wins over pos if both);
    2. else rel_type vocabulary;
    3. else ``neutral``.

    This is a **heuristic, not gold**. Complementary multi-claim groups
    (e.g. different patient subpopulations on one triple) share polarity
    and therefore do *not* disagree on value - which is the point.
    """
    text = claim or ""
    has_neg = any(tok in text for tok in CLAIM_NEG_TOKENS)
    has_pos = any(tok in text for tok in CLAIM_POS_TOKENS)
    if has_neg:
        return POLARITY_NEG
    if has_pos:
        return POLARITY_POS
    return REL_TYPE_POLARITY.get((rel_type or "").strip().lower(), POLARITY_NEUTRAL)


def value_from_claim(rel_type: str, claim: str) -> str:
    """Preferred ``Fact.value`` extractor for real-corpus rows.

    Thin alias of :func:`polarity_value` (A+B). **Do not** pass raw claim
    text as ``value`` on multi-claim corpora: complementary qualifiers on
    one triple then disagree and the kernel over-reports. Claim stays in
    ``Fact.claim`` for provenance only.
    """
    return polarity_value(rel_type, claim)


# --- row -> Fact ------------------------------------------------------------


def lineage_key_from_title(title: str) -> str:
    """Stable series key: title with a trailing year/version marker stripped.

    ``中国老年糖尿病诊疗指南（2024版）`` and ``中国老年糖尿病诊疗指南（2021年版）``
    share one lineage so classify can split EVOLUTION from SOURCE_AUTHORITY.
    Empty title yields empty key (never invented).
    """
    t = (title or "").strip()
    if not t:
        return ""
    for closer, opener in (("）", "（"), (")", "(")):
        if t.endswith(closer):
            i = t.rfind(opener)
            if i > 0:
                inner = t[i + 1 : -1]
                if any(ch.isdigit() for ch in inner) and (
                    "版" in inner or "年" in inner or len(inner) <= 8
                ):
                    return t[:i].strip()
    return t


def version_tag_from_title(title: str) -> str:
    """Version/edition tag if the title carries one; else empty string."""
    t = (title or "").strip()
    if not t:
        return ""
    for closer, opener in (("）", "（"), (")", "(")):
        if t.endswith(closer):
            i = t.rfind(opener)
            if i > 0:
                inner = t[i + 1 : -1]
                if any(ch.isdigit() for ch in inner):
                    return inner.strip()
    return ""


def _as_aware_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"infinity", "none", "null"}:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def relation_row_to_fact(
    row: Mapping[str, Any],
    *,
    doc: Mapping[str, Any] | None = None,
    value: str | None = None,
) -> Fact:
    """One ``kg_relation_t`` row -> one relation :class:`Fact`.

    ``value`` is **caller-filled**. For real corpora pass
    ``value_from_claim(rel_type, claim)`` (A+B polarity). The fallback
    default of raw ``claim`` text is only the kg_service CONTRA identity
    rule and **over-reports** on complementary multi-claim groups (E10
    measured 200 claim-identity pairs vs 38 true polarity disagreements
    on the build tenant). Provenance comes from ``doc``
    (``doc_asset_t`` projection): title -> source_group/version,
    ``authority_level`` -> Fact.authority_level, ``published_at`` is *not*
    copied into valid_at (the edge's business time already lives on the row).
    """
    fact_id = str(row.get("id") or "").strip()
    subject = str(row.get("src") or "").strip()
    predicate = str(row.get("rel_type") or "").strip()
    obj = str(row.get("dst") or "").strip()
    claim = str(row.get("claim") or "")
    doc = doc or {}
    title = str(doc.get("title") or "")
    auth = doc.get("authority_level", row.get("authority_level", 3))
    try:
        auth_i = int(auth) if auth is not None else 3
    except (TypeError, ValueError):
        auth_i = 3
    source_id = str(
        doc.get("id") or row.get("doc_id") or row.get("source_id") or ""
    ).strip()
    return Fact(
        fact_id=fact_id,
        fact_type="relation",
        subject=subject,
        predicate=predicate,
        object=obj,
        value=(claim if value is None else str(value)),
        source_id=source_id,
        source_group=lineage_key_from_title(title),
        version=version_tag_from_title(title),
        authority_level=auth_i,
        valid_at=_as_aware_or_none(row.get("valid_at")),
        invalid_at=_as_aware_or_none(row.get("invalid_at")),
        claim=claim,
    )


def facts_from_relation_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    docs: Mapping[str, Mapping[str, Any]] | None = None,
    values: Mapping[str, str] | None = None,
) -> list[Fact]:
    """Map many relation rows. ``docs``/``values`` are keyed by row ``id``."""
    docs = docs or {}
    values = values or {}
    out: list[Fact] = []
    for row in rows:
        rid = str(row.get("id") or "")
        out.append(
            relation_row_to_fact(
                row,
                doc=docs.get(rid),
                value=values.get(rid),
            )
        )
    return out


# --- records -> wire --------------------------------------------------------


def records_to_adjudications(
    records: Sequence[Any],
) -> list[ConflictAdjudication]:
    """Project kernel records onto the frozen ``ConflictAdjudication`` triple.

    Accepts ``ConflictAdjudicationRecord`` (via ``to_wire``) or a plain
    mapping already holding ``conflict_id``/``type``/``resolution``.
    """
    out: list[ConflictAdjudication] = []
    for rec in records:
        if hasattr(rec, "to_wire"):
            wire = rec.to_wire()
        elif isinstance(rec, Mapping):
            wire = {
                "conflict_id": str(rec.get("conflict_id", "")),
                "type": str(rec.get("type") or rec.get("kind", "")),
                "resolution": str(rec.get("resolution", "")),
            }
        else:
            continue
        out.append(
            ConflictAdjudication(
                conflict_id=str(wire.get("conflict_id", "")),
                type=str(wire.get("type", "")),
                resolution=str(wire.get("resolution", "")),
            )
        )
    return out


# --- batch facade -----------------------------------------------------------


def reconcile_relation_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    docs: Mapping[str, Mapping[str, Any]] | None = None,
    values: Mapping[str, str] | None = None,
    version_clock: Any,
    authority_rank: Mapping[str, int] | None = None,
    extraction_error_ids: frozenset[str] | None = None,
    prefer: str | None = None,
    llm: Callable[[Any], Any] | None = None,
) -> ReconcileResult:
    """Row adapter + frozen ``reconcile``. Zero DB / zero LLM by default."""
    from services.knowevo.conflict_kernel import ClassifyContext

    facts = facts_from_relation_rows(rows, docs=docs, values=values)
    context = ClassifyContext(
        extraction_error_ids=extraction_error_ids or frozenset()
    )
    return reconcile(
        facts,
        authority_rank=authority_rank,
        version_clock=version_clock,
        llm=llm,
        prefer=prefer,
        context=context,
    )


# --- kg_service observer seam -----------------------------------------------


def make_relation_conflict_observer(
    *,
    docs: Mapping[str, Mapping[str, Any]] | None = None,
    version_clock: Any,
    authority_rank: Mapping[str, int] | None = None,
    on_records: Callable[[list[ConflictAdjudication], ReconcileResult], None]
    | None = None,
) -> Callable[[Mapping[str, Any]], None]:
    """Build a side-effect-free observer for ``KGService(conflict_observer=)``.

    The observer is invoked only on the existing-conflict branch of
    ``_merge_edge`` and never alters supersede/contested outcomes. It turns
    the ``(old_row, new_row)`` pair into two Facts, runs the kernel, and
    hands the projected adjudications to ``on_records`` (default: drop).
    Exceptions are swallowed by the caller seam; this function itself does
    not raise on empty payloads.
    """

    def _observe(event: Mapping[str, Any]) -> None:
        old = event.get("existing") or {}
        new = event.get("incoming") or {}
        if not old or not new:
            return
        rows = [dict(old), dict(new)]
        result = reconcile_relation_rows(
            rows,
            docs=docs,
            version_clock=version_clock,
            authority_rank=authority_rank,
        )
        records = records_to_adjudications(result.records)
        if on_records is not None:
            on_records(records, result)

    return _observe
