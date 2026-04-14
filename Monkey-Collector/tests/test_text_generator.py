"""Tests for server.text_generator — text generation strategies."""

import random
from unittest.mock import MagicMock, patch

import pytest

from server.pipeline.text_generator import (
    SAMPLE_TEXTS,
    LLMTextGenerator,
    RandomTextGenerator,
    create_text_generator,
)
from tests.conftest import make_element


@pytest.fixture
def dummy_element():
    return make_element(
        resource_id="com.test:id/search_input",
        class_name="android.widget.EditText",
        content_desc="Search field",
        text="",
    )


DUMMY_XML = '<hierarchy><node class="android.widget.EditText" bounds="[0,0][100,100]" /></hierarchy>'


class TestRandomTextGenerator:
    def test_returns_from_samples(self, dummy_element):
        rng = random.Random(42)
        gen = RandomTextGenerator(rng)
        for _ in range(20):
            result = gen.generate(dummy_element, DUMMY_XML)
            assert result in SAMPLE_TEXTS

    def test_deterministic_with_seed(self, dummy_element):
        results_a = [
            RandomTextGenerator(random.Random(42)).generate(dummy_element, "")
            for _ in range(5)
        ]
        results_b = [
            RandomTextGenerator(random.Random(42)).generate(dummy_element, "")
            for _ in range(5)
        ]
        assert results_a == results_b

    def test_custom_samples(self, dummy_element):
        custom = ["alpha", "beta", "gamma"]
        gen = RandomTextGenerator(random.Random(0), sample_texts=custom)
        for _ in range(20):
            assert gen.generate(dummy_element, "") in custom


class TestLLMTextGenerator:
    def _make_mock_client(self, output_text="Generated text"):
        """Create a mock OpenAI client using the Responses API."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = output_text
        mock_client.responses.create.return_value = mock_response
        return mock_client

    def test_success(self, dummy_element):
        gen = LLMTextGenerator(api_key="fake-key")
        gen._client = self._make_mock_client("Pizza recipe")
        result = gen.generate(dummy_element, DUMMY_XML)
        assert result == "Pizza recipe"

    def test_empty_response_fallback(self, dummy_element):
        gen = LLMTextGenerator(api_key="fake-key", rng=random.Random(42))
        gen._client = self._make_mock_client("")
        result = gen.generate(dummy_element, DUMMY_XML)
        assert result in SAMPLE_TEXTS

    def test_api_error_fallback(self, dummy_element):
        gen = LLMTextGenerator(api_key="fake-key", rng=random.Random(42))
        gen._client = MagicMock()
        gen._client.responses.create.side_effect = Exception("API down")
        result = gen.generate(dummy_element, DUMMY_XML)
        assert result in SAMPLE_TEXTS

    def test_lazy_client_init(self):
        gen = LLMTextGenerator(api_key="fake-key")
        assert gen._client is None

    def test_strips_quotes(self, dummy_element):
        gen = LLMTextGenerator(api_key="fake-key")
        gen._client = self._make_mock_client('"Hello World"')
        result = gen.generate(dummy_element, DUMMY_XML)
        assert result == "Hello World"

    def test_none_output_text_fallback(self, dummy_element):
        """output_text=None -> falls back to SAMPLE_TEXTS."""
        gen = LLMTextGenerator(api_key="fake-key", rng=random.Random(42))
        gen._client = self._make_mock_client(None)
        gen._client.responses.create.return_value.output_text = None
        result = gen.generate(dummy_element, DUMMY_XML)
        assert result in SAMPLE_TEXTS

    def test_empty_raw_xml(self, dummy_element):
        """Empty raw_xml -> API still called, text returned."""
        gen = LLMTextGenerator(api_key="fake-key")
        gen._client = self._make_mock_client("Search query")
        result = gen.generate(dummy_element, "")
        assert result == "Search query"
        gen._client.responses.create.assert_called_once()


class TestLLMCostTracking:
    def _make_mock_client(self, output_text="Generated text", input_tokens=100, output_tokens=20):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.output_text = output_text
        mock_response.usage.input_tokens = input_tokens
        mock_response.usage.output_tokens = output_tokens
        mock_client.responses.create.return_value = mock_response
        return mock_client

    def test_cost_recorded_on_success(self, dummy_element):
        mock_tracker = MagicMock()
        mock_tracker.record.return_value = {}
        gen = LLMTextGenerator(api_key="fake-key", cost_tracker=mock_tracker)
        gen._client = self._make_mock_client("Hello")
        gen.set_step(5)
        gen.generate(dummy_element, DUMMY_XML)
        mock_tracker.record.assert_called_once_with(
            model="gpt-5-nano",
            input_tokens=100,
            output_tokens=20,
            step=5,
        )

    def test_no_cost_on_api_failure(self, dummy_element):
        mock_tracker = MagicMock()
        gen = LLMTextGenerator(api_key="fake-key", cost_tracker=mock_tracker, rng=random.Random(42))
        gen._client = MagicMock()
        gen._client.responses.create.side_effect = Exception("API down")
        gen.generate(dummy_element, DUMMY_XML)
        mock_tracker.record.assert_not_called()

    def test_no_tracker_no_error(self, dummy_element):
        gen = LLMTextGenerator(api_key="fake-key")
        gen._client = self._make_mock_client("Result")
        result = gen.generate(dummy_element, DUMMY_XML)
        assert result == "Result"

    def test_set_step(self):
        gen = LLMTextGenerator(api_key="fake-key")
        gen.set_step(42)
        assert gen._current_step == 42


class TestCreateTextGenerator:
    def test_random_mode(self):
        gen = create_text_generator("random")
        assert isinstance(gen, RandomTextGenerator)

    def test_api_mode_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
        gen = create_text_generator("api")
        assert isinstance(gen, LLMTextGenerator)

    def test_api_mode_no_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        # Prevent load_dotenv from restoring the key from .env
        with patch("dotenv.load_dotenv", return_value=None):
            gen = create_text_generator("api")
        assert isinstance(gen, RandomTextGenerator)

    def test_api_mode_dotenv_unavailable(self, monkeypatch):
        """When python-dotenv not installed, still creates generator from env."""
        import sys

        monkeypatch.setenv("OPENAI_API_KEY", "test-key-456")
        # Setting module to None in sys.modules causes ImportError on import
        monkeypatch.setitem(sys.modules, "dotenv", None)
        gen = create_text_generator("api")
        assert isinstance(gen, LLMTextGenerator)
