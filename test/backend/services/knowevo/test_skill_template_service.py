"""
Unit tests for services/knowevo/skill_template_service.py (T-20) and the
mining pipeline guard around it - deterministic pattern induction from
decision cards, the template candidate contract, upsert (INSERT/UPDATE
only), apply/instantiate rendering, reuse statistics, and the pipeline's
GuardedLLM honesty limits.

Layer 1 (always runs): everything, via the in-memory FakeStore seam and a
scripted FakeLLM matching the frozen llm contract - no Postgres, no
network, nothing fabricated. Persistence against real skill_template_t is
exercised by the mine_skill_templates real run, not here (zero-ALTER
INSERT/UPDATE only, no Layer-2 fixtures needed).
"""
import asyncio
import sys
import time
import uuid as uuid_mod
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

import services.knowevo.pipeline.mine_skill_templates as mine_mod
from services.knowevo.pipeline.mine_skill_templates import GuardedLLM
from services.knowevo.skill_template_service import (
    SOURCE_KEYS,
    TEMPLATE_VARIABLES,
    PgSkillTemplateStore,
    SkillTemplateService,
    _parse_induce_json,
    _relation_template_hint,
    _skeleton_body,
    _template_name,
    card_domain,
    card_signature,
    classify_task_type,
    group_cards,
    render_template,
)

TENANT = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    """Async callable matching the frozen llm contract.

    ``replies`` maps a prompt substring to the text to return; ``calls``
    records every invocation for assertions.
    """

    def __init__(self, replies=None, default="{}"):
        self.replies = replies or {}
        self.default = default
        self.calls = []

    async def __call__(self, prompt, *, kind, tier="mid", temperature=0.0):
        self.calls.append({"prompt": prompt, "kind": kind, "tier": tier,
                           "temperature": temperature})
        for needle, text in self.replies.items():
            if needle in prompt:
                return text
        return self.default


class FakeStore:
    """In-memory skill_template_t + decision_card_t stand-in.

    Honours the same five operations the service uses; update() only
    accepts real columns (mirrors PgSkillTemplateStore's column filter so
    a regression that writes an internal key fails here too).
    """

    COLUMNS = {"name", "task_type", "domain", "version", "body_md",
               "variables", "source", "reuse_count", "reuse_success",
               "avg_edit_distance"}

    def __init__(self, cards=None):
        self.cards = list(cards or [])
        self.rows: dict[str, dict] = {}
        self.inserts = 0
        self.updates = 0

    async def list_cards(self, tenant_id, limit=200):
        return [dict(c) for c in self.cards[:limit]]

    async def get_by_name(self, name, tenant_id):
        row = self.rows.get(name)
        return dict(row) if row else None

    async def insert(self, tenant_id, row):
        self.inserts += 1
        record = {"id": str(uuid_mod.uuid4()), **row}
        self.rows[row["name"]] = record
        return record["id"]

    async def update(self, name, tenant_id, values):
        self.updates += 1
        row = self.rows.get(name)
        if row is None:
            raise KeyError(f"template not found: {name}")
        for key, value in values.items():
            assert key in self.COLUMNS, f"non-column update key: {key}"
            row[key] = value
        return row["id"]

    async def list_all(self, tenant_id, limit=50):
        return [dict(r) for r in list(self.rows.values())[:limit]]


# ---------------------------------------------------------------------------
# Card fixtures
# ---------------------------------------------------------------------------

def make_card(question="二甲双胍属于哪一类降糖药？", route="R",
              decision="RECOMMEND", domain="healthcare", relations=None):
    return {
        "id": str(uuid_mod.uuid4()),
        "payload": {
            "question": question,
            "route": route,
            "decision": decision,
            "candidates": [{"name": "metformin"}] if decision == "RECOMMEND" else [],
            "evidence_chain": ([{"kg_path": relations}] if relations else []),
        },
        "knowledge_stamp": {"domain": domain, "kg_cutoff": "2026-01-01"},
    }


# ---------------------------------------------------------------------------
# Deterministic pattern extraction
# ---------------------------------------------------------------------------

class TestPatternExtraction:
    def test_refusal_wins_over_route(self):
        card = make_card(route="R", decision="INSUFFICIENT_EVIDENCE")
        assert classify_task_type(card) == "refusal"

    def test_version_compare_overrides_route(self):
        card = make_card(question="新版指南与2020版有什么变化？", route="R")
        assert classify_task_type(card) == "version_compare"

    def test_fact_lookup_and_reasoning(self):
        assert classify_task_type(make_card(route="R")) == "fact_lookup"
        assert classify_task_type(make_card(route="M")) == "reasoning_decision"
        assert classify_task_type(make_card(route="RM")) == "reasoning_decision"

    def test_unknown_route_is_general_qa(self):
        assert classify_task_type(make_card(route="")) == "general_qa"

    def test_domain_priority(self):
        assert card_domain(make_card(domain="healthcare")) == "healthcare"
        card = make_card()
        card["knowledge_stamp"] = {}
        card["payload"].pop("evidence_chain")
        assert card_domain(card) == "general"

    def test_signature_groups(self):
        card_a = make_card(route="R")
        card_b = make_card(route="R")
        card_c = make_card(route="M")
        groups = group_cards([card_a, card_b, card_c])
        assert len(groups[("healthcare", "fact_lookup")]) == 2
        assert len(groups[("healthcare", "reasoning_decision")]) == 1
        assert card_signature(card_a) == ("healthcare", "fact_lookup")

    def test_relation_template_hint_counts(self):
        rels = [{"rel_type": "禁忌"}, {"rel_type": "适应症"},
                {"rel_type": "禁忌"}]
        card = make_card(route="M", relations=rels)
        assert _relation_template_hint([card]) == "禁忌 -> 适应症"

    def test_relation_template_hint_empty(self):
        assert _relation_template_hint([make_card()]) == ""


# ---------------------------------------------------------------------------
# Induction: candidate contract + honest dropping
# ---------------------------------------------------------------------------

class TestInduction:
    def test_candidate_contract_deterministic(self):
        cards = [make_card(route="R") for _ in range(3)]
        service = SkillTemplateService(tenant_id=TENANT)
        candidates = asyncio.run(
            service.induce_from_cards(cards, min_support=2))
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["name"] == _template_name("healthcare", "fact_lookup")
        assert cand["name"] == "fact_lookup-healthcare"
        for key in TEMPLATE_VARIABLES:
            assert key in cand["variables"]
        for key in SOURCE_KEYS:
            assert key in cand["source"]
        assert cand["source"]["pattern"] == "healthcare/fact_lookup"
        assert len(cand["source"]["mined_from"]) == 3
        assert cand["source"]["induced_by"] == "deterministic"
        assert cand["support"] == 3
        # Placeholder contract: the skeleton keeps the injection points.
        for placeholder in ("{domain}", "{task_type}",
                            "{relation_template}", "{domain_rules}"):
            assert placeholder in cand["body_md"]

    def test_below_support_is_dropped_not_padded(self):
        cards = [make_card(route="R"), make_card(route="M")]
        service = SkillTemplateService(tenant_id=TENANT)
        candidates = asyncio.run(
            service.induce_from_cards(cards, min_support=2))
        assert candidates == []
        assert service.last_dropped == [
            {"domain": "healthcare", "task_type": "fact_lookup",
             "support": 1},
            {"domain": "healthcare", "task_type": "reasoning_decision",
             "support": 1},
        ]

    def test_min_support_validation(self):
        service = SkillTemplateService(tenant_id=TENANT)
        with pytest.raises(ValueError):
            asyncio.run(service.induce_from_cards([], min_support=0))

    def test_llm_channel_used_when_json_ok(self):
        llm_body = ("--- name: {template_name} description: mined --- "
                    "body flow: {relation_template}")
        llm = FakeLLM(replies={"fact_lookup": (
            '{"body_md": "%s", "relation_template": "禁忌->药品", '
            '"domain_rules": "高钾慎用"}' % llm_body)})
        cards = [make_card(route="R") for _ in range(2)]
        service = SkillTemplateService(tenant_id=TENANT, llm=llm)
        candidates = asyncio.run(service.induce_from_cards(cards, min_support=2))
        assert len(llm.calls) == 1
        assert llm.calls[0]["kind"] == "skill_template_induce"
        assert candidates[0]["source"]["induced_by"] == "llm"
        assert "{relation_template}" in candidates[0]["body_md"]
        assert candidates[0]["variables"]["relation_template"] == "禁忌->药品"
        assert candidates[0]["variables"]["domain_rules"] == "高钾慎用"
        # Identity variables stay as observed, not LLM-invented.
        assert candidates[0]["variables"]["domain"] == "healthcare"

    def test_llm_garbage_falls_back_to_skeleton(self):
        llm = FakeLLM(default="not json at all")
        cards = [make_card(route="R") for _ in range(2)]
        service = SkillTemplateService(tenant_id=TENANT, llm=llm)
        candidates = asyncio.run(service.induce_from_cards(cards, min_support=2))
        assert candidates[0]["source"]["induced_by"] == "deterministic"
        assert "{domain}" in candidates[0]["body_md"]

    def test_llm_exception_falls_back_to_skeleton(self):
        class BoomLLM:
            async def __call__(self, prompt, *, kind, **kwargs):
                raise RuntimeError("provider down")

        cards = [make_card(route="R") for _ in range(2)]
        service = SkillTemplateService(tenant_id=TENANT, llm=BoomLLM())
        candidates = asyncio.run(service.induce_from_cards(cards, min_support=2))
        assert candidates[0]["source"]["induced_by"] == "deterministic"

    def test_induce_reads_store(self):
        cards = [make_card(route="R") for _ in range(2)]
        store = FakeStore(cards=cards)
        service = SkillTemplateService(tenant_id=TENANT, store=store)
        candidates = asyncio.run(service.induce(min_support=2))
        assert len(candidates) == 1
        assert candidates[0]["source"]["mined_from"] == [
            c["id"] for c in cards]

    def test_skeleton_body_keeps_placeholders_and_support(self):
        variables = {"domain": "d", "task_type": "t",
                     "relation_template": "", "domain_rules": ""}
        body = _skeleton_body("d", "t", variables, support=4)
        assert "support=4" in body
        for placeholder in ("{domain}", "{task_type}",
                            "{relation_template}", "{domain_rules}",
                            "{template_name}"):
            assert placeholder in body


# ---------------------------------------------------------------------------
# Persistence: upsert (INSERT/UPDATE only) via the store seam
# ---------------------------------------------------------------------------

class TestPersistence:
    def _candidate(self, body="template body v1"):
        return {
            "name": "fact_lookup-healthcare",
            "task_type": "fact_lookup",
            "domain": "healthcare",
            "version": "1.0.0",
            "body_md": body,
            "variables": {"domain": "healthcare", "task_type": "fact_lookup",
                          "relation_template": "r1", "domain_rules": ""},
            "source": {"pattern": "healthcare/fact_lookup",
                       "mined_from": ["card-1", "card-2"],
                       "induced_at": "2026-09-19T00:00:00+00:00"},
            "support": 2,
        }

    def test_save_insert_then_update_is_upsert(self):
        store = FakeStore()
        service = SkillTemplateService(tenant_id=TENANT, store=store)
        row_id = asyncio.run(service.save_candidate(self._candidate()))
        assert store.inserts == 1 and store.updates == 0
        # Same deterministic name on re-mining -> UPDATE, still one row.
        updated = self._candidate(body="template body v2")
        updated["source"] = dict(updated["source"],
                                 induced_at="2026-09-19T01:00:00+00:00")
        row_id2 = asyncio.run(service.save_candidate(updated))
        assert store.inserts == 1 and store.updates == 1
        assert row_id2 == row_id
        rows = asyncio.run(service.list_templates())
        assert len(rows) == 1
        assert rows[0]["body_md"] == "template body v2"

    def test_save_validates_contract(self):
        service = SkillTemplateService(tenant_id=TENANT, store=FakeStore())
        bad = self._candidate()
        bad.pop("body_md")
        with pytest.raises(ValueError, match="body_md"):
            asyncio.run(service.save_candidate(bad))
        bad2 = self._candidate()
        bad2["variables"].pop("domain_rules")
        with pytest.raises(ValueError, match="domain_rules"):
            asyncio.run(service.save_candidate(bad2))
        bad3 = self._candidate()
        bad3["source"].pop("mined_from")
        with pytest.raises(ValueError, match="mined_from"):
            asyncio.run(service.save_candidate(bad3))

    def test_get_template_roundtrip(self):
        store = FakeStore()
        service = SkillTemplateService(tenant_id=TENANT, store=store)
        asyncio.run(service.save_candidate(self._candidate()))
        row = asyncio.run(service.get_template("fact_lookup-healthcare"))
        assert row is not None
        assert row["variables"]["domain"] == "healthcare"
        missing = asyncio.run(service.get_template("nope"))
        assert missing is None

    def test_tenant_id_required(self):
        with pytest.raises(ValueError, match="tenant_id"):
            SkillTemplateService(tenant_id="")


# ---------------------------------------------------------------------------
# Apply (instantiate) + reuse statistics
# ---------------------------------------------------------------------------

class TestApplyAndReuse:
    def _seed(self):
        store = FakeStore()
        service = SkillTemplateService(tenant_id=TENANT, store=store)
        body = ("---\nname: {template_name}\n---\n"
                "domain={domain} task={task_type} rel={relation_template} "
                "rules={domain_rules} unknown={not_a_var} keep={kept}")
        asyncio.run(service.save_candidate({
            "name": "fact_lookup-healthcare",
            "task_type": "fact_lookup",
            "domain": "healthcare",
            "version": "1.0.0",
            "body_md": body,
            "variables": {"domain": "healthcare", "task_type": "fact_lookup",
                          "relation_template": "禁忌->药品", "domain_rules": ""},
            "source": {"pattern": "healthcare/fact_lookup",
                       "mined_from": ["c1"], "induced_at": "t0"},
            "support": 1,
        }))
        return service, store, body

    def test_apply_renders_and_bumps_count(self):
        service, store, _ = self._seed()
        result = asyncio.run(service.apply_template(
            "fact_lookup-healthcare",
            variables={"domain_rules": "高钾慎用"}))
        text = result["skill_md"]
        assert "name: fact_lookup-healthcare" in text  # template_name slot
        assert "domain=healthcare" in text
        assert "task=fact_lookup" in text
        assert "rel=禁忌->药品" in text
        assert "rules=高钾慎用" in text
        assert "{not_a_var}" in text   # unknown placeholders survive
        assert "{kept}" in text
        assert result["reuse_count"] == 1
        row = asyncio.run(service.get_template("fact_lookup-healthcare"))
        assert row["reuse_count"] == 1

    def test_apply_overrides_template_defaults(self):
        service, _, _ = self._seed()
        result = asyncio.run(service.apply_template(
            "fact_lookup-healthcare",
            variables={"domain": "ckd", "relation_template": "A->B"}))
        assert "domain=ckd" in result["skill_md"]
        assert "rel=A->B" in result["skill_md"]
        # merged view returned for the caller to inspect
        assert result["variables"]["domain"] == "ckd"
        assert result["variables"]["task_type"] == "fact_lookup"

    def test_apply_unknown_template(self):
        service, _, _ = self._seed()
        with pytest.raises(KeyError):
            asyncio.run(service.apply_template("missing-template"))

    def test_outcome_before_apply_rejected(self):
        service, _, _ = self._seed()
        with pytest.raises(ValueError, match="one outcome per apply"):
            asyncio.run(service.record_reuse_outcome(
                "fact_lookup-healthcare", success=True))

    def test_running_success_rate(self):
        service, _, _ = self._seed()
        asyncio.run(service.apply_template("fact_lookup-healthcare"))
        stats = asyncio.run(service.record_reuse_outcome(
            "fact_lookup-healthcare", success=True))
        assert stats["reuse_success"] == 1.0
        asyncio.run(service.apply_template("fact_lookup-healthcare"))
        stats = asyncio.run(service.record_reuse_outcome(
            "fact_lookup-healthcare", success=False))
        assert stats["reuse_success"] == pytest.approx(0.5)
        asyncio.run(service.apply_template("fact_lookup-healthcare"))
        stats = asyncio.run(service.record_reuse_outcome(
            "fact_lookup-healthcare", success=True))
        assert stats["reuse_success"] == pytest.approx(2 / 3)

    def test_avg_edit_distance_running_mean(self):
        service, _, _ = self._seed()
        asyncio.run(service.apply_template("fact_lookup-healthcare"))
        asyncio.run(service.record_reuse_outcome(
            "fact_lookup-healthcare", success=True, edit_distance=10.0))
        asyncio.run(service.apply_template("fact_lookup-healthcare"))
        asyncio.run(service.record_reuse_outcome(
            "fact_lookup-healthcare", success=True, edit_distance=20.0))
        row = asyncio.run(service.get_template("fact_lookup-healthcare"))
        assert row["avg_edit_distance"] == pytest.approx(15.0)
        # A None distance leaves the mean unchanged (never invented).
        asyncio.run(service.apply_template("fact_lookup-healthcare"))
        asyncio.run(service.record_reuse_outcome(
            "fact_lookup-healthcare", success=True, edit_distance=None))
        row = asyncio.run(service.get_template("fact_lookup-healthcare"))
        assert row["avg_edit_distance"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Rendering + parsing helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_render_substitutes_known_keys_only(self):
        body = "d={domain} t={task_type} u={unknown} n={template_name}"
        out = render_template(body, {"domain": "healthcare",
                                     "task_type": "fact_lookup"},
                              template_name="fact_lookup-healthcare")
        assert out == ("d=healthcare t=fact_lookup "
                       "u={unknown} n=fact_lookup-healthcare")

    def test_render_empty_variable_survives(self):
        assert render_template("r={relation_template}!",
                               {"relation_template": ""}) == \
            "r={relation_template}!"

    def test_parse_induce_json_fenced_and_prose(self):
        fenced = "```json\n{\"body_md\": \"b\", \"relation_template\": \"r\"}\n```"
        assert _parse_induce_json(fenced)["body_md"] == "b"
        prose = "Here you go: {\"body_md\": \"b2\", \"domain_rules\": \"x\"}"
        parsed = _parse_induce_json(prose)
        assert parsed["body_md"] == "b2" and parsed["domain_rules"] == "x"
        assert _parse_induce_json("total garbage") == {}
        assert _parse_induce_json("[1,2,3]") == {}

    def test_pg_store_column_filter_rejects_internal_keys(self):
        # PgSkillTemplateStore must never try to write an internal key into
        # a real column set; simulate by checking the filter contract via
        # the class attribute (the SQL path itself is the real-run's job).
        store = PgSkillTemplateStore()
        assert "_distance_n" not in store._ROW_FIELDS
        assert "reuse_count" in store._ROW_FIELDS

    def test_pg_store_exposes_full_seam(self):
        # The real store must implement every operation the service calls;
        # a missing method otherwise only explodes at real-run time (as the
        # list_all gap did during the first T-20 mining run).
        for method in ("list_cards", "list_cards_all_tenants", "get_by_name",
                       "list_all", "insert", "update"):
            assert callable(getattr(PgSkillTemplateStore, method, None)), \
                f"PgSkillTemplateStore missing {method}"


# ---------------------------------------------------------------------------
# Pipeline GuardedLLM: the honesty guard (pitfalls #38/#39 discipline)
# ---------------------------------------------------------------------------

class TestGuardedLLM:
    def test_retry_then_success(self):
        attempts = {"n": 0}

        class Flaky:
            async def call_with_usage(self, prompt, *, kind, tier="mid",
                                      temperature=0.0):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise TimeoutError("slow provider")
                return "ok", {"input_tokens": 11, "output_tokens": 7}

        llm = GuardedLLM(Flaky())
        text = asyncio.run(llm("p", kind="skill_template_induce"))
        assert text == "ok"
        assert llm.calls == 1 and llm.failures == 1
        assert llm.input_tokens == 11 and llm.output_tokens == 7

    def test_consecutive_failures_take_channel_down(self):
        class AlwaysSlow:
            async def call_with_usage(self, prompt, *, kind, **kwargs):
                raise TimeoutError("upstream hang")

        llm = GuardedLLM(AlwaysSlow())
        for _ in range(mine_mod.MAX_ATTEMPTS):
            with pytest.raises(GuardedLLM.LLMChannelDown):
                asyncio.run(llm("p", kind="skill_template_induce"))
        assert llm.consecutive_fails >= mine_mod.MAX_CONSECUTIVE_FAILS
        assert "consecutive" in llm.down_reason or llm.down_reason
        # Channel stays down for subsequent calls without touching the
        # provider again (failures stop growing).
        failures = llm.failures
        with pytest.raises(GuardedLLM.LLMChannelDown):
            asyncio.run(llm("p", kind="skill_template_induce"))
        assert llm.failures == failures

    def test_run_budget_exhausted(self, monkeypatch):
        import services.knowevo.pipeline.mine_skill_templates as mod

        class Instant:
            async def call_with_usage(self, prompt, *, kind, **kwargs):
                return "ok", {"input_tokens": 1, "output_tokens": 1}

        llm = GuardedLLM(Instant())
        monkeypatch.setattr(mod, "TOTAL_BUDGET_S", -1.0)
        with pytest.raises(GuardedLLM.LLMChannelDown, match="budget"):
            asyncio.run(llm("p", kind="skill_template_induce"))

    def test_timeout_enforced_per_call(self, monkeypatch):
        import services.knowevo.pipeline.mine_skill_templates as mod

        async def slow_inner(*a, **k):
            await asyncio.sleep(5)
            return "ok", {}

        class Inner:
            call_with_usage = staticmethod(slow_inner)

        llm = GuardedLLM(Inner())
        monkeypatch.setattr(mod, "CALL_TIMEOUT_S", 0.05)
        started = time.monotonic()
        with pytest.raises(GuardedLLM.LLMChannelDown):
            asyncio.run(llm("p", kind="skill_template_induce"))
        assert time.monotonic() - started < 2.0
