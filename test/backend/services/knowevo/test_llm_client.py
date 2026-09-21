"""Unit tests for services/knowevo/llm_client.py (T-08 wiring).

Layer 1 (always runs): tier resolution, tenant default fallback, the
unconfigured error path, and the injected-``llm`` callable contract.
No network and no real model: ``get_model_by_model_id`` and
``tenant_config_manager`` are monkey-patched to in-memory records.
"""
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_REPO_ROOT / "backend"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"

# Plain dict model records keyed by (tenant_id, model_id).
FAKE_MODELS = {
    (TENANT_A, 111): {
        "model_id": 111,
        "tenant_id": TENANT_A,
        "model_name": "qwen-turbo",
        "model_repo": "",
        "model_factory": "siliconflow",
        "model_type": "chat",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-t1",
    },
    (TENANT_A, 222): {
        "model_id": 222,
        "tenant_id": TENANT_A,
        "model_name": "qwen-plus",
        "model_repo": "",
        "model_factory": "siliconflow",
        "model_type": "chat",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-t1",
        # R20 review P2-2: the cap must be declared, otherwise both the
        # extract (None) and non-extract (configured) assertions below are
        # vacuous - config.get("max_output_tokens") would be None for both.
        "max_output_tokens": 8192,
    },
    (TENANT_B, 999): {
        "model_id": 999,
        "tenant_id": TENANT_B,
        "model_name": "default-llm",
        "model_repo": "",
        "model_factory": "siliconflow",
        "model_type": "chat",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-t2",
    },
}


@pytest.fixture(autouse=True)
def _patch_sources(monkeypatch):
    """Point the wiring at in-memory model records."""
    from services.knowevo import llm_client

    def fake_get_model_by_model_id(model_id, tenant_id=None):
        return FAKE_MODELS.get((tenant_id, int(model_id)))

    monkeypatch.setattr(
        llm_client, "get_model_by_model_id", fake_get_model_by_model_id)

    def fake_get_model_config(key, default=None, tenant_id=None):
        # Tenant B has a default LLM; tenant A resolves only via tier ids.
        if key == "LLM_ID" and tenant_id == TENANT_B:
            return FAKE_MODELS[(TENANT_B, 999)]
        return default or {}

    monkeypatch.setattr(
        llm_client.tenant_config_manager,
        "get_model_config",
        fake_get_model_config,
    )
    monkeypatch.setattr(llm_client, "OpenAIModel", FakeOpenAIModel)


class FakeOpenAIModel:
    """Drop-in for nexent OpenAIModel: records construction, returns content.

    `generate` mirrors the real signature: smolagents' OpenAIModel.generate
    takes **kwargs and merges them into the request body, so a stub that
    accepted only `messages` would hide the fact that a client-level
    extra_body never reaches the wire (r19 root cause).
    """

    last_generate_messages = None
    last_generate_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.content = f"ok:{kwargs.get('model_id')}"

    def generate(self, messages, **kwargs):
        FakeOpenAIModel.last_generate_messages = messages
        FakeOpenAIModel.last_generate_kwargs = kwargs
        return SimpleContent(self.content)


class SimpleContent:
    def __init__(self, text):
        self.content = text


def test_router_uses_tier_model_id_over_default(monkeypatch):
    """Small tier must resolve via KW_LLM_SMALL_MODEL_ID, not tenant default."""
    from services.knowevo import llm_client

    monkeypatch.setattr(
        llm_client, "KW_LLM_SMALL_MODEL_ID", "111")
    monkeypatch.setattr(
        llm_client, "KW_LLM_MID_MODEL_ID", "222")
    monkeypatch.setattr(
        llm_client, "KW_LLM_LARGE_MODEL_ID", "")

    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    model = router._get_model(llm_client.TIER_SMALL, temperature=0.0)
    assert model.kwargs["model_id"] == "qwen-turbo"
    assert model.kwargs["api_base"] == "https://api.example.com/v1"


def test_callable_contract_returns_str(monkeypatch):
    """The injected-llm signature (prompt, kind, tier, temperature) -> str."""
    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    out = await_llm(router, "hello", kind="extract", tier=llm_client.TIER_MID)
    assert isinstance(out, str)
    assert out.startswith("ok:")


def test_fallback_to_tenant_llm(monkeypatch):
    """Tenant with only a default LLM still resolves (run.py fallback)."""
    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_SMALL_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_LARGE_MODEL_ID", "")
    router = llm_client.LlmRouter(tenant_id=TENANT_B)
    model = router._get_model(llm_client.TIER_MID, temperature=0.1)
    assert "999" in model.kwargs["model_id"] or model.kwargs["model_id"]


def test_unconfigured_raises_clear_error(monkeypatch):
    """No tier id and no tenant default -> LLMConfigurationError."""
    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_SMALL_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_LARGE_MODEL_ID", "")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    with pytest.raises(llm_client.LLMConfigurationError):
        router._get_model(llm_client.TIER_MID, temperature=0.0)


def test_model_cached_per_tier_temperature(monkeypatch):
    """Same (tier, temperature) reuses the OpenAIModel instance."""
    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    first = router._get_model(llm_client.TIER_MID, temperature=0.0)
    second = router._get_model(llm_client.TIER_MID, temperature=0.0)
    assert first is second


def test_extract_kind_disables_thinking(monkeypatch):
    """r19: extraction must not spend the output cap on a reasoning chain."""
    import asyncio

    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    model = router._get_model(
        llm_client.TIER_MID, temperature=0.0, kind="extract")
    assert model.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    # No client-level cap for extraction. Fixture model 222 declares 8192
    # (r20 review P2-2), so this assertion is discriminating - and cleanup
    # only: the generate() path never forwarded this attribute, the wire cap
    # that truncated the JSON was the provider default (see llm_client note).
    assert model.kwargs["max_output_tokens"] is None
    # The assertion above is about the CLIENT and is not enough: smolagents'
    # generate() builds its body from `self.kwargs`, which stays empty for a
    # named constructor argument, so only the per-call kwarg reaches the
    # wire (r19 wire probe: completion_kwargs == ['messages', 'model']).
    FakeOpenAIModel.last_generate_kwargs = None
    asyncio.run(router.call_with_usage("prompt", kind="extract"))
    assert FakeOpenAIModel.last_generate_kwargs == {
        "extra_body": {"thinking": {"type": "disabled"}}}


def test_other_kinds_send_no_provider_extras(monkeypatch):
    """judge/align/route families keep the provider default (no extras).

    The tuple lists the kinds that actually exist in the code paths today
    (decision_service route_llm, eval judge/answer, plus their ablation_
    prefixed forms via the harness wrapper). The card-chain kinds
    (decision_card / hop_plan) are intentionally NOT here - see
    test_card_chain_kinds_disable_thinking.
    """
    import asyncio

    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    for kind in ("judge", "align", "route", "route_llm",
                 "ablation_judge", "ablation_answer", "ablation_route_llm"):
        model = router._get_model(
            llm_client.TIER_MID, temperature=0.0, kind=kind)
        assert model.kwargs["extra_body"] is None, kind
        # Fixture model 222 declares 8192, so this pins the non-card branch
        # to the configured cap (r20 review P2-2): only the thinking-disabled
        # branch may blank it.
        assert model.kwargs["max_output_tokens"] == 8192, kind
        FakeOpenAIModel.last_generate_kwargs = None
        asyncio.run(router.call_with_usage("prompt", kind=kind))
        assert FakeOpenAIModel.last_generate_kwargs == {}, kind


def test_card_chain_kinds_disable_thinking(monkeypatch):
    """pitfalls #59: the card chain burned the output cap into reasoning.

    e8-paired.log recorded 24x empty content for the ablation card/hop kinds
    (finish_reason=length rt=8192, and stop rt=3921 with empty content)
    while the identical burn had already been fixed for extract (r19). The
    JSON-object card kinds therefore join the disabled set - including the
    ``ablation_`` prefixed forms the harness sends - while judge-like kinds
    stay untouched (previous test).
    """
    import asyncio

    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    disabled = llm_client._NO_THINKING_EXTRA_BODY
    for kind in ("decision_card", "hop_plan",
                 "ablation_decision_card", "ablation_hop_plan"):
        model = router._get_model(
            llm_client.TIER_MID, temperature=0.0, kind=kind)
        assert model.kwargs["extra_body"] == disabled, kind
        # Fixture model 222 declares 8192: the disabled branch blanks the
        # client cap exactly like extract does (same code path).
        assert model.kwargs["max_output_tokens"] is None, kind
        FakeOpenAIModel.last_generate_kwargs = None
        asyncio.run(router.call_with_usage("prompt", kind=kind))
        assert FakeOpenAIModel.last_generate_kwargs == {
            "extra_body": disabled}, kind


def test_extract_and_chat_clients_cached_separately(monkeypatch):
    """One cached client per kind class: extract is reused, chat is distinct."""
    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    extract_first = router._get_model(
        llm_client.TIER_MID, temperature=0.0, kind="extract")
    extract_second = router._get_model(
        llm_client.TIER_MID, temperature=0.0, kind="extract")
    chat = router._get_model(
        llm_client.TIER_MID, temperature=0.0, kind="judge")
    assert extract_first is extract_second
    assert extract_first is not chat
    assert chat.kwargs["extra_body"] is None


def test_call_with_usage_routes_extract_to_thinking_disabled_client(monkeypatch):
    """The frozen async contract must pick the client by kind."""
    import asyncio

    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    text, usage = asyncio.run(
        router.call_with_usage("prompt", kind="extract"))
    assert text.startswith("ok:")
    # Core counters keep their meaning. The r21 P1-4 observability keys are
    # additive: this fake reports neither a provider payload nor tokens, so
    # they are honestly 0 / None (never fabricated).
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["reasoning_tokens"] == 0
    assert usage["finish_reason"] is None
    extract = router._get_model(
        llm_client.TIER_MID, temperature=0.0, kind="extract")
    assert extract.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def await_llm(callable_, prompt, **kwargs):
    import asyncio
    return asyncio.run(callable_(prompt, **kwargs))


# ---------------------------------------------------------------------------
# r21 P1-4 observability (pitfalls #52/#55 沉淀机制): finish_reason +
# reasoning_tokens surfaced per call, and the kind x empty-body counter.
# Everything below is scripted - no provider is ever contacted.
# ---------------------------------------------------------------------------


class _ScriptedUsageDetails:
    def __init__(self, reasoning_tokens):
        self.reasoning_tokens = reasoning_tokens


class _ScriptedUsage:
    def __init__(self, prompt_tokens, completion_tokens, reasoning_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.completion_tokens_details = _ScriptedUsageDetails(reasoning_tokens)


class _ScriptedRaw:
    """Provider ChatCompletion shape: choices[0].finish_reason + usage."""

    def __init__(self, finish_reason, reasoning_tokens):
        self.choices = [SimpleNamespace(finish_reason=finish_reason)]
        self.usage = _ScriptedUsage(3, 5, reasoning_tokens)


class _ScriptedTokenUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _ScriptedResult:
    def __init__(self, content, finish_reason, reasoning_tokens):
        self.content = content
        self.token_usage = _ScriptedTokenUsage(3, 5)
        self.raw = _ScriptedRaw(finish_reason, reasoning_tokens)


class ScriptedOpenAIModel:
    """OpenAIModel stand-in returning a scripted ChatMessage-shaped result.

    Mirrors smolagents' ``generate`` (the path ``call_with_usage`` takes): the
    provider payload is exposed via ``result.raw`` and tokens via
    ``result.token_usage``.
    """

    next_content = "ok"
    next_finish_reason = None
    next_reasoning_tokens = None
    last_generate_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def generate(self, messages, **kwargs):
        ScriptedOpenAIModel.last_generate_kwargs = kwargs
        return _ScriptedResult(
            ScriptedOpenAIModel.next_content,
            ScriptedOpenAIModel.next_finish_reason,
            ScriptedOpenAIModel.next_reasoning_tokens,
        )


def _install_scripted_model(monkeypatch, *, content, finish_reason, reasoning):
    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    monkeypatch.setattr(llm_client, "OpenAIModel", ScriptedOpenAIModel)
    monkeypatch.setattr(
        ScriptedOpenAIModel, "next_content", content, raising=False)
    monkeypatch.setattr(
        ScriptedOpenAIModel, "next_finish_reason", finish_reason, raising=False)
    monkeypatch.setattr(
        ScriptedOpenAIModel, "next_reasoning_tokens", reasoning, raising=False)
    return llm_client


def test_usage_carries_finish_reason_and_int_reasoning_tokens(monkeypatch):
    """P1-4 ①③: caller reads this call's finish_reason + reasoning_tokens."""
    llm_client = _install_scripted_model(
        monkeypatch, content='{"entities": []}',
        finish_reason="length", reasoning=8192)
    router = llm_client.LlmRouter(tenant_id=TENANT_A)

    text, usage = asyncio.run(router.call_with_usage("p", kind="extract"))

    assert text == '{"entities": []}'
    assert usage["finish_reason"] == "length"
    assert isinstance(usage["reasoning_tokens"], int)
    assert usage["reasoning_tokens"] == 8192
    # Additive: the pre-existing counters keep their meaning.
    assert usage["input_tokens"] == 3
    assert usage["output_tokens"] == 5


def test_reasoning_tokens_zero_when_provider_omits_them(monkeypatch):
    """P1-4 ①: an unreported reasoning split is 0, never fabricated."""
    llm_client = _install_scripted_model(
        monkeypatch, content="body", finish_reason="stop", reasoning=None)
    router = llm_client.LlmRouter(tenant_id=TENANT_A)

    _text, usage = asyncio.run(router.call_with_usage("p", kind="judge"))

    assert usage["reasoning_tokens"] == 0
    assert usage["finish_reason"] == "stop"


def test_empty_content_counter_is_per_kind(monkeypatch):
    """P1-4 ②: empty body bumps empty_content; non-empty only bumps calls."""
    llm_client = _install_scripted_model(
        monkeypatch, content="   ", finish_reason="length", reasoning=8192)
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    kind = "p14_empty_probe"
    before = llm_client.empty_content_counts().get(
        kind, {"calls": 0, "empty_content": 0})

    asyncio.run(router.call_with_usage("p", kind=kind))  # whitespace -> empty
    monkeypatch.setattr(
        ScriptedOpenAIModel, "next_content", "non-empty body", raising=False)
    asyncio.run(router.call_with_usage("p", kind=kind))
    asyncio.run(router.call_with_usage("p", kind=kind))

    after = llm_client.empty_content_counts()[kind]
    assert after["calls"] - before["calls"] == 3
    assert after["empty_content"] - before["empty_content"] == 1


def test_empty_content_emits_structured_event(monkeypatch, caplog):
    """P1-4 ②: every empty body logs event=llm_empty_content with the fields."""
    llm_client = _install_scripted_model(
        monkeypatch, content="", finish_reason="length", reasoning=8192)
    router = llm_client.LlmRouter(tenant_id=TENANT_A)

    with caplog.at_level(logging.WARNING, logger=llm_client.__name__):
        asyncio.run(router.call_with_usage("p", kind="p14_log_probe"))

    logged = [
        record.getMessage() for record in caplog.records
        if "event=llm_empty_content" in record.getMessage()
    ]
    assert logged, "empty content must emit event=llm_empty_content"
    assert "kind=p14_log_probe" in logged[-1]
    assert "finish_reason=length" in logged[-1]
    assert "reasoning_tokens=8192" in logged[-1]


def test_additive_keys_do_not_change_extract_wire_or_cache(monkeypatch):
    """P1-4 ④: extract still disables thinking and caches per kind-class."""
    llm_client = _install_scripted_model(
        monkeypatch, content="body", finish_reason="stop", reasoning=None)
    router = llm_client.LlmRouter(tenant_id=TENANT_A)

    asyncio.run(router.call_with_usage("p", kind="extract"))
    assert ScriptedOpenAIModel.last_generate_kwargs == {
        "extra_body": {"thinking": {"type": "disabled"}}}
    asyncio.run(router.call_with_usage("p", kind="judge"))
    assert ScriptedOpenAIModel.last_generate_kwargs == {}

    extract = router._get_model(llm_client.TIER_MID, 0.0, kind="extract")
    judge = router._get_model(llm_client.TIER_MID, 0.0, kind="judge")
    assert extract is not judge
    assert extract.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_last_usage_exposes_most_recent_call_diagnostics(monkeypatch):
    """T-24 read surface: last_usage() mirrors the last call's usage dict
    while __call__ keeps its frozen ``-> str`` contract."""
    llm_client = _install_scripted_model(
        monkeypatch, content="body", finish_reason="length", reasoning=8192)
    router = llm_client.LlmRouter(tenant_id=TENANT_A)

    assert router.last_usage() is None  # nothing measured yet

    asyncio.run(router.call_with_usage("p", kind="extract"))
    first = router.last_usage()
    assert first["finish_reason"] == "length"
    assert first["reasoning_tokens"] == 8192
    # A copy: mutating the returned dict must not corrupt router state.
    first["finish_reason"] = "mutated"
    assert router.last_usage()["finish_reason"] == "length"

    # __call__ still returns str, and refreshes the same read surface.
    monkeypatch.setattr(
        ScriptedOpenAIModel, "next_finish_reason", "stop", raising=False)
    out = asyncio.run(router("p", kind="extract"))
    assert isinstance(out, str)
    assert router.last_usage()["finish_reason"] == "stop"