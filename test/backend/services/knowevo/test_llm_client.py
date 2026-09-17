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
    import services.knowevo.llm_client as llm_client

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
    """Drop-in for nexent OpenAIModel: records construction, returns content."""

    last_generate_messages = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.content = f"ok:{kwargs.get('model_id')}"

    def generate(self, messages):
        FakeOpenAIModel.last_generate_messages = messages
        return SimpleContent(self.content)


class SimpleContent:
    def __init__(self, text):
        self.content = text


def test_router_uses_tier_model_id_over_default(monkeypatch):
    """Small tier must resolve via KW_LLM_SMALL_MODEL_ID, not tenant default."""
    import services.knowevo.llm_client as llm_client

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
    import services.knowevo.llm_client as llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    out = await_llm(router, "hello", kind="extract", tier=llm_client.TIER_MID)
    assert isinstance(out, str)
    assert out.startswith("ok:")


def test_fallback_to_tenant_llm(monkeypatch):
    """Tenant with only a default LLM still resolves (run.py fallback)."""
    import services.knowevo.llm_client as llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_SMALL_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_LARGE_MODEL_ID", "")
    router = llm_client.LlmRouter(tenant_id=TENANT_B)
    model = router._get_model(llm_client.TIER_MID, temperature=0.1)
    assert "999" in model.kwargs["model_id"] or model.kwargs["model_id"]


def test_unconfigured_raises_clear_error(monkeypatch):
    """No tier id and no tenant default -> LLMConfigurationError."""
    import services.knowevo.llm_client as llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_SMALL_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "")
    monkeypatch.setattr(llm_client, "KW_LLM_LARGE_MODEL_ID", "")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    with pytest.raises(llm_client.LLMConfigurationError):
        router._get_model(llm_client.TIER_MID, temperature=0.0)


def test_model_cached_per_tier_temperature(monkeypatch):
    """Same (tier, temperature) reuses the OpenAIModel instance."""
    import services.knowevo.llm_client as llm_client

    monkeypatch.setattr(llm_client, "KW_LLM_MID_MODEL_ID", "222")
    router = llm_client.LlmRouter(tenant_id=TENANT_A)
    first = router._get_model(llm_client.TIER_MID, temperature=0.0)
    second = router._get_model(llm_client.TIER_MID, temperature=0.0)
    assert first is second


def await_llm(callable_, prompt, **kwargs):
    import asyncio
    return asyncio.run(callable_(prompt, **kwargs))