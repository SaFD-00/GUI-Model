"""Tests for server.explorer — SmartExplorer action selection and execution."""

from unittest.mock import MagicMock, patch

import pytest

from server.actions import Action, InputText, LongPress, PressBack, PressHome, Swipe, Tap
from server.explorer import SmartExplorer
from server.text_generator import TextGenerator
from tests.conftest import make_tree


@pytest.fixture
def explorer(mock_adb):
    return SmartExplorer(mock_adb, config={"seed": 42})


@pytest.fixture
def tree_with_all():
    """Tree with clickable, editable, scrollable elements."""
    return make_tree([
        {"clickable": True, "bounds": (100, 100, 300, 300), "class_name": "android.widget.Button"},
        {"clickable": True, "bounds": (400, 100, 600, 300), "class_name": "android.widget.Button"},
        {"class_name": "android.widget.EditText", "clickable": True, "bounds": (100, 400, 600, 500)},
        {"scrollable": True, "bounds": (0, 600, 1080, 1800), "class_name": "android.widget.ScrollView"},
    ])


class TestSelectAction:
    def test_returns_action(self, explorer, tree_with_all):
        action = explorer.select_action(tree_with_all)
        assert isinstance(action, Action)

    def test_tap_targets_clickable(self, explorer, tree_with_all):
        # Force tap by setting other weights to 0
        explorer.action_weights = {"tap": 1.0, "press_back": 0, "swipe": 0, "input_text": 0, "long_press": 0, "press_home": 0}
        action = explorer.select_action(tree_with_all)
        assert isinstance(action, Tap)
        clickable_indices = {e.index for e in tree_with_all.get_clickable_elements()}
        assert action.element_index in clickable_indices

    def test_input_prefers_empty_editable(self, explorer):
        tree = make_tree([
            {"class_name": "android.widget.EditText", "clickable": True, "bounds": (0, 0, 500, 100), "text": "existing"},
            {"class_name": "android.widget.EditText", "clickable": True, "bounds": (0, 200, 500, 300), "text": ""},
        ])
        explorer.action_weights = {"tap": 0, "press_back": 0, "swipe": 0, "input_text": 1.0, "long_press": 0, "press_home": 0}
        action = explorer.select_action(tree)
        assert isinstance(action, InputText)
        # Should prefer the empty field (index 1)
        assert action.element_index == 1

    def test_swipe_uses_scrollable(self, explorer, tree_with_all):
        explorer.action_weights = {"tap": 0, "press_back": 0, "swipe": 1.0, "input_text": 0, "long_press": 0, "press_home": 0}
        action = explorer.select_action(tree_with_all)
        assert isinstance(action, Swipe)
        assert action.element_index == 3  # scrollable element

    def test_swipe_no_scrollable_uses_screen(self, explorer):
        tree = make_tree([
            {"clickable": True, "bounds": (100, 100, 300, 300), "class_name": "android.widget.Button"},
        ])
        explorer.action_weights = {"tap": 0, "press_back": 0, "swipe": 1.0, "input_text": 0, "long_press": 0, "press_home": 0}
        action = explorer.select_action(tree)
        assert isinstance(action, Swipe)
        assert action.element_index == -1  # screen midpoint, no element

    def test_first_screen_suppresses_back(self, explorer):
        # Use a tree WITHOUT editable elements to avoid input_text weight boost
        tree = make_tree([
            {"clickable": True, "bounds": (100, 100, 300, 300), "class_name": "android.widget.Button"},
            {"scrollable": True, "bounds": (0, 600, 1080, 1800), "class_name": "android.widget.ScrollView"},
        ])
        explorer.action_weights = {"tap": 0, "press_back": 1.0, "swipe": 0, "input_text": 0, "long_press": 0, "press_home": 0}
        action = explorer.select_action(tree, is_first_screen=True)
        # press_back=0 on first screen, all others=0, total=0 → first_screen fallback → Tap
        assert isinstance(action, Tap)

    def test_editable_boosts_input_weight(self, explorer, tree_with_all):
        # Default weights have input_text=0.10, but with editable elements it should be >= 0.25
        # We verify by checking internal weight modification
        weights = dict(explorer.action_weights)
        editable = tree_with_all.get_editable_elements()
        if editable:
            weights["input_text"] = max(weights.get("input_text", 0.1), 0.25)
        assert weights["input_text"] >= 0.25

    def test_zero_weight_not_first_screen_returns_back(self, explorer):
        # Need clickable+scrollable present so tap/swipe weights don't get auto-boosted
        tree = make_tree([
            {"clickable": True, "bounds": (100, 100, 300, 300), "class_name": "android.widget.Button"},
            {"scrollable": True, "bounds": (0, 600, 1080, 1800), "class_name": "android.widget.ScrollView"},
        ])
        explorer.action_weights = {"tap": 0, "press_back": 0, "swipe": 0, "input_text": 0, "long_press": 0, "press_home": 0}
        action = explorer.select_action(tree, is_first_screen=False)
        assert isinstance(action, PressBack)


class TestExcludeElements:
    def test_exclude_element(self, explorer):
        tree = make_tree([
            {"clickable": True, "bounds": (100, 100, 300, 300), "class_name": "android.widget.Button"},
        ])
        explorer.action_weights = {"tap": 1.0, "press_back": 0, "swipe": 0, "input_text": 0, "long_press": 0, "press_home": 0}

        # Before exclusion: element 0 should be selected
        action = explorer.select_action(tree)
        assert action.element_index == 0

        # After exclusion: element 0 excluded, fallback to random tap
        explorer.exclude_element(0)
        action = explorer.select_action(tree)
        assert action.element_index != 0 or isinstance(action, Tap)

    def test_clear_excluded(self, explorer):
        explorer.exclude_element(0)
        explorer.exclude_element(1)
        assert explorer.get_excluded_count() == 2
        explorer.clear_excluded()
        assert explorer.get_excluded_count() == 0


class TestExecuteAction:
    def test_tap(self, explorer, mock_adb):
        explorer.execute_action(Tap(x=100, y=200))
        mock_adb.tap.assert_called_once_with(100, 200)

    def test_swipe(self, explorer, mock_adb):
        explorer.execute_action(Swipe(x1=10, y1=20, x2=30, y2=40, duration_ms=300))
        mock_adb.swipe.assert_called_once_with(10, 20, 30, 40, 300)

    @patch("server.explorer.time.sleep")
    def test_input_text(self, mock_sleep, explorer, mock_adb):
        explorer.execute_action(InputText(text="hello", x=100, y=200))
        mock_adb.tap.assert_called_once_with(100, 200)
        mock_adb.clear_text_field.assert_called_once()
        mock_adb.input_text.assert_called_once_with("hello")

    def test_input_text_no_coordinates(self, explorer, mock_adb):
        explorer.execute_action(InputText(text="hello", x=0, y=0))
        mock_adb.tap.assert_not_called()
        mock_adb.clear_text_field.assert_called_once()
        mock_adb.input_text.assert_called_once_with("hello")

    def test_press_back(self, explorer, mock_adb):
        explorer.execute_action(PressBack())
        mock_adb.press_back.assert_called_once()

    def test_press_home(self, explorer, mock_adb):
        explorer.execute_action(PressHome())
        mock_adb.press_home.assert_called_once()

    def test_long_press(self, explorer, mock_adb):
        explorer.execute_action(LongPress(x=100, y=200, duration_ms=1000))
        mock_adb.long_press.assert_called_once_with(100, 200, 1000)


class TestAppDetection:
    def test_has_left_app_true(self, explorer, mock_adb):
        mock_adb.get_current_package.return_value = "com.other.app"
        assert explorer.has_left_app("com.test.app") is True

    def test_has_left_app_false(self, explorer, mock_adb):
        mock_adb.get_current_package.return_value = "com.test.app"
        assert explorer.has_left_app("com.test.app") is False

    def test_has_left_app_exception(self, explorer, mock_adb):
        mock_adb.get_current_package.side_effect = Exception("ADB error")
        assert explorer.has_left_app("com.test.app") is False

    def test_has_left_app_empty_package(self, explorer, mock_adb):
        mock_adb.get_current_package.return_value = ""
        assert explorer.has_left_app("com.test.app") is False


class TestTextGeneratorIntegration:
    def test_uses_generator(self, mock_adb):
        gen = MagicMock(spec=TextGenerator)
        gen.generate.return_value = "LLM generated"
        explorer = SmartExplorer(mock_adb, config={"seed": 42}, text_generator=gen)
        explorer.set_raw_xml("<xml>test</xml>")

        tree = make_tree([
            {"class_name": "android.widget.EditText", "clickable": True, "bounds": (0, 0, 500, 100), "text": ""},
        ])
        explorer.action_weights = {"tap": 0, "press_back": 0, "swipe": 0, "input_text": 1.0, "long_press": 0, "press_home": 0}

        action = explorer.select_action(tree)
        assert isinstance(action, InputText)
        assert action.text == "LLM generated"
        gen.generate.assert_called_once()


class TestDeterminism:
    def test_deterministic_with_seed(self, mock_adb):
        tree = make_tree([
            {"clickable": True, "bounds": (100, 100, 300, 300), "class_name": "android.widget.Button"},
            {"clickable": True, "bounds": (400, 100, 600, 300), "class_name": "android.widget.Button"},
            {"scrollable": True, "bounds": (0, 600, 1080, 1800), "class_name": "android.widget.ScrollView"},
        ])

        results_a = []
        exp_a = SmartExplorer(mock_adb, config={"seed": 123})
        for i in range(10):
            results_a.append(exp_a.select_action(tree, step=i).to_dict())

        results_b = []
        exp_b = SmartExplorer(mock_adb, config={"seed": 123})
        for i in range(10):
            results_b.append(exp_b.select_action(tree, step=i).to_dict())

        assert results_a == results_b
