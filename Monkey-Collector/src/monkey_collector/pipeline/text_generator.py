"""Text generation strategies for InputText actions.

Two strategies:
  - RandomTextGenerator: picks from a fixed sample list (legacy behavior).
  - LLMTextGenerator: calls OpenAI API with screen context to produce
    contextually appropriate text; falls back to random on failure.
"""

from __future__ import annotations

import os
import random
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from loguru import logger

from monkey_collector.xml.structured_parser import encode_to_html_xml
from monkey_collector.xml.ui_tree import UIElement

if TYPE_CHECKING:
    from monkey_collector.domain.cost_tracker import CostTracker

# Default sample texts (same as explorer.SAMPLE_TEXTS)
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
    "- For password fields: generate a test password like \"Test1234!\""
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
    def generate(self, element: UIElement, raw_xml: str) -> str:
        """Generate text appropriate for *element* given the current screen XML."""


class RandomTextGenerator(TextGenerator):
    """Select a random text from a fixed sample list."""

    def __init__(self, rng: random.Random, sample_texts: list[str] | None = None):
        self._rng = rng
        self._sample_texts = sample_texts or SAMPLE_TEXTS

    def generate(self, element: UIElement, raw_xml: str) -> str:
        return self._rng.choice(self._sample_texts)


class LLMTextGenerator(TextGenerator):
    """Call OpenAI API to generate contextually appropriate input text."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5-nano",
        fallback_texts: list[str] | None = None,
        rng: random.Random | None = None,
        cost_tracker: CostTracker | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._fallback_texts = fallback_texts or SAMPLE_TEXTS
        self._rng = rng or random.Random()
        self._client = None  # lazy-init
        self._cost_tracker = cost_tracker
        self._current_step: int = 0

    def set_step(self, step: int) -> None:
        """Set the current exploration step for cost tracking."""
        self._current_step = step

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def generate(self, element: UIElement, raw_xml: str) -> str:
        try:
            screen_xml = encode_to_html_xml(raw_xml) if raw_xml else ""
            user_msg = USER_PROMPT_TEMPLATE.format(
                screen_xml=screen_xml,
                resource_id=element.resource_id or "(none)",
                content_desc=element.content_desc or "(none)",
                current_text=element.text or "(empty)",
                display_name=element.display_name or "(unknown)",
            )

            client = self._get_client()
            response = client.responses.create(
                model=self._model,
                instructions=SYSTEM_PROMPT,
                input=user_msg,
                max_output_tokens=50,
                reasoning={"effort": "minimal"},
                text={"verbosity": "low"},
            )

            if self._cost_tracker and hasattr(response, "usage") and response.usage:
                self._cost_tracker.record(
                    model=self._model,
                    input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
                    step=self._current_step,
                )

            text = (response.output_text or "").strip().strip('"').strip("'")
            if text:
                logger.debug(f"LLM generated text: {text!r}")
                return text

            logger.warning("LLM returned empty text, falling back to random")
            return self._rng.choice(self._fallback_texts)

        except Exception as e:
            logger.warning(f"LLM text generation failed ({e}), falling back to random")
            return self._rng.choice(self._fallback_texts)


def create_text_generator(
    mode: str,
    seed: int = 42,
    sample_texts: list[str] | None = None,
    cost_tracker: CostTracker | None = None,
) -> TextGenerator:
    """Factory: create a TextGenerator based on *mode*.

    Args:
        mode: ``"api"`` for LLM-based or ``"random"`` for hardcoded sample texts.
        seed: Random seed for reproducibility.
        sample_texts: Override default sample texts.
    """
    rng = random.Random(seed)

    if mode == "random":
        return RandomTextGenerator(rng, sample_texts)

    # mode == "api"
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        logger.warning("python-dotenv not installed, reading OPENAI_API_KEY from environment only")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning(
            "OPENAI_API_KEY not set — falling back to random text generation. "
            "Set it in .env or environment to use LLM-based input."
        )
        return RandomTextGenerator(rng, sample_texts)

    logger.info("Using LLM text generation (model: gpt-5-nano)")
    return LLMTextGenerator(
        api_key=api_key,
        model="gpt-5-nano",
        fallback_texts=sample_texts or SAMPLE_TEXTS,
        rng=rng,
        cost_tracker=cost_tracker,
    )
