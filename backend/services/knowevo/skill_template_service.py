"""KnowEvo skill-template service (T-20): mine parameterized SKILL.md
templates from real decision-card history, store them in skill_template_t
(zero-ALTER: INSERT/UPDATE DML only), instantiate (apply) them back into
concrete SKILL.md text, and keep the reuse loop honest with counters.

Layering per 02-tech-plan 3.4: the entry/sub-task SKILL.md files under
competition/skills/ are the reusable workflow surface; a mined template is
the *parameterized* memory of "this (domain, task_type) question pattern
has a workflow that already worked N times". ``variables`` carries the
injection points ({domain, task_type, relation_template, domain_rules});
``source`` carries the mining provenance ({pattern, mined_from, induced_at})
so every template can be traced back to the decision cards that induced it.
Fabricating either is the cardinal sin here: a candidate whose support is
below ``min_support`` is dropped and reported, never padded.

The LLM is injected as the async callable contract frozen by kg_service /
decision_service (``await llm(prompt, *, kind, tier, temperature)``) and is
strictly optional: without one, induction still produces deterministic
skeleton templates (induced_by="deterministic") from the observed pattern -
honest but less eloquent. Persistence goes through a store seam
(``store`` = PgSkillTemplateStore in production, an in-memory fake in
tests) following the kg_service.py pattern; decision_card_t is read-only
for this service.

Interface contract: competition/tasks/T-20-brief.md; mechanism notes:
competition/docs/skill-mechanism.md (frontmatter contract - ``version`` is
NOT a valid SKILL.md frontmatter key, it lives in the ``version`` column).
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from services.knowevo.schemas import (
    DECISION_INSUFFICIENT,
    ROUTE_BOTH,
    ROUTE_REASONING,
    ROUTE_RETRIEVAL,
)

logger = logging.getLogger(__name__)

# The four injection points every template must expose (brief acceptance:
# variables 含 {domain, task_type, relation_template, domain_rules}).
TEMPLATE_VARIABLES = ("domain", "task_type", "relation_template",
                      "domain_rules")

# source JSONB keys (brief: source 含 {pattern, mined_from, induced_at}).
SOURCE_KEYS = ("pattern", "mined_from", "induced_at")

# Version-compare phrasing that flips a task_type to version_compare
# (same lexical family as decision_service.VERSION_COMPARE_WORDS).
VERSION_COMPARE_RE = re.compile(
    r"(最新版|新版|旧版|版次|\d{4}\s*版|版本对比|有什么变化|"
    r"updated guideline|difference between|new version)", re.IGNORECASE)

# Placeholder rendering: only known variable keys are substituted; any
# other {braced} token in the markdown stays untouched (markdown that
# legitimately contains braces must survive an apply round-trip).
_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

# skill_template_t.name is String(64) and unique - the deterministic name
# keeps re-mining idempotent (same pattern -> same name -> UPDATE).
_NAME_MAX_LEN = 64
_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Deterministic pattern extraction (no LLM anywhere in this section)
# ---------------------------------------------------------------------------

def classify_task_type(card: dict[str, Any]) -> str:
    """Task type of one decision-card row, from its payload only.

    Priority: refusal > version_compare > fact_lookup > reasoning_decision.
    Refusal cards are their own pattern (the "no evidence" workflow is worth
    reusing as a template too); version wording overrides the route because
    a version question on the retrieval route is still a version task.
    """
    payload = dict(card.get("payload") or {})
    if payload.get("decision") == DECISION_INSUFFICIENT:
        return "refusal"
    question = str(payload.get("question") or "")
    if VERSION_COMPARE_RE.search(question):
        return "version_compare"
    route = payload.get("route")
    if route == ROUTE_RETRIEVAL:
        return "fact_lookup"
    if route in (ROUTE_REASONING, ROUTE_BOTH):
        return "reasoning_decision"
    return "general_qa"


def card_domain(card: dict[str, Any]) -> str:
    """Domain of one card row.

    Real decision_card_t rows carry the domain on the payload, inside the
    payload's knowledge_stamp, and on the row-level knowledge_stamp column
    - check all three, else general.
    """
    payload = dict(card.get("payload") or {})
    stamp = dict(payload.get("knowledge_stamp") or {})
    row_stamp = dict(card.get("knowledge_stamp") or {})
    for source in (payload.get("domain"), stamp.get("domain"),
                   row_stamp.get("domain"), card.get("domain")):
        if source:
            return str(source)[:32]
    return "general"


def card_signature(card: dict[str, Any]) -> tuple[str, str]:
    """Grouping key: (domain, task_type)."""
    return card_domain(card), classify_task_type(card)


def group_cards(cards: Iterable[dict[str, Any]],
                ) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group decision-card rows by (domain, task_type)."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for card in cards:
        groups.setdefault(card_signature(card), []).append(card)
    return groups


def _template_name(domain: str, task_type: str) -> str:
    return f"{task_type}-{domain}"[:_NAME_MAX_LEN]


def _relation_template_hint(group: list[dict[str, Any]]) -> str:
    """Most frequent kg relation types across the group's paths (v0
    deterministic signal; the LLM channel refines it when available)."""
    counts: dict[str, int] = {}
    for card in group:
        payload = dict(card.get("payload") or {})
        chains = payload.get("evidence_chain")
        if not isinstance(chains, list):
            continue
        for chain in chains:
            if not isinstance(chain, dict):
                continue
            for path in chain.get("kg_path") or []:
                if isinstance(path, dict):
                    rel = str(path.get("rel_type") or "").strip()
                    if rel:
                        counts[rel] = counts.get(rel, 0) + 1
    if not counts:
        return ""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return " -> ".join(rel for rel, _ in ordered[:5])


def _skeleton_body(domain: str, task_type: str, variables: dict[str, Any],
                   support: int) -> str:
    """Deterministic SKILL.md body for a mined pattern.

    Keeps all four variable placeholders so apply-time injection works
    uniformly with the LLM channel; only what mining actually observed -
    the pattern and its support - is baked in as provenance. No invented
    steps.
    """
    return (
        "---\n"
        "name: {template_name}\n"
        "description: >-\n"
        f"  Mined workflow template for {task_type} questions in the "
        f"{domain} domain (support={support} decision cards).\n"
        "---\n"
        "\n"
        f"# {{task_type}} workflow ({{domain}}, mined template)\n"
        "\n"
        "## 职责\n"
        "按已验证有效的模式处理 {domain} 域的 {task_type} 类任务；"
        f"本模板由 {support} 张真实决策卡归纳"
        "（逐卡来源见 skill_template_t.source.mined_from）。\n"
        "\n"
        "## 允许的原子工具\n"
        "- knowledge_base_search / kg_search / kg_multi_hop / "
        "decision_card_render（按任务类型取用，见流程）\n"
        "\n"
        "## 流程\n"
        "1. 任务类型：{task_type}；领域：{domain}。\n"
        "2. 关系模板（领域注入）：{relation_template}\n"
        "3. 领域规则：{domain_rules}\n"
        "4. 证据收集后交证据组装产出决策卡；证据不足时拒答"
        "（INSUFFICIENT_EVIDENCE），不得编造。\n"
        "\n"
        "## 输出契约\n"
        "决策卡（含证据链与知识版本戳）+ 一句话模式说明。\n"
    )


# ---------------------------------------------------------------------------
# Store seam: real store (zero-ALTER, INSERT/UPDATE only) in this file so
# the brief's exclusive-file list stays exact; tests inject a fake.
# ---------------------------------------------------------------------------

class PgSkillTemplateStore:
    """skill_template_t persistence via the knowevo_db helpers.

    Read side: decision_card_t rows for induction (read-only). Write side:
    INSERT/UPDATE DML only - no DELETE, no DDL.
    """

    _CARD_FIELDS = ("id", "payload", "knowledge_stamp")
    _ROW_FIELDS = ("id", "name", "task_type", "domain", "version", "body_md",
                   "variables", "source", "reuse_count", "reuse_success",
                   "avg_edit_distance", "created_at")

    async def list_cards(self, tenant_id: str,
                         limit: int = 200) -> list[dict[str, Any]]:
        from database.knowevo_db import DecisionCard, _get_db_session
        with _get_db_session() as session:
            rows = (session.query(DecisionCard)
                    .filter(DecisionCard.tenant_id == tenant_id)
                    .order_by(DecisionCard.created_at.desc())
                    .limit(limit)
                    .all())
            cards = []
            for row in rows:
                cards.append({
                    "id": str(row.id),
                    "payload": dict(row.payload or {}),
                    "knowledge_stamp": dict(row.knowledge_stamp or {}),
                })
            return cards

    async def list_cards_all_tenants(self,
                                     limit: int = 200) -> list[dict[str, Any]]:
        """Cross-tenant read for the offline mining pipeline only.

        Deliberately NOT used by any request-scoped path: pattern induction
        pools real history when single-tenant support cannot reach the
        threshold (the T-20 real DB holds 2 cards per tenant). Provenance
        (mined_from ids) stays per-card traceable; the pipeline marks the
        candidate source with cross_tenant=True.
        """
        from database.knowevo_db import DecisionCard, _get_db_session
        with _get_db_session() as session:
            rows = (session.query(DecisionCard)
                    .order_by(DecisionCard.created_at.desc())
                    .limit(limit)
                    .all())
            cards = []
            for row in rows:
                cards.append({
                    "id": str(row.id),
                    "tenant_id": str(row.tenant_id),
                    "payload": dict(row.payload or {}),
                    "knowledge_stamp": dict(row.knowledge_stamp or {}),
                })
            return cards

    async def get_by_name(self, name: str,
                          tenant_id: str) -> dict[str, Any] | None:
        from database.knowevo_db import SkillTemplate, _get_db_session
        with _get_db_session() as session:
            row = (session.query(SkillTemplate)
                   .filter(SkillTemplate.name == name,
                           SkillTemplate.tenant_id == tenant_id)
                   .first())
            return self._row_to_dict(row) if row is not None else None

    async def list_all(self, tenant_id: str,
                       limit: int = 50) -> list[dict[str, Any]]:
        from database.knowevo_db import SkillTemplate, _get_db_session
        with _get_db_session() as session:
            rows = (session.query(SkillTemplate)
                    .filter(SkillTemplate.tenant_id == tenant_id)
                    .order_by(SkillTemplate.created_at.desc())
                    .limit(limit)
                    .all())
            return [self._row_to_dict(row) for row in rows]

    async def insert(self, tenant_id: str,
                     row: dict[str, Any]) -> str:
        from database.knowevo_db import SkillTemplate, create_row
        result = create_row(SkillTemplate, tenant_id=tenant_id, **row)
        return str(result["id"])

    async def update(self, name: str, tenant_id: str,
                     values: dict[str, Any]) -> str:
        from database.knowevo_db import SkillTemplate, _get_db_session
        columns = {k: v for k, v in values.items()
                   if not k.startswith("_") and k in self._ROW_FIELDS}
        with _get_db_session() as session:
            row = (session.query(SkillTemplate)
                   .filter(SkillTemplate.name == name,
                           SkillTemplate.tenant_id == tenant_id)
                   .first())
            if row is None:
                raise KeyError(f"template not found: {name}")
            for key, value in columns.items():
                setattr(row, key, value)
            session.flush()
            return str(row.id)

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in self._ROW_FIELDS:
            value = getattr(row, field, None)
            if field == "id":
                value = str(value)
            elif field == "created_at" and value is not None:
                value = value.isoformat()
            out[field] = value
        return out


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SkillTemplateService:
    """Induce / store / apply / track reuse of parameterized SKILL.md
    templates in skill_template_t (T-20).

    ``store`` is the persistence seam (PgSkillTemplateStore in production,
    an in-memory fake in tests). ``llm`` is the injected async callable or
    None for the deterministic channel only.
    """

    def __init__(self, tenant_id: str, store: Any = None, llm: Any = None,
                 tier: str = "mid", lang: str = "zh"):
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self.tenant_id = tenant_id
        self.store = store
        self.llm = llm
        self.tier = tier
        self.lang = lang
        # Support report of the last induce run (honesty output).
        self.last_dropped: list[dict[str, Any]] = []

    # ── store plumbing (lazy real store; kg_service seam style) ────────

    def _get_store(self):
        if self.store is None:
            self.store = PgSkillTemplateStore()
        return self.store

    # ── induction (decision_card_t is read-only for this service) ──────

    async def induce_from_cards(
        self,
        cards: list[dict[str, Any]],
        *,
        min_support: int = 2,
    ) -> list[dict[str, Any]]:
        """Mine template candidates from decision-card rows.

        A (domain, task_type) group becomes a candidate at support >=
        min_support; below it the group is dropped and reported via
        ``last_dropped`` - never padded. Candidate shape:

            {name, task_type, domain, version, body_md,
             variables: {domain, task_type, relation_template, domain_rules},
             source: {pattern, mined_from, induced_at, ...}, support}
        """
        if min_support < 1:
            raise ValueError("min_support must be >= 1")
        groups = group_cards(cards)
        candidates: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        for (domain, task_type), group in sorted(
                groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            support = len(group)
            if support < min_support:
                dropped.append({"domain": domain, "task_type": task_type,
                                "support": support})
                continue
            candidates.append(
                await self._build_candidate(domain, task_type, group, support))
        self.last_dropped = dropped
        return candidates

    async def induce(self, *, limit: int = 200,
                     min_support: int = 2) -> list[dict[str, Any]]:
        """Read decision_card_t (read-only) through the store, then mine."""
        cards = await self._get_store().list_cards(self.tenant_id, limit=limit)
        return await self.induce_from_cards(cards, min_support=min_support)

    async def _build_candidate(self, domain: str, task_type: str,
                               group: list[dict[str, Any]],
                               support: int) -> dict[str, Any]:
        mined_from = [str(card.get("id")) for card in group if card.get("id")]
        variables = {
            "domain": domain,
            "task_type": task_type,
            "relation_template": _relation_template_hint(group),
            "domain_rules": "",
        }
        body_md = ""
        induced_by = "deterministic"
        if self.llm is not None:
            try:
                llm_result = await self._llm_induce(
                    domain, task_type, group, variables)
                body_md = str(llm_result.get("body_md") or "").strip()
                if body_md:
                    induced_by = "llm"
                # The LLM refines the two soft variables; the identity
                # variables (domain/task_type) stay as observed.
                for key in ("relation_template", "domain_rules"):
                    value = str(llm_result.get(key) or "").strip()
                    if value:
                        variables[key] = value
            except Exception as exc:  # noqa: BLE001 - fall back honestly
                logger.warning(
                    "LLM induction failed for %s/%s, using deterministic "
                    "skeleton: %s", domain, task_type, exc)
        if not body_md:
            body_md = _skeleton_body(domain, task_type, variables, support)
        return {
            "name": _template_name(domain, task_type),
            "task_type": task_type,
            "domain": domain,
            "version": _VERSION,
            "body_md": body_md,
            "variables": variables,
            "source": {
                "pattern": f"{domain}/{task_type}",
                "mined_from": mined_from,
                "induced_at": datetime.now(UTC).isoformat(),
                "induced_by": induced_by,
                "support": support,
            },
            "support": support,
        }

    async def _llm_induce(self, domain: str, task_type: str,
                          group: list[dict[str, Any]],
                          variables: dict[str, Any]) -> dict[str, Any]:
        """One LLM call: parameterize the template body from real cards.

        The prompt sees card payloads (question/route/decision), never
        fabricated content; the model only rephrases and abstracts.
        """
        from services.knowevo.kg_service import _render_prompt

        samples = []
        for card in group[:8]:
            payload = dict(card.get("payload") or {})
            samples.append({
                "question": payload.get("question", ""),
                "route": payload.get("route", ""),
                "decision": payload.get("decision", ""),
                "candidates": len(payload.get("candidates") or []),
            })
        system, user = _render_prompt(
            "knowevo_skill_induce", self.lang,
            domain=domain, task_type=task_type, support=len(group),
            samples=json.dumps(samples, ensure_ascii=False, indent=2),
            relation_template=variables.get("relation_template") or "(none)",
            variables_json=json.dumps(variables, ensure_ascii=False),
        )
        raw = await self.llm(system + "\n\n" + user,
                             kind="skill_template_induce",
                             tier=self.tier, temperature=0.0)
        return _parse_induce_json(raw)

    # ── persistence (INSERT/UPDATE only; zero ALTER) ───────────────────

    async def save_candidate(self, candidate: dict[str, Any]) -> str:
        """Upsert one candidate by deterministic name.

        Existing name -> UPDATE (re-mining stays idempotent); new name ->
        INSERT. No DELETE, no DDL.
        """
        for key in ("name", "task_type", "domain", "version", "body_md",
                    "variables", "source"):
            if candidate.get(key) in (None, ""):
                raise ValueError(f"candidate missing required field: {key}")
        missing = [v for v in TEMPLATE_VARIABLES
                   if v not in (candidate["variables"] or {})]
        if missing:
            raise ValueError(f"variables missing keys: {missing}")
        missing = [s for s in SOURCE_KEYS
                   if s not in (candidate["source"] or {})]
        if missing:
            raise ValueError(f"source missing keys: {missing}")

        store = self._get_store()
        existing = await store.get_by_name(candidate["name"], self.tenant_id)
        row = {
            "name": candidate["name"],
            "task_type": candidate["task_type"],
            "domain": candidate["domain"],
            "version": candidate["version"],
            "body_md": candidate["body_md"],
            "variables": candidate["variables"],
            "source": candidate["source"],
        }
        if existing:
            return await store.update(candidate["name"], self.tenant_id, row)
        return await store.insert(self.tenant_id, row)

    async def list_templates(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._get_store().list_all(self.tenant_id, limit=limit)

    async def get_template(self, name: str) -> dict[str, Any] | None:
        return await self._get_store().get_by_name(name, self.tenant_id)

    # ── apply (instantiate) ────────────────────────────────────────────

    async def apply_template(
        self, name: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Instantiate a template into concrete SKILL.md text.

        Variables passed here override the template defaults; unknown
        {placeholders} survive untouched. Each apply bumps reuse_count.
        """
        row = await self.get_template(name)
        if row is None:
            raise KeyError(f"template not found: {name}")
        defaults = dict(row.get("variables") or {})
        overrides = {k: str(v) for k, v in (variables or {}).items() if v}
        merged = {**defaults, **overrides}
        rendered = render_template(row["body_md"], merged,
                                   template_name=row["name"])
        new_count = int(row.get("reuse_count") or 0) + 1
        await self._get_store().update(name, self.tenant_id,
                                       {"reuse_count": new_count})
        return {
            "name": name,
            "skill_md": rendered,
            "variables": merged,
            "reuse_count": new_count,
        }

    # ── reuse statistics ───────────────────────────────────────────────

    async def record_reuse_outcome(self, name: str, success: bool,
                                   edit_distance: float | None = None,
                                   ) -> dict[str, Any]:
        """Record one apply outcome (contract: exactly one per apply).

        ``reuse_success`` is the running success rate with reuse_count as
        denominator. ``avg_edit_distance`` is the running mean assuming a
        distance once per apply; a None distance leaves the mean unchanged
        (never invented). skill_template_t has no outcome-count column, so
        the denominator is reuse_count - documented, not hidden.
        """
        row = await self.get_template(name)
        if row is None:
            raise KeyError(f"template not found: {name}")
        count = int(row.get("reuse_count") or 0)
        if count < 1:
            raise ValueError(
                "record_reuse_outcome before any apply - one outcome per "
                "apply is the contract")
        n_prev = max(1, count - 1)
        prev_rate = row.get("reuse_success")
        base = float(prev_rate) if prev_rate is not None else 0.0
        rate = (base * n_prev + (1.0 if success else 0.0)) / count

        updates: dict[str, Any] = {"reuse_success": rate}
        if edit_distance is not None:
            prev_avg = row.get("avg_edit_distance")
            base_avg = float(prev_avg) if prev_avg is not None else 0.0
            updates["avg_edit_distance"] = (
                (base_avg * n_prev + float(edit_distance)) / count)

        await self._get_store().update(name, self.tenant_id, updates)
        return {"name": name, "reuse_count": count,
                "reuse_success": updates["reuse_success"],
                "avg_edit_distance": updates.get("avg_edit_distance")}


# ---------------------------------------------------------------------------
# Rendering + parsing helpers (module-level for direct test access)
# ---------------------------------------------------------------------------

def render_template(body_md: str, variables: dict[str, Any],
                    template_name: str = "") -> str:
    """Substitute {key} placeholders for known variable keys only."""

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables and variables[key] not in (None, ""):
            return str(variables[key])
        if key == "template_name" and template_name:
            return template_name
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body_md)


def _parse_induce_json(raw: str) -> dict[str, Any]:
    """Parse the LLM induction reply: a JSON object with body_md plus the
    two soft variables. Tolerates a markdown fence; returns {} on garbage
    so the caller falls back to the deterministic skeleton."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Tolerate trailing prose around the JSON object.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}
