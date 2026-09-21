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

# JSON-object kinds ask for one machine-readable object; a hidden reasoning
# chain buys nothing and on reasoning-heavy providers it consumes the whole
# output cap, so the JSON never appears (r19 evidence for kind='extract':
# finish_reason=length with reasoning_tokens == completion_tokens ==
# max_output_tokens and content_len == 0 - see pitfalls #52). The same burn
# hit the decision-card chain in the E8 paired run (e8-paired.log: 24x
# event=llm_empty_content kind=ablation_decision_card / ablation_hop_plan,
# finish_reason=length rt=8192 AND finish_reason=stop rt=3921, content empty
# every time) while judge / route / answer kinds never went empty - so the
# disabled set grows to exactly the card-chain JSON kinds and nothing else
# (pitfalls #59). Judge/align/route/route_llm keep the provider default:
# nothing observed is broken there, and the judge's quality must not be
# quietly changed by this fix.
_THINKING_DISABLED_KINDS = frozenset({"extract", "decision_card", "hop_plan"})
_ABLATION_KIND_PREFIX = "ablation_"
_NO_THINKING_EXTRA_BODY = {"thinking": {"type": "disabled"}}


def _thinking_disabled(kind: str | None) -> bool:
    """True for the JSON-object kinds, incl. their ``ablation_`` forms.

    The ablation harness wraps this router and prefixes every kind with
    ``ablation_`` (``pipeline/ablation.py``), so the prefix is stripped
    before set membership; an unlisted kind (judge / route / answer ...)
    keeps the provider default.
    """
    if not kind:
        return False
    base = kind.removeprefix(_ABLATION_KIND_PREFIX)
    return base in _THINKING_DISABLED_KINDS


# Thinking-disabled kinds leave the client-level output cap unset. Honest
# attribution (r20 review P2-4): the causal lever is the per-call `thinking`
# flag above. The generate() path assembles its request body from
# self.kwargs, so this client attribute never reached the wire for any kind
# (r19 wire probe: max_tokens_on_wire=null); the 8192 that truncated the JSON
# was the provider's own default. Leaving it unset is kept as a deliberate
# cleanup: with thinking disabled the provider default measured comfortable
# (r19 probe: 956 content chars / 333 completion tokens / finish_reason=stop),
# and raising it to 32768 trips the gateway tpm/rpm budget (HTTP 429, r19
# streaming probe). All other kinds keep the previously configured value.

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


# Pitfalls #52/#55 observability: a process-level, thread-safe tally of how
# often each call ``kind`` returns empty content, plus the per-call
# ``finish_reason``/``reasoning_tokens`` that keep "no content" separable from
# "no entities". ``call_with_usage`` runs inside ``asyncio.to_thread`` workers
# and long eval batches, so the tally is lock-guarded and the read path hands
# back a copy. Keys are the raw ``kind`` strings (e.g. "extract", "hop_plan").
_EMPTY_CONTENT_LOCK = threading.Lock()
_EMPTY_CONTENT_COUNTS: dict[str, dict[str, int]] = {}


def _record_call_outcome(
    kind: str, text: str, finish_reason: str | None, reasoning_tokens: int,
) -> None:
    """Tally one call; log a structured event when the body is empty.

    Empty means ``str(text).strip() == ""`` - the symptom behind both #52 (a
    reasoning chain consumed the whole output budget) and #55 (transient empty
    generation). ``finish_reason``/``reasoning_tokens`` are this call's
    measured values, so a "no content" line stays distinguishable from a
    parsed-but-empty "no entities" body.
    """
    is_empty = not str(text).strip()
    with _EMPTY_CONTENT_LOCK:
        bucket = _EMPTY_CONTENT_COUNTS.setdefault(
            kind, {"calls": 0, "empty_content": 0})
        bucket["calls"] += 1
        if is_empty:
            bucket["empty_content"] += 1
    if is_empty:
        logger.warning(
            "event=llm_empty_content kind=%s finish_reason=%s reasoning_tokens=%s",
            kind, finish_reason, reasoning_tokens)


def empty_content_counts() -> dict[str, dict[str, int]]:
    """Read-only copy of the per-kind ``{calls, empty_content}`` tally."""
    with _EMPTY_CONTENT_LOCK:
        return {
            kind: dict(bucket) for kind, bucket in _EMPTY_CONTENT_COUNTS.items()
        }


def _observe_call(model: Any, result: Any) -> tuple[str | None, int]:
    """Return ``(finish_reason, reasoning_tokens)`` measured for one call.

    ``call_with_usage`` reaches the provider through smolagents'
    ``OpenAIModel.generate`` (non-streaming), so the returned
    ``ChatMessage.raw`` is the provider ``ChatCompletion`` and this call's
    ``finish_reason`` / ``usage.completion_tokens_details.reasoning_tokens``
    live there (r21 probe on the generate path:
    ``raw.choices[0].finish_reason == "length"``,
    ``raw.usage.completion_tokens_details.reasoning_tokens == 8192``; the
    adapter's ``last_response_diagnostics`` stays None because the streaming
    ``__call__`` is not on this path). ``last_response_diagnostics`` is used
    only when ``raw`` is not a completion payload - e.g. a caller that went
    through the streaming adapter. Anything unmeasured stays ``None`` / ``0``:
    never estimated, never fabricated.
    """
    raw = getattr(result, "raw", None)
    if hasattr(raw, "choices") and hasattr(raw, "usage"):
        choices = getattr(raw, "choices", None) or []
        finish_reason = (
            getattr(choices[0], "finish_reason", None) if choices else None)
        reported = getattr(
            getattr(raw.usage, "completion_tokens_details", None),
            "reasoning_tokens", None)
        reasoning_tokens = int(reported) if isinstance(reported, (int, float)) else 0
        return (
            str(finish_reason) if finish_reason is not None else None,
            reasoning_tokens,
        )
    diagnostics = getattr(model, "last_response_diagnostics", None)
    if isinstance(diagnostics, dict):
        finish_reason = diagnostics.get("finish_reason")
        reported = diagnostics.get("reasoning_tokens")
        return (
            str(finish_reason) if finish_reason is not None else None,
            int(reported) if isinstance(reported, (int, float)) else 0,
        )
    return None, 0


class LLMConfigurationError(RuntimeError):
    """Raised when no usable model is configured for the requested tier."""


class LlmRouter:
    """Resolve a model config per tier and expose the async LLM callable.

    One OpenAIModel instance is cached per ``(tier, temperature,
    disable_thinking)`` triple so repeated extraction/adjudication calls
    reuse the client instead of re-resolving configs (r20 review P2-3: the
    key grew a boolean when the extract path started disabling thinking).
    The ``__call__`` signature matches the injected ``llm`` contract
    exactly; ``kind`` is carried for logging and the cost ledger.
    """

    def __init__(self, tenant_id: str):
        self._tenant_id = tenant_id
        self._models: dict[tuple[str, float, bool], Any] = {}
        self._lock = threading.Lock()
        # T-24 read surface: the most recent call's usage dict, exposed via
        # last_usage() so a span-level caller can aggregate call-level
        # diagnostics (finish_reason / reasoning_tokens) WITHOUT changing the
        # frozen ``__call__ -> str`` contract.
        self._last_usage: dict[str, Any] | None = None

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
        disable_thinking = _thinking_disabled(kind)
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
                extra_body=_NO_THINKING_EXTRA_BODY if disable_thinking else None,
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

    def last_usage(self) -> dict[str, Any] | None:
        """Copy of the most recent call's usage dict, or None before any call.

        T-24 (pitfalls #52/#55 沉淀机制, product side): ``__call__`` keeps
        returning ``str`` for every existing caller; this additive read
        surface lets a span-level caller (pipeline/ingest_graph.py) aggregate
        ``reasoning_tokens`` and ``finish_reason`` per call into the
        kg_extract_run_t ledger without re-plumbing the injected-``llm``
        contract. A copy is returned so callers cannot mutate router state.
        """
        with self._lock:
            return (
                dict(self._last_usage)
                if self._last_usage is not None else None
            )

    async def call_with_usage(
        self,
        prompt: str,
        *,
        kind: str,
        tier: str = TIER_MID,
        temperature: float = 0.0,
    ) -> tuple[str, dict[str, Any]]:
        """Like ``__call__`` but also return token counters + observability.

        Additive sibling of the frozen ``(prompt, *, kind, tier,
        temperature) -> str`` contract: ``__call__`` stays byte-for-byte
        compatible for every existing caller, while the offline evaluation
        runner (T-10a-2) needs per-call tokens for the cost ledger and the
        p95/token metrics. ``input_tokens``/``output_tokens`` come from
        ``ChatMessage.token_usage``, which the SDK fills from the provider
        response (0 when the provider omitted usage - reported as measured,
        never estimated).

        The returned dict is purely additive over those two counters:
        ``reasoning_tokens`` (int, 0 when the provider reports none) and
        ``finish_reason`` (str | None) satisfy the pitfalls #52 沉淀机制 -
        the extraction chain can now tell a ``length`` truncation that burned
        the output budget on a reasoning chain apart from a genuine empty
        body. Existing callers read via ``.get()`` and are unaffected.
        """
        model = self._get_model(tier, temperature, kind=kind)
        messages = [{"role": "user", "content": prompt}]
        # The no-thinking extras must ride on the call, not on the client.
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
            {"extra_body": _NO_THINKING_EXTRA_BODY}
            if _thinking_disabled(kind) else {})
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
        finish_reason, reasoning_tokens = _observe_call(model, result)
        usage = {
            "input_tokens": int(getattr(tu, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(tu, "output_tokens", 0) or 0),
            # Additive observability (pitfalls #52/#55); see _observe_call.
            "reasoning_tokens": reasoning_tokens,
            "finish_reason": finish_reason,
        }
        _record_call_outcome(kind, text, finish_reason, reasoning_tokens)
        with self._lock:
            self._last_usage = usage
        return text, usage


def build_llm_callable(tenant_id: str) -> LlmRouter:
    """Entry point for wiring: one router per tenant/request scope."""
    return LlmRouter(tenant_id=tenant_id)