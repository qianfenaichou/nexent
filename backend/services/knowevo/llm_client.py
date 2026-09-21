"""T-08 wiring: real three-tier LLM callable for the KnowEvo services.

Implements the injected ``llm`` contract frozen by kg_service.py and
ontology_service.py: an async callable accepting ``(prompt, *, kind,
tier, temperature)`` and returning the raw text completion. Model
resolution mirrors the upstream standard (management/services/agent/run.py
fa-memory extraction): resolve a model config per tier, construct an
OpenAIModel client, and adapt its sync ``generate`` through
``asyncio.to_thread`` so the thread-hostile SDK call never blocks the
event loop.

Design inspired by: upstream run.py fa_memory_extractor wiring.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from nexent.core.models.openai_llm import OpenAIModel

from consts.const import (
    KW_LLM_LARGE_MODEL_ID,  # noqa: F401 - read live via _tier_model_id()
    KW_LLM_MID_MODEL_ID,  # noqa: F401 - read live via _tier_model_id()
    KW_LLM_SMALL_MODEL_ID,  # noqa: F401 - read live via _tier_model_id()
    MODEL_CONFIG_MAPPING,
)
from database.model_management_db import get_model_by_model_id
from utils.config_utils import get_model_name_from_config, tenant_config_manager

logger = logging.getLogger(__name__)

TIER_SMALL = "small"
TIER_MID = "mid"
TIER_LARGE = "large"

# Extraction asks for one JSON object; a hidden reasoning chain buys nothing
# and on reasoning-heavy providers it consumes the whole output cap, so the
# JSON never appears (r19 evidence: finish_reason=length with
# reasoning_tokens == completion_tokens == max_output_tokens and
# content_len == 0 - see pitfalls #52). Disable thinking for kind='extract'
# only; every other kind (judge / route / render / hop / align) keeps the
# provider default.
_EXTRACT_EXTRA_BODY = {"thinking": {"type": "disabled"}}
# Extraction deliberately sends no explicit output cap. The tenant's 8192
# cap is what turned a reasoning chain into an empty extraction in the
# first place, and raising it to 32768 is rejected by the sensenova gateway
# as over the tpm/rpm budget (HTTP 429 insufficient_quota, r19 streaming
# probe). With thinking disabled the provider's own default cap carries the
# JSON comfortably (r19 probe: 956 content chars / 333 completion tokens /
# finish_reason=stop), so None is both the safe and the measured choice.
# Non-extract kinds keep the bounded evaluation cap as before.

# Read live (not frozen at import) so env overrides and tests can patch
# the module constants after import.
_TIER_TO_MODEL_ID_ATTR = {
    TIER_SMALL: "KW_LLM_SMALL_MODEL_ID",
    TIER_MID: "KW_LLM_MID_MODEL_ID",
    TIER_LARGE: "KW_LLM_LARGE_MODEL_ID",
}


def _tier_model_id(tier: str) -> str:
    # Live read from module globals: env-driven constants stay patchable
    # after import (tests override them via monkeypatch).
    return globals()[_TIER_TO_MODEL_ID_ATTR[tier]]


class LLMConfigurationError(RuntimeError):
    """Raised when no usable model is configured for the requested tier."""


class LlmRouter:
    """Resolve a model config per tier and expose the async LLM callable.

    One OpenAIModel instance is cached per ``(tier, temperature)`` pair so
    repeated extraction/adjudication calls reuse the client instead of
    re-resolving configs. The ``__call__`` signature matches the injected
    ``llm`` contract exactly; ``kind`` is carried for logging and the
    cost ledger.
    """

    def __init__(self, tenant_id: str):
        self._tenant_id = tenant_id
        self._models: dict[tuple[str, float], Any] = {}
        self._lock = threading.Lock()

    def _resolve_model_config(self, tier: str) -> dict[str, Any]:
        """Pick the tier model id first, then fall back to the tenant LLM.

        The KwEvo tier ids (KW_LLM_SMALL/MID/LARGE_MODEL_ID) are platform
        model ids registered in the tenant; when absent or unresolvable we
        fall back to the tenant default LLM config (LLM_ID), matching the
        upstream run.py resolution order.
        """
        model_id = _tier_model_id(tier)
        if model_id:
            config = get_model_by_model_id(int(model_id), self._tenant_id)
            if config:
                return config
            logger.warning(
                "kw llm tier=%s model_id=%s unresolved for tenant=%s; "
                "falling back to tenant default",
                tier, model_id, self._tenant_id,
            )
        config = tenant_config_manager.get_model_config(
            MODEL_CONFIG_MAPPING["llm"], tenant_id=self._tenant_id)
        if not config:
            raise LLMConfigurationError(
                f"no LLM configured for tenant={self._tenant_id} tier={tier}; "
                "set KW_LLM_SMALL/MID/LARGE_MODEL_ID or tenant LLM_ID")
        return config

    def _get_model(self, tier: str, temperature: float,
                   kind: str | None = None) -> OpenAIModel:
        disable_thinking = kind == "extract"
        key = (tier, temperature, disable_thinking)
        with self._lock:
            model = self._models.get(key)
            if model is not None:
                return model
            config = self._resolve_model_config(tier)
            model = OpenAIModel(
                model_id=get_model_name_from_config(config),
                api_base=config.get("base_url", ""),
                api_key=config.get("api_key", ""),
                temperature=temperature,
                top_p=config.get("top_p", 0.9),
                model_factory=config.get("model_factory"),
                ssl_verify=config.get("ssl_verify", True),
                display_name=config.get("display_name") or None,
                timeout_seconds=config.get("timeout_seconds"),
                # Declared on the client for the `model(...)` path (nexent's
                # OpenAIModel.__call__ merges self.extra_body); the generate()
                # path used below needs the per-call kwarg instead, see
                # call_with_usage.
                extra_body=_EXTRACT_EXTRA_BODY if disable_thinking else None,
                # Output budget: extraction leaves the cap to the provider
                # (see the note above _EXTRACT_EXTRA_BODY); other callers
                # keep the bounded evaluation cap that prevents
                # reasoning-heavy judges from producing truncated mid-JSON,
                # and None keeps the provider default.
                max_output_tokens=(
                    None if disable_thinking
                    else config.get("max_output_tokens")),
            )
            self._models[key] = model
            return model

    async def __call__(
        self,
        prompt: str,
        *,
        kind: str,
        tier: str = TIER_MID,
        temperature: float = 0.0,
    ) -> str:
        """Run one completion; the prompt already carries system+user."""
        content, _usage = await self.call_with_usage(
            prompt, kind=kind, tier=tier, temperature=temperature)
        return content

    async def call_with_usage(
        self,
        prompt: str,
        *,
        kind: str,
        tier: str = TIER_MID,
        temperature: float = 0.0,
    ) -> tuple[str, dict[str, int]]:
        """Like ``__call__`` but also return the token counters.

        Additive sibling of the frozen ``(prompt, *, kind, tier,
        temperature) -> str`` contract: ``__call__`` stays byte-for-byte
        compatible for every existing caller, while the offline evaluation
        runner (T-10a-2) needs per-call tokens for the cost ledger and the
        p95/token metrics. Counters come from ``ChatMessage.token_usage``,
        which the SDK fills from the provider response (0 when the provider
        omitted usage - reported as measured, never estimated).
        """
        model = self._get_model(tier, temperature, kind=kind)
        messages = [{"role": "user", "content": prompt}]
        # The extract extras must ride on the call, not on the client.
        # `generate()` (smolagents' OpenAIModel) assembles its request body
        # from `self.kwargs`, and a named constructor argument never lands
        # there, so an instance-level extra_body is silently dropped: the
        # r19 wire probe showed completion_kwargs == ['messages', 'model']
        # and no `thinking` key in the outgoing body, which is why the
        # empty-content burn survived the client-level "fix". Passing it to
        # `generate(**kwargs)` reaches `_prepare_completion_kwargs`'s
        # `completion_kwargs.update(kwargs)` and from there the SDK's
        # `extra_body` merge.
        call_kwargs = (
            {"extra_body": _EXTRACT_EXTRA_BODY} if kind == "extract" else {})
        result = await asyncio.to_thread(
            model.generate, messages, **call_kwargs)
        content = getattr(result, "content", result)
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict))
        text = content if isinstance(content, str) else str(content)
        # Token counters live on the ChatMessage returned by generate
        # (``message.token_usage``); the model's own last_* attrs are only
        # set when usage reaches the stream-assembly path, so read the
        # message - the single source both paths write to.
        tu = getattr(result, "token_usage", None)
        usage = {
            "input_tokens": int(getattr(tu, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(tu, "output_tokens", 0) or 0),
        }
        return text, usage


def build_llm_callable(tenant_id: str) -> LlmRouter:
    """Entry point for wiring: one router per tenant/request scope."""
    return LlmRouter(tenant_id=tenant_id)