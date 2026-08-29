"""Shared fixtures for all test modules."""

import pytest
from loguru import logger


@pytest.fixture(autouse=True, scope="session")
def _suppress_loguru():
    """Suppress loguru output during tests."""
    logger.disable("monkey_collector")
    yield
    logger.enable("monkey_collector")
