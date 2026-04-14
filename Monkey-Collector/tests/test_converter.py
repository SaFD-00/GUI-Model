"""Tests for server.converter — session-to-JSONL conversion."""

import json

from server.export.converter import (
    Converter,
    _find_element_at,
    _map_event_to_action,
    generate_example,
)
from server.infra.xml.parser.structured_parser import parse_to_html_xml
from server.infra.xml.ui_tree import parse_uiautomator_xml
from tests.conftest import make_element
from tests.fixtures.session_fixtures import create_mock_session
from tests.fixtures.xml_samples import COMPLEX_XML, SIMPLE_XML

SIMPLE_PARSED = parse_to_html_xml(SIMPLE_XML) or ""
COMPLEX_PARSED = parse_to_html_xml(COMPLEX_XML) or ""


class TestFindElementAt:
    def test_exact_center(self):
        elems = [make_element(index=0, bounds=(100, 100, 200, 200))]
        result = _find_element_at(elems, 150, 150)
        assert result is not None
        assert result.index == 0

    def test_smallest_wins(self):
        big = make_element(index=0, bounds=(0, 0, 1000, 1000))
        small = make_element(index=1, bounds=(100, 100, 200, 200))
        result = _find_element_at([big, small], 150, 150)
        assert result.index == 1

    def test_no_match(self):
        elems = [make_element(index=0, bounds=(100, 100, 200, 200))]
        assert _find_element_at(elems, 500, 500) is None

    def test_on_boundary(self):
        elems = [make_element(index=0, bounds=(100, 100, 200, 200))]
        result = _find_element_at(elems, 100, 100)
        assert result is not None


class TestMapEventToAction:
    def _elements(self):
        return [
            make_element(index=0, bounds=(0, 0, 500, 500)),
            make_element(index=1, bounds=(100, 100, 200, 200)),
        ]

    def test_tap_with_element_index(self):
        result = _map_event_to_action(
            {"action_type": "tap", "element_index": 3, "x": 0, "y": 0},
            self._elements(),
        )
        assert result["type"] == "Click"
        assert result["index"] == 3

    def test_tap_coordinate_fallback(self):
        result = _map_event_to_action(
            {"action_type": "tap", "element_index": -1, "x": 150, "y": 150},
            self._elements(),
        )
        assert result["type"] == "Click"
        assert result["index"] == 1  # smallest element at (150,150)

    def test_swipe_up(self):
        result = _map_event_to_action(
            {"action_type": "swipe", "x1": 500, "y1": 800, "x2": 500, "y2": 200},
            [],
        )
        assert result["type"] == "Swipe"
        assert result["params"]["direction"] == "Up"

    def test_swipe_down(self):
        result = _map_event_to_action(
            {"action_type": "swipe", "x1": 500, "y1": 200, "x2": 500, "y2": 800},
            [],
        )
        assert result["params"]["direction"] == "Down"

    def test_swipe_left(self):
        result = _map_event_to_action(
            {"action_type": "swipe", "x1": 800, "y1": 500, "x2": 200, "y2": 500},
            [],
        )
        assert result["params"]["direction"] == "Left"

    def test_swipe_right(self):
        result = _map_event_to_action(
            {"action_type": "swipe", "x1": 200, "y1": 500, "x2": 800, "y2": 500},
            [],
        )
        assert result["params"]["direction"] == "Right"

    def test_swipe_no_coords_default_up(self):
        result = _map_event_to_action({"action_type": "swipe"}, [])
        assert result["params"]["direction"] == "Up"

    def test_input_text(self):
        result = _map_event_to_action(
            {"action_type": "input_text", "text": "hello", "element_index": 1}, []
        )
        assert result["type"] == "Input"
        assert result["params"]["text"] == "hello"

    def test_press_back(self):
        result = _map_event_to_action({"action_type": "press_back"}, [])
        assert result["type"] == "Back"

    def test_long_press(self):
        result = _map_event_to_action(
            {"action_type": "long_press", "x": 150, "y": 150, "element_index": -1},
            self._elements(),
        )
        assert result["type"] == "LongClick"

    def test_press_home(self):
        result = _map_event_to_action({"action_type": "press_home"}, [])
        assert result["type"] == "Home"

    def test_unknown_type(self):
        assert _map_event_to_action({"action_type": "teleport"}, []) is None


class TestGenerateExample:
    def test_sharegpt_format(self):
        elements = parse_uiautomator_xml(SIMPLE_XML)
        result = generate_example(
            SIMPLE_PARSED, COMPLEX_PARSED,
            {"action_type": "tap", "element_index": 2, "x": 978, "y": 84},
            "images/0001.png",
            before_elements=elements,
        )
        assert result is not None
        msgs = result["messages"]
        assert len(msgs) == 3
        assert msgs[0]["from"] == "system"
        assert msgs[1]["from"] == "human"
        assert msgs[2]["from"] == "gpt"
        assert "images" in result

    def test_skip_no_state_change(self):
        result = generate_example(
            SIMPLE_PARSED, SIMPLE_PARSED,
            {"action_type": "tap", "element_index": 2},
            "img.png",
        )
        assert result is None

    def test_skip_empty_xml(self):
        result = generate_example(
            "", SIMPLE_PARSED,
            {"action_type": "tap"}, "img.png",
        )
        assert result is None


class TestConverterSession:
    def test_convert_session(self, tmp_path):
        session_dir = create_mock_session(tmp_path)
        output_path = tmp_path / "output.jsonl"
        images_dir = tmp_path / "images"

        converter = Converter(str(output_path), str(images_dir))
        count = converter.convert_session(str(session_dir), session_label=1)

        assert count >= 1
        assert output_path.exists()
        lines = output_path.read_text().strip().split("\n")
        assert len(lines) == count
        for line in lines:
            data = json.loads(line)
            assert "messages" in data

    def test_convert_session_insufficient_xml(self, tmp_path):
        session_dir = tmp_path / "short_session"
        xml_dir = session_dir / "xml"
        xml_dir.mkdir(parents=True)
        (xml_dir / "0000.xml").write_text(SIMPLE_XML)
        if SIMPLE_PARSED:
            (xml_dir / "0000_parsed.xml").write_text(SIMPLE_PARSED)
        (session_dir / "metadata.json").write_text("{}")

        output_path = tmp_path / "output.jsonl"
        converter = Converter(str(output_path), str(tmp_path / "images"))
        assert converter.convert_session(str(session_dir), session_label=1) == 0

    def test_convert_all(self, tmp_path):
        raw_dir = tmp_path / "raw"
        create_mock_session(raw_dir, "session_a")
        create_mock_session(raw_dir, "session_b")

        output_path = tmp_path / "output.jsonl"
        converter = Converter(str(output_path), str(tmp_path / "images"))
        total = converter.convert_all(str(raw_dir))
        assert total >= 2  # at least 1 per session


class TestFindEventByIndex:
    def test_out_of_range(self):
        """Index beyond events -> empty dict."""
        result = Converter._find_event_by_index({0: {"step": 0}}, index=5)
        assert result == {}


class TestConvertAllEmpty:
    def test_empty_dir(self, tmp_path):
        """Empty directory (no valid sessions) -> 0."""
        output = tmp_path / "output.jsonl"
        images = tmp_path / "images"
        converter = Converter(str(output), str(images))
        result = converter.convert_all(str(tmp_path))
        assert result == 0
