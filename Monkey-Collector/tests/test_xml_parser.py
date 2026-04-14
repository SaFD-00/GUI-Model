"""Tests for server.xml_parser — UIElement, UITree, XML parsing."""

from server.infra.xml.ui_tree import UIElement, UITree, parse_bounds, parse_uiautomator_xml
from tests.conftest import make_element


class TestParseBounds:
    def test_valid(self):
        assert parse_bounds("[10,20][300,400]") == (10, 20, 300, 400)

    def test_invalid(self):
        assert parse_bounds("garbage") == (0, 0, 0, 0)

    def test_empty(self):
        assert parse_bounds("") == (0, 0, 0, 0)

    def test_zeros(self):
        assert parse_bounds("[0,0][0,0]") == (0, 0, 0, 0)


class TestUIElementProperties:
    def test_area(self):
        elem = make_element(bounds=(0, 0, 100, 200))
        assert elem.area == 20000

    def test_area_zero_bounds(self):
        elem = make_element(bounds=(0, 0, 0, 0))
        assert elem.area == 0

    def test_area_inverted_bounds(self):
        # When bounds are inverted, w and h are both negative, product is positive
        elem = make_element(bounds=(100, 200, 50, 100))
        assert elem.area == 5000  # (-50)*(-100) = 5000, max(0, 5000) = 5000

    def test_center(self):
        elem = make_element(bounds=(0, 0, 100, 200))
        assert elem.center == (50, 100)

    def test_center_offset(self):
        elem = make_element(bounds=(100, 200, 300, 400))
        assert elem.center == (200, 300)

    def test_short_class(self):
        elem = make_element(class_name="android.widget.TextView")
        assert elem.short_class == "TextView"

    def test_short_class_empty(self):
        elem = make_element(class_name="")
        assert elem.short_class == ""

    def test_display_name_content_desc(self):
        elem = make_element(content_desc="Search", text="ignore", resource_id="ignore:id/x")
        assert elem.display_name == "Search"

    def test_display_name_text(self):
        elem = make_element(content_desc="", text="Hello")
        assert elem.display_name == "Hello"

    def test_display_name_resource_id(self):
        elem = make_element(content_desc="", text="", resource_id="com.app:id/my_button")
        assert elem.display_name == "my button"

    def test_display_name_class_fallback(self):
        elem = make_element(
            content_desc="", text="", resource_id="",
            class_name="android.widget.Button",
        )
        assert elem.display_name == "Button"


class TestParseXML:
    def test_simple_xml(self, simple_xml):
        elements = parse_uiautomator_xml(simple_xml)
        # 7 nodes in XML, but root FrameLayout (index=0) and all visible ones
        # Expected: index 0-6 visible, index 0 has bounds [0,0][1080,1920] so included
        assert len(elements) >= 5

    def test_filters_invisible(self):
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="hidden" class="android.widget.TextView"
        bounds="[10,10][100,100]" visible-to-user="false"
        clickable="false" enabled="true" scrollable="false"
        checkable="false" checked="false" long-clickable="false"
        password="false" selected="false" package="com.test" />
</hierarchy>"""
        elements = parse_uiautomator_xml(xml)
        assert len(elements) == 0

    def test_filters_zero_bounds(self):
        xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="zero" class="android.widget.TextView"
        bounds="[0,0][0,0]" visible-to-user="true"
        clickable="false" enabled="true" scrollable="false"
        checkable="false" checked="false" long-clickable="false"
        password="false" selected="false" package="com.test" />
</hierarchy>"""
        elements = parse_uiautomator_xml(xml)
        assert len(elements) == 0

    def test_invalid_xml(self):
        assert parse_uiautomator_xml("<not valid xml!!!") == []

    def test_empty_string(self):
        assert parse_uiautomator_xml("") == []


class TestUITree:
    def test_get_clickable(self, simple_ui_tree):
        clickable = simple_ui_tree.get_clickable_elements()
        assert len(clickable) >= 2  # search_btn + fab (EditText is also clickable)
        for e in clickable:
            assert e.clickable and e.enabled and e.area > 0

    def test_get_editable(self, simple_ui_tree):
        editable = simple_ui_tree.get_editable_elements()
        assert len(editable) >= 1
        assert any("EditText" in e.class_name for e in editable)

    def test_get_scrollable(self, simple_ui_tree):
        scrollable = simple_ui_tree.get_scrollable_elements()
        assert len(scrollable) >= 1
        assert all(e.scrollable for e in scrollable)

    def test_get_interactable(self, simple_ui_tree):
        interactable = simple_ui_tree.get_interactable_elements()
        # Should include clickable + scrollable + editable
        assert len(interactable) >= 3

    def test_from_xml_string(self, simple_xml):
        tree = UITree.from_xml_string(simple_xml)
        assert len(tree) >= 5

    def test_len_and_iter(self, simple_ui_tree):
        n = len(simple_ui_tree)
        items = list(simple_ui_tree)
        assert len(items) == n
        assert all(isinstance(e, UIElement) for e in items)

    def test_empty_tree_from_minimal_xml(self, minimal_xml):
        tree = UITree.from_xml_string(minimal_xml)
        assert len(tree) == 0
        assert tree.get_clickable_elements() == []
