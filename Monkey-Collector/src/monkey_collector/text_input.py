"""Text generation strategies for ``input_text`` actions.

Two strategies:
  - :class:`RandomTextGenerator`: picks from a fixed sample list (legacy
    behavior, and the ``llm.input_mode="random"`` / no-API-key fallback).
  - :class:`LLMTextGenerator`: asks the shared
    :class:`~monkey_collector.llm.client.LLMClient` (OpenRouter Chat
    Completions, e.g. ``qwen/qwen3.8-flash``) for contextually appropriate
    text; falls back to random on any failure or empty response.

:func:`create_text_generator` picks between them based on
``RunConfig.llm.input_mode``.

Restored from the device-push era (``pipeline/text_generator.py``, removed in
the M1b teardown) with one interface change: ``generate`` now takes raw
scalar fields instead of a ``UIElement``. That type was deleted along with
the old XML parser, and the explorer's own element abstraction is not decided
until M3 — this module must not guess it. Callers pass whatever fields
they've resolved for the target field; M3's explorer (or a test) can supply
them from any element representation it likes.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from monkey_collector.config import LlmConfig
    from monkey_collector.llm.client import LLMClient

# Default sample texts (same as the device-push era's explorer.SAMPLE_TEXTS).
SAMPLE_TEXTS = [
    "Hello World",
    "Test Note",
    "Meeting at 3pm",
    "Shopping list",
    "Important memo",
    "John Doe",
    "test@example.com",
    "12345",
    "New item",
    "Quick note",
]

SYSTEM_PROMPT = (
    "You are a mobile app tester generating realistic input text for Android UI fields.\n"
    "Given the current screen's UI structure and a target input field, generate a single "
    "realistic text value that a real user would type into this field.\n\n"
    "Rules:\n"
    "- Return ONLY the text to type, nothing else (no quotes, no explanation)\n"
    "- Make the text contextually appropriate for the field and the app screen\n"
    "- Vary your responses: use different names, addresses, search terms, etc.\n"
    "- Keep text concise (1-30 characters typically)\n"
    "- Use the field's resource ID, description, and surrounding UI context as clues\n"
    "- For search fields: generate diverse search queries relevant to the app\n"
    "- For name fields: generate realistic names\n"
    "- For email fields: generate realistic email addresses\n"
    "- For number fields: generate appropriate numbers\n"
    "- For note/memo fields: generate short realistic notes\n"
    "- For password fields: generate a test password like \"Test1234!\"\n"
    "- When an 'App under test' description is provided, tailor the text to that "
    "app's domain (e.g. product search terms for a shopping app, note content for "
    "a notes app, a task title for a to-do app)"
)

USER_PROMPT_TEMPLATE = (
    "<screen>{screen_xml}</screen>\n\n"
    "Target input field:\n"
    "- resource_id: {resource_id}\n"
    "- content_desc: {content_desc}\n"
    "- current_text: {current_text}\n"
    "- display_name: {display_name}\n\n"
    "Generate appropriate input text for this field."
)


class TextGenerator(ABC):
    """Base class for input text generation strategies."""

    @abstractmethod
    def generate(
        self,
        *,
        resource_id: str,
        content_desc: str,
        current_text: str,
        display_name: str,
        screen_xml: str,
    ) -> str:
        """Generate text appropriate for the target field on the current screen.

        Args:
            resource_id: the field's ``resource-id`` attribute, or ``""``.
            content_desc: the field's ``content-desc`` attribute, or ``""``.
            current_text: the field's current ``text`` value, or ``""``.
            display_name: a human-readable label for the field, or ``""``.
            screen_xml: the current screen, already encoded as a string for
                the prompt (or ``""`` if none is available). Callers should
                pass the output of
                :func:`monkey_collector.xml.parse_device_xml_absolute`, NOT
                :func:`monkey_collector.xml.parse_device_xml`.
                ``parse_device_xml`` scales every box into the 840x1876
                EXPORT frame — that resize exists to satisfy the EXP08
                ``data-bbox`` contract, and pulling it into an input-text
                prompt would force this call site to thread device
                width/height through just to satisfy that resize's frame
                cross-check — for a task (picking realistic text for a field)
                that only needs structure, resource-ids and visible text, not
                pixel-perfect coordinates. ``parse_device_xml_absolute`` needs
                no width/height and carries the same structure/text/ids, so it
                is exactly as informative here at lower coupling; its
                coordinates being in device space rather than export space is
                irrelevant to a prompt that never reads them for tapping.
        """

    def set_app_context(self, app_context: str) -> None:  # noqa: B027
        """Set a human-readable description of the app under test.

        Optional hook used by LLM-backed strategies to ground generated text in
        the current app's domain. Intentionally a concrete no-op (not abstract)
        so strategies that ignore context (e.g. random generation) need not
        override it.
        """


class RandomTextGenerator(TextGenerator):
    """Select a random text from a fixed sample list. Ignores all screen context."""

    def __init__(self, rng: random.Random, sample_texts: list[str] | None = None):
        self._rng = rng
        self._sample_texts = sample_texts or SAMPLE_TEXTS

    def generate(
        self,
        *,
        resource_id: str,
        content_desc: str,
        current_text: str,
        display_name: str,
        screen_xml: str,
    ) -> str:
        return self._rng.choice(self._sample_texts)


class LLMTextGenerator(TextGenerator):
    """Use the shared :class:`LLMClient` to generate contextual input text."""

    def __init__(
        self,
        llm_client: LLMClient,
        fallback_texts: list[str] | None = None,
        rng: random.Random | None = None,
    ):
        self._client = llm_client
        self._fallback_texts = fallback_texts or SAMPLE_TEXTS
        self._rng = rng or random.Random()
        self._app_context = ""

    def set_app_context(self, app_context: str) -> None:
        self._app_context = (app_context or "").strip()

    def generate(
        self,
        *,
        resource_id: str,
        content_desc: str,
        current_text: str,
        display_name: str,
        screen_xml: str,
    ) -> str:
        try:
            app_block = (
                f"App under test: {self._app_context}\n\n" if self._app_context else ""
            )
            user_msg = app_block + USER_PROMPT_TEMPLATE.format(
                screen_xml=screen_xml or "",
                resource_id=resource_id or "(none)",
                content_desc=content_desc or "(none)",
                current_text=current_text or "(empty)",
                display_name=display_name or "(unknown)",
            )

            text = self._client.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=50,
                agent="text_generator",
            )
            text = (text or "").strip().strip('"').strip("'")
            if text:
                logger.debug(f"LLM generated text: {text!r}")
                return text

            logger.warning("LLM returned empty text, falling back to random")
            return self._rng.choice(self._fallback_texts)

        except Exception as e:
            # Any SDK/network/parsing failure falls back to random rather than
            # propagating — a text generator that can crash the run over a
            # flaky API call would be worse than one that occasionally picks
            # a canned sample.
            logger.warning(f"LLM text generation failed ({e}), falling back to random")
            return self._rng.choice(self._fallback_texts)


def create_text_generator(
    llm_config: LlmConfig,
    *,
    llm_client: LLMClient | None = None,
    seed: int = 42,
    sample_texts: list[str] | None = None,
) -> TextGenerator:
    """Factory: build a :class:`TextGenerator` from config and an optional client.

    Args:
        llm_config: the resolved ``RunConfig.llm`` section. Consumes
            ``llm_config.input_mode`` (``"api"`` | ``"random"``).
        llm_client: a pre-built shared client (e.g. from
            :func:`monkey_collector.llm.create_llm_client`), or ``None``. This
            factory does not build a client itself — API-key handling already
            lives in :func:`~monkey_collector.llm.client.create_llm_client`,
            and duplicating it here would give that decision two homes.
        seed: RNG seed for the random-text strategy (and the LLM strategy's
            fallback RNG).
        sample_texts: override the default fixed sample list.

    ``input_mode="api"`` with ``llm_client=None`` (most commonly:
    ``OPENROUTER_API_KEY`` unset) does NOT fall back to random silently: a
    WARNING is logged here naming the fallback, because which mode actually
    ran changes how the collected input text should be interpreted later.
    """
    rng = random.Random(seed)
    mode = llm_config.input_mode

    if mode == "random" or llm_client is None:
        if mode == "api" and llm_client is None:
            logger.warning(
                "llm.input_mode='api' requested but no LLM client is available "
                "(most likely OPENROUTER_API_KEY is unset) — falling back to "
                "RandomTextGenerator for this run. Input text will be canned "
                "samples, not model-generated; check logs/cost.csv to confirm "
                "which mode actually ran."
            )
        return RandomTextGenerator(rng, sample_texts)

    logger.info(f"Using LLM text generation (model: {llm_client.model})")
    return LLMTextGenerator(
        llm_client=llm_client,
        fallback_texts=sample_texts or SAMPLE_TEXTS,
        rng=rng,
    )


__all__ = [
    "SAMPLE_TEXTS",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "LLMTextGenerator",
    "RandomTextGenerator",
    "TextGenerator",
    "create_text_generator",
]
