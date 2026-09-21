"""Unit tests for services/knowevo/llm_client.py (T-08 wiring).

Layer 1 (always runs): tier resolution, tenant default fallback, the
unconfigured error path, and the injected-``llm`` callable contract.
No network and no real model: ``get_model_by_model_id`` and
``tenant_config_manager`` are monkey-patched to in-memory records.
"""
import sys
from pathlib import Path

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
    """judge/align/route/render/hop keep the provider default (no extras)."""
    import asyncio

    from services.knowevo import llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    for kind in ("judge", "align", "route", "render", "hop"):
        model = router._get_model(
            llm_client.TIER_MID, temperature=0.0, kind=kind)
        assert model.kwargs["extra_body"] is None, kind
        # Fixture model 222 declares 8192, so this pins the non-extract
        # branch to the configured cap (r20 review P2-2): only the extract
        # branch may blank it.
        assert model.kwargs["max_output_tokens"] == 8192, kind
        FakeOpenAIModel.last_generate_kwargs = None
        asyncio.run(router.call_with_usage("prompt", kind=kind))
        assert FakeOpenAIModel.last_generate_kwargs == {}, kind


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
    assert usage == {"input_tokens": 0, "output_tokens": 0}
    extract = router._get_model(
        llm_client.TIER_MID, temperature=0.0, kind="extract")
    assert extract.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def await_llm(callable_, prompt, **kwargs):
    import asyncio
    return asyncio.run(callable_(prompt, **kwargs))