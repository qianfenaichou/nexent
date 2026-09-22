"""
Tests for the T-29 used_tokens observation gap: render_card must carry
the measured usage of its own decision-card LLM call on the card.

Three contract points:
- the LLM render path aggregates call_with_usage's measured counters into
  card.used_tokens (DB row and payload are the same field via to_payload);
- the zero-LLM deterministic refusal keeps used_tokens == 0 (honest
  accounting: no model call, no tokens);
- a plain frozen-contract callable (no call_with_usage seam) still renders,
  reporting 0 - measured-only, never an estimate - and no other card
  semantic field moves because of the observation.
"""
import asyncio
import sys
from pathlib import Path

# Upstream convention (test/backend/services/knowevo/test_ontology_service.py):
# backend root on sys.path; never add __init__.py under the test tree.
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), ):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from services.knowevo.decision_service import DecisionService
from services.knowevo.schemas import (
    CHANNEL_KG,
    DECISION_INSUFFICIENT,
    DECISION_RECOMMEND,
    EvidenceChain,
    EvidenceItem,
    Provenance,
)

TENANT = "11111111-1111-1111-1111-111111111111"

CARD_JSON = """{
  "candidates": [{
    "option": "首选 SGLT2i（恩格列净）",
    "score": 0.82, "confidence_calibrated": 0.9,
    "evidence_chain": [{
      "claim": "eGFR 45 时 SGLT2i 仍可起始",
      "provenance": {"doc": "指南2024版", "span": "§9.2 用药",
                     "kg_path": ["Drug:sglt2i -> CKD_G3a"],
                     "version_pinned": false},
      "tag": "EXTRACTED", "source_channel": "kg"}],
    "risks": ["eGFR 持续下降至<30 需停用"]}],
  "decision": "RECOMMEND",
  "uncertainty_notes": ["说明书剂量与指南表述存在差异"]
}"""

USAGE = {"input_tokens": 123, "output_tokens": 45,
         "reasoning_tokens": 10, "finish_reason": "stop"}


class UsageLLM:
    """Async callable exposing the additive call_with_usage seam.

    Mirrors llm_client.LlmRouter's dual surface: ``call_with_usage``
    returns (content, usage) while ``__call__`` stays str-only, so the
    service's preferred seam and its fallback are both exercisable.
    """

    def __init__(self, reply: str = CARD_JSON, usage: dict | None = USAGE):
        self.reply = reply
        self.usage = usage
        self.usage_calls: list[dict] = []

    async def call_with_usage(self, prompt, *, kind, tier="mid",
                              temperature=0.0):
        self.usage_calls.append({"kind": kind, "tier": tier})
        return self.reply, self.usage

    async def __call__(self, prompt, *, kind, tier="mid", temperature=0.0):
        raise AssertionError("render_card must prefer the call_with_usage "
                             "seam when the callable exposes it")


class FrozenLLM:
    """Async callable matching only the frozen str contract (no usage)."""

    def __init__(self, reply: str = CARD_JSON):
        self.reply = reply
        self.calls = 0

    async def __call__(self, prompt, *, kind, tier="mid", temperature=0.0):
        self.calls += 1
        return self.reply


def _chain_with_claim():
    chain = EvidenceChain()
    chain.items.append(EvidenceItem(
        claim="eGFR 45 时 SGLT2i 仍可起始",
        provenance=Provenance(doc="指南2024版", span="§9.2 用药",
                              kg_path=["Drug:sglt2i", "CKD_G3a"],
                              version_pinned=False),
        tag="EXTRACTED", source_channel=CHANNEL_KG))
    return chain


def _run(coro):
    return asyncio.run(coro)


class TestUsedTokensObservation:
    @pytest.mark.asyncio
    async def test_llm_path_assigns_measured_usage(self):
        """The card render's own call feeds card.used_tokens (T-29)."""
        llm = UsageLLM()
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert llm.usage_calls[0]["kind"] == "decision_card"
        assert card.used_tokens == USAGE["input_tokens"] + USAGE["output_tokens"]

    @pytest.mark.asyncio
    async def test_used_tokens_reaches_payload_and_contract(self):
        """DB row and payload share to_payload, so both sides agree and
        the wire contract still validates with the populated field."""
        svc = DecisionService(llm=UsageLLM(), tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        payload = card.to_payload()
        assert payload["used_tokens"] == card.used_tokens
        contract = svc.validate_card(card)
        assert contract.used_tokens == card.used_tokens

    @pytest.mark.asyncio
    async def test_deterministic_refusal_keeps_zero_tokens(self):
        """Zero-LLM refusal: no model call, so used_tokens stays exactly 0
        (honest accounting, never fabricated on the refusal surface)."""
        llm = UsageLLM()
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("库外问题", EvidenceChain())
        assert card.decision == DECISION_INSUFFICIENT
        assert card.used_tokens == 0
        assert llm.usage_calls == []
        assert svc.validate_card(card).used_tokens == 0

    @pytest.mark.asyncio
    async def test_frozen_contract_fallback_reports_zero(self):
        """No call_with_usage seam -> usage is unknowable; the card still
        renders and reports 0 (measured-only, never an estimate)."""
        llm = FrozenLLM()
        svc = DecisionService(llm=llm, tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert llm.calls == 1
        assert card.decision == DECISION_RECOMMEND
        assert card.used_tokens == 0

    @pytest.mark.asyncio
    async def test_zero_usage_dict_is_reported_as_zero(self):
        """A provider that omitted usage yields 0, not a guess."""
        svc = DecisionService(llm=UsageLLM(usage={}), tenant_id=TENANT)
        card = await svc.render_card("q", _chain_with_claim())
        assert card.used_tokens == 0

    @pytest.mark.asyncio
    async def test_observation_does_not_move_card_semantics(self):
        """Only the token counter is new: every semantic field of the card
        rendered through the usage seam equals the frozen-contract render
        of the same reply."""
        question = "eGFR 45 的患者如何起始 SGLT2i？"
        via_usage = await DecisionService(
            llm=UsageLLM(), tenant_id=TENANT).render_card(
            question, _chain_with_claim())
        via_frozen = await DecisionService(
            llm=FrozenLLM(), tenant_id=TENANT).render_card(
            question, _chain_with_claim())
        assert via_usage.decision == via_frozen.decision
        assert [c.option for c in via_usage.candidates] == \
            [c.option for c in via_frozen.candidates]
        assert [r for c in via_usage.candidates for r in c.risks] == \
            [r for c in via_frozen.candidates for r in c.risks]
        assert via_usage.uncertainty_notes == via_frozen.uncertainty_notes
        assert via_usage.route == via_frozen.route
        assert via_usage.disclaimer == via_frozen.disclaimer
        # kg_cutoff is a freshly resolved now()-clock per render, so only
        # its stable parts are comparable across two independent renders.
        assert via_usage.knowledge_stamp.ontology_version == \
            via_frozen.knowledge_stamp.ontology_version
        assert via_usage.knowledge_stamp.clock_source == \
            via_frozen.knowledge_stamp.clock_source
        assert via_usage.used_tokens == USAGE["input_tokens"] + \
            USAGE["output_tokens"]
        assert via_frozen.used_tokens == 0
