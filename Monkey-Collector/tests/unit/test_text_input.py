"""Tests for monkey_collector.text_input — input-text generation strategies."""

import random
from contextlib import contextmanager
from unittest.mock import MagicMock

from loguru import logger

from monkey_collector.config import LlmConfig
from monkey_collector.text_input import (
    SAMPLE_TEXTS,
    LLMTextGenerator,
    RandomTextGenerator,
    create_text_generator,
)

DUMMY_XML = '<hierarchy><node class="android.widget.EditText" bounds="[0,0][100,100]" /></hierarchy>'

FIELD_KWARGS = dict(
    resource_id="com.test:id/search_input",
    content_desc="Search field",
    current_text="",
    display_name="Search",
    screen_xml=DUMMY_XML,
)


@contextmanager
def captured_warnings():
    """Collect loguru WARNING messages emitted inside the block.

    tests/conftest.py disables the "monkey_collector" logger namespace for the
    whole session so other tests stay quiet; re-enable it for the duration of
    the block, since these tests are pinning the WARNING itself, not treating
    it as incidental noise.
    """
    messages: list[str] = []
    handler_id = logger.add(messages.append, level="WARNING", format="{message}")
    logger.enable("monkey_collector")
    try:
        yield messages
    finally:
        logger.disable("monkey_collector")
        logger.remove(handler_id)


def _mock_client(text="Generated text", model="qwen/qwen3.8-flash"):
    """A stand-in LLMClient whose chat() returns *text*."""
    client = MagicMock()
    client.model = model
    client.chat.return_value = text
    return client


class TestRandomTextGenerator:
    def test_returns_from_samples(self):
        rng = random.Random(42)
        gen = RandomTextGenerator(rng)
        for _ in range(20):
            assert gen.generate(**FIELD_KWARGS) in SAMPLE_TEXTS

    def test_deterministic_with_seed(self):
        results_a = [
            RandomTextGenerator(random.Random(42)).generate(**FIELD_KWARGS) for _ in range(5)
        ]
        results_b = [
            RandomTextGenerator(random.Random(42)).generate(**FIELD_KWARGS) for _ in range(5)
        ]
        assert results_a == results_b

    def test_custom_samples(self):
        custom = ["alpha", "beta", "gamma"]
        gen = RandomTextGenerator(random.Random(0), sample_texts=custom)
        for _ in range(20):
            assert gen.generate(**FIELD_KWARGS) in custom

    def test_set_app_context_is_noop(self):
        before = RandomTextGenerator(random.Random(42)).generate(**FIELD_KWARGS)
        gen = RandomTextGenerator(random.Random(42))
        gen.set_app_context("Amazon Shopping")  # must not raise
        after = gen.generate(**FIELD_KWARGS)
        assert before == after  # context does not affect random output


class TestLLMTextGenerator:
    def test_success(self):
        gen = LLMTextGenerator(_mock_client("Pizza recipe"))
        assert gen.generate(**FIELD_KWARGS) == "Pizza recipe"

    def test_empty_response_fallback(self):
        gen = LLMTextGenerator(_mock_client(""), rng=random.Random(42))
        assert gen.generate(**FIELD_KWARGS) in SAMPLE_TEXTS

    def test_none_response_fallback(self):
        gen = LLMTextGenerator(_mock_client(None), rng=random.Random(42))
        assert gen.generate(**FIELD_KWARGS) in SAMPLE_TEXTS

    def test_api_error_does_not_propagate_and_falls_back(self):
        client = _mock_client()
        client.chat.side_effect = Exception("API down")
        gen = LLMTextGenerator(client, rng=random.Random(42))
        # Must not raise — a flaky provider call cannot crash the run.
        result = gen.generate(**FIELD_KWARGS)
        assert result in SAMPLE_TEXTS

    def test_strips_quotes(self):
        gen = LLMTextGenerator(_mock_client('"Hello World"'))
        assert gen.generate(**FIELD_KWARGS) == "Hello World"

    def test_empty_screen_xml_is_accepted(self):
        client = _mock_client("Search query")
        gen = LLMTextGenerator(client)
        kwargs = dict(FIELD_KWARGS, screen_xml="")
        assert gen.generate(**kwargs) == "Search query"
        client.chat.assert_called_once()

    def test_sends_system_and_user_messages(self):
        client = _mock_client("x")
        gen = LLMTextGenerator(client)
        gen.generate(**FIELD_KWARGS)

        args, kwargs = client.chat.call_args
        messages = args[0]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert kwargs.get("agent") == "text_generator"
        assert kwargs.get("max_tokens") == 50

    def test_app_context_included_in_user_message(self):
        client = _mock_client("x")
        gen = LLMTextGenerator(client)
        gen.set_app_context("Amazon Shopping (Shopping/General) — Top e-commerce")
        gen.generate(**FIELD_KWARGS)

        user_msg = client.chat.call_args.args[0][1]["content"]
        assert "App under test: Amazon Shopping (Shopping/General) — Top e-commerce" in user_msg

    def test_no_app_context_omits_line(self):
        client = _mock_client("x")
        gen = LLMTextGenerator(client)
        gen.generate(**FIELD_KWARGS)

        user_msg = client.chat.call_args.args[0][1]["content"]
        assert "App under test:" not in user_msg


class TestCreateTextGenerator:
    def test_random_mode_never_touches_llm_client(self):
        client = _mock_client()
        gen = create_text_generator(LlmConfig(input_mode="random"), llm_client=client)
        assert isinstance(gen, RandomTextGenerator)
        gen.generate(**FIELD_KWARGS)
        client.chat.assert_not_called()

    def test_api_mode_with_client_uses_llm(self):
        client = _mock_client("from llm")
        gen = create_text_generator(LlmConfig(input_mode="api"), llm_client=client)
        assert isinstance(gen, LLMTextGenerator)
        assert gen.generate(**FIELD_KWARGS) == "from llm"

    def test_api_mode_no_client_falls_back_to_random_with_no_llm_calls(self):
        gen = create_text_generator(LlmConfig(input_mode="api"), llm_client=None)
        assert isinstance(gen, RandomTextGenerator)
        # RandomTextGenerator has no LLM client at all — generation cannot call one.
        assert gen.generate(**FIELD_KWARGS) in SAMPLE_TEXTS

    def test_api_mode_no_client_warns_about_the_fallback(self):
        with captured_warnings() as messages:
            create_text_generator(LlmConfig(input_mode="api"), llm_client=None)
        assert any("falling back to" in m.lower() and "random" in m.lower() for m in messages)

    def test_random_mode_ignores_client_without_warning(self):
        with captured_warnings() as messages:
            gen = create_text_generator(LlmConfig(input_mode="random"), llm_client=_mock_client())
        assert isinstance(gen, RandomTextGenerator)
        assert messages == []

    def test_no_api_key_end_to_end_falls_back_to_random_with_warning(self, monkeypatch):
        """input_mode='api' with no OPENROUTER_API_KEY: the actual failure this
        criterion is about, not just 'no client was passed in'. Chains the real
        create_llm_client() (which returns None without a key) into
        create_text_generator(), exactly as M4's wiring will.
        """
        from unittest.mock import patch

        from monkey_collector.llm.client import create_llm_client

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with patch("dotenv.load_dotenv", return_value=None):
            llm_client = create_llm_client()
        assert llm_client is None

        with captured_warnings() as messages:
            gen = create_text_generator(LlmConfig(input_mode="api"), llm_client=llm_client)

        assert isinstance(gen, RandomTextGenerator)
        assert any("falling back to" in m.lower() and "random" in m.lower() for m in messages)
