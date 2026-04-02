"""Tests for server.text_generator — text generation strategies."""

import random
from unittest.mock import MagicMock, patch

import pytest

from server.text_generator import (
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
