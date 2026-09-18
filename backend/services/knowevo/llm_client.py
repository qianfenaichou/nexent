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

    def _get_model(self, tier: str, temperature: float) -> OpenAIModel:
        key = (tier, temperature)
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
                # Bounded output for offline evaluation: without an explicit
                # cap the provider default left reasoning-heavy judges
                # truncated mid-JSON (observed as unparseable judge output).
                # None keeps the provider default for other callers.
                max_output_tokens=config.get("max_output_tokens"),
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
        model = self._get_model(tier, temperature)
        messages = [{"role": "user", "content": prompt}]
        result = await asyncio.to_thread(model.generate, messages)
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