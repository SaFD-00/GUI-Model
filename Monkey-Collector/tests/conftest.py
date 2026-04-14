"""Shared fixtures for all test modules."""

from unittest.mock import MagicMock

import pytest
from loguru import logger

from server.infra.device.adb import AdbClient
from server.infra.xml.ui_tree import UIElement, UITree
from tests.fixtures.xml_samples import COMPLEX_XML, MINIMAL_XML, SIMPLE_XML


@pytest.fixture(autouse=True, scope="session")
def _suppress_loguru():
    """Suppress loguru output during tests."""
    logger.disable("server")
    yield
    logger.enable("server")


# ── XML fixtures ──

@pytest.fixture
def minimal_xml():
    return MINIMAL_XML


@pytest.fixture
def simple_xml():
    return SIMPLE_XML


@pytest.fixture
def complex_xml():
    return COMPLEX_XML


@pytest.fixture
def simple_ui_tree(simple_xml):
    return UITree.from_xml_string(simple_xml)


@pytest.fixture
def complex_ui_tree(complex_xml):
    return UITree.from_xml_string(complex_xml)


# ── Element helpers ──

def make_element(**overrides) -> UIElement:
    """Create a UIElement with sensible defaults, overridable via kwargs."""
    defaults = dict(
        index=0,
        resource_id="",
        class_name="android.widget.TextView",
        text="",
        content_desc="",
        bounds=(0, 0, 100, 100),
        clickable=False,
        scrollable=False,
        enabled=True,
        checkable=False,
        checked=False,
        long_clickable=False,
        password=False,
        selected=False,
        package="com.test.app",
        visible=True,
        important=False,
    )
    defaults.update(overrides)
    return UIElement(**defaults)


def make_tree(element_defs: list[dict]) -> UITree:
    """Build a UITree from a list of override dicts."""
    elements = [make_element(index=i, **d) for i, d in enumerate(element_defs)]
    return UITree(elements)


# ── Mock ADB ──

@pytest.fixture
def mock_adb():
    adb = MagicMock(spec=AdbClient)
    adb.get_device_resolution.return_value = (1080, 1920)
    adb.get_current_package.return_value = "com.test.app"
    adb.shell.return_value = ""
    adb.tap.return_value = ""
    adb.swipe.return_value = ""
    adb.input_text.return_value = ""
    adb.press_back.return_value = ""
    adb.press_home.return_value = ""
    adb.long_press.return_value = ""
    adb.clear_text_field.return_value = ""
    adb.launch_app.return_value = ""
    adb.get_current_activity.return_value = "com.test.app/.MainActivity"
    adb.get_declared_activities.return_value = [
        "com.test.app/.MainActivity",
        "com.test.app/.SettingsActivity",
    ]
    return adb
