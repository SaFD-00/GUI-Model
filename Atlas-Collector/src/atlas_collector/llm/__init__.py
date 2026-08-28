"""LLM access for Atlas-Collector.

The LLM is used for **input text generation only** — page identity is LLM-free.
See :mod:`atlas_collector.llm.client`.
"""

from __future__ import annotations

from atlas_collector.llm.client import (
    API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMClient,
    TokenUsage,
    create_llm_client,
)

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LLMClient",
    "TokenUsage",
    "create_llm_client",
]
