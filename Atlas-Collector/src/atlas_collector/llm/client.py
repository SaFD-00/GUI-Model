"""OpenRouter (OpenAI-compatible Chat Completions) client.

**The LLM has exactly one job in Atlas-Collector: generating input text for text
fields.** Page identity is decided structurally (BM25 + element diff + pixel
gate) and never calls this client. Do not widen the LLM's remit without
revisiting ARCHITECTURE.md — the LLM-free page identity is a hard constraint.

OpenRouter speaks the OpenAI **Chat Completions** API (``chat.completions``),
not the Responses API — so this client uses ``client.chat.completions.create``
and reads ``usage.prompt_tokens`` / ``usage.completion_tokens`` (the Responses
API names them ``input_tokens`` / ``output_tokens``).

The ``openai`` SDK is imported lazily inside the methods so that importing this
module — and therefore the CLI — costs nothing and works with the dependency
absent.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai import OpenAI

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "qwen/qwen3.8-flash"
DEFAULT_TIMEOUT = 30.0
API_KEY_ENV = "OPENROUTER_API_KEY"
BASE_URL_ENV = "OPENROUTER_BASE_URL"
MODEL_ENV = "OPENROUTER_MODEL"


@dataclass
class TokenUsage:
    """Accumulated token counts for one client instance."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    #: Per-agent breakdown, keyed by the ``agent`` label passed to :meth:`chat`.
    by_agent: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(self, agent: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.calls += 1
        bucket = self.by_agent.setdefault(
            agent, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        )
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["calls"] += 1


#: Signature of the accounting hook: ``(agent, prompt_tokens, completion_tokens, model)``.
UsageHook = Callable[[str, int, int, str], None]


class LLMClient:
    """Thin wrapper over the OpenAI SDK pointed at OpenRouter.

    Args:
        api_key: OpenRouter key. Read from ``OPENROUTER_API_KEY`` by
            :func:`create_llm_client` when not given.
        model: chat model id.
        base_url: OpenRouter Chat Completions base URL.
        usage_hook: optional accounting callback, invoked once per successful
            call *after* :attr:`usage` is updated. This is the seam a later
            milestone's cost tracker plugs into — nothing here writes files.
        timeout: per-call timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        *,
        usage_hook: UsageHook | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.base_url = base_url
        self._usage_hook = usage_hook
        self._timeout = timeout
        self._client: OpenAI | None = None  # lazy-init
        self.usage = TokenUsage()

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self._api_key)
        return self._client

    def chat(
        self,
        messages: str | list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict | None = None,
        agent: str = "llm",
        timeout: float | None = None,
    ) -> str:
        """Run a single chat completion and return the message content.

        Args:
            messages: a single user prompt string, or a list of
                ``{"role", "content"}`` dicts.
            max_tokens: optional cap on output tokens.
            temperature: optional sampling temperature.
            response_format: e.g. ``{"type": "json_object"}`` (providers that do
                not support it ignore it).
            agent: accounting label, e.g. ``"text_generator"``.
            timeout: per-call override of the client default.
        """
        payload = (
            [{"role": "user", "content": messages}] if isinstance(messages, str) else messages
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload,
            "timeout": timeout if timeout is not None else self._timeout,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format is not None:
            kwargs["response_format"] = response_format

        response = self._get_client().chat.completions.create(**kwargs)
        self._account(response, agent)
        return (response.choices[0].message.content or "").strip()

    def _account(self, response: Any, agent: str) -> None:
        """Record token usage and fire the accounting hook. Never raises."""
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        self.usage.record(agent, prompt_tokens, completion_tokens)
        if self._usage_hook is not None:
            try:
                self._usage_hook(agent, prompt_tokens, completion_tokens, self.model)
            except Exception as exc:  # accounting must never break collection
                logger.warning(f"[llm] usage hook raised, ignored: {exc}")


def create_llm_client(
    *,
    model: str | None = None,
    base_url: str | None = None,
    usage_hook: UsageHook | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> LLMClient | None:
    """Build an :class:`LLMClient`, or return None when no API key is set.

    Returning None rather than raising is deliberate: input-text generation
    falls back to hardcoded ``random`` text, and collection continues. Page
    identity is unaffected either way — it never touches the LLM.
    """
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        logger.warning(
            f"{API_KEY_ENV} is not set — input text generation falls back to 'random'. "
            "Page identity is LLM-free and unaffected."
        )
        return None
    return LLMClient(
        api_key=api_key,
        model=model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL,
        base_url=base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL,
        usage_hook=usage_hook,
        timeout=timeout,
    )


__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "LLMClient",
    "TokenUsage",
    "UsageHook",
    "create_llm_client",
]
