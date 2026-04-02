"""Tests for server.xml_encoder — XML transformation pipeline."""

import xml.etree.ElementTree as ET

from server.xml_encoder import (
    encode_to_html_xml,
    indent_xml,
    parse_to_html_xml,
    reformat_xml,
    remove_nodes_with_empty_bounds,
    remove_redundancies,
    simplify_structure,
)


def _make_node(class_name, bounds="[10,10][200,200]", **attrs):
    """Helper to create a minimal uiautomator XML string with one node."""
    defaults = {
        "text": "",
        "resource-id": "",
        "content-desc": "",
        "checkable": "false",
        "checked": "false",
        "clickable": "false",
        "enabled": "true",
        "scrollable": "false",
        "long-clickable": "false",
        "password": "false",
        "selected": "false",
        "important": "false",
        "index": "0",
    }
    defaults.update(attrs)
    defaults["class"] = class_name
    defaults["bounds"] = bounds
    attr_str = " ".join(f'{k}="{v}"' for k, v in defaults.items())
    return f'<node {attr_str} />'


def _wrap_hierarchy(*nodes):
    inner = "\n".join(nodes)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<hierarchy rotation="0">\n{inner}\n</hierarchy>'


class TestReformatXml:
    def _get_child(self, xml_str):
        """Parse reformat result from hierarchy-wrapped XML; return first child."""
        result = reformat_xml(xml_str)
        root = ET.fromstring(result)
        # hierarchy becomes <div>, child is the actual converted node
        children = list(root)
        return children[0] if children else root

    def test_edittext_to_input(self):
        xml = _wrap_hierarchy(_make_node("android.widget.EditText", text="hello"))
        child = self._get_child(xml)
        assert child.tag == "input"
        assert child.text == "hello"

    def test_checkable_to_checker(self):
        xml = _wrap_hierarchy(
            _make_node("android.widget.Switch", checkable="true", checked="true")
        )
        child = self._get_child(xml)
        assert child.tag == "checker"
        assert child.attrib.get("checked") == "true"

    def test_clickable_to_button(self):
        xml = _wrap_hierarchy(
            _make_node("android.widget.ImageButton", clickable="true", **{"content-desc": "Go"})
        )
        child = self._get_child(xml)
        assert child.tag == "button"
        assert "clickable" not in child.attrib

    def test_layout_to_div(self):
        xml_with_child = _wrap_hierarchy(
            f'<node class="android.widget.FrameLayout" bounds="[0,0][1080,1920]" '
            f'index="0" text="" resource-id="" content-desc="" checkable="false" '
            f'checked="false" clickable="false" enabled="true" scrollable="false" '
            f'long-clickable="false" password="false" selected="false" important="false">'
            f'{_make_node("android.widget.TextView", text="child")}'
            f'</node>'
        )
        child = self._get_child(xml_with_child)
        assert child.tag == "div"

    def test_imageview_to_img(self):
        xml = _wrap_hierarchy(
            _make_node("android.widget.ImageView", **{"content-desc": "photo"})
        )
        child = self._get_child(xml)
        assert child.tag == "img"

    def test_textview_to_p(self):
        xml = _wrap_hierarchy(_make_node("android.widget.TextView", text="Hello"))
        child = self._get_child(xml)
        assert child.tag == "p"
        assert child.text == "Hello"

    def test_resource_id_stripped(self):
        xml = _wrap_hierarchy(
            _make_node(
                "android.widget.ImageButton",
                clickable="true",
                **{"resource-id": "com.app:id/my_fab", "content-desc": "fab"},
            )
        )
        child = self._get_child(xml)
        assert child.attrib.get("id") == "my_fab"

    def test_invalid_xml(self):
        assert reformat_xml("<not valid!!!") == ""

    def test_scrollable_to_scroll(self):
        xml_with_child = _wrap_hierarchy(
            f'<node class="android.widget.ScrollView" bounds="[0,0][1080,1920]" '
            f'index="0" text="" resource-id="" content-desc="" checkable="false" '
            f'checked="false" clickable="false" enabled="true" scrollable="true" '
            f'long-clickable="false" password="false" selected="false" important="false">'
            f'{_make_node("android.widget.TextView", text="item")}'
            f'</node>'
        )
        child = self._get_child(xml_with_child)
        assert child.tag == "scroll"


class TestSimplifyStructure:
    def test_collapses_single_child(self):
        xml = '<div bounds="[0,0][1080,1920]"><div bounds="[0,0][1080,1920]"><p>Hello</p></div></div>'
        result = simplify_structure(xml)
        root = ET.fromstring(result)
        assert root.tag == "p"
        assert root.text == "Hello"

    def test_preserves_button(self):
        xml = '<button bounds="[0,0][100,100]"><p>Click</p></button>'
        result = simplify_structure(xml)
        root = ET.fromstring(result)
        assert root.tag == "button"

    def test_preserves_text_attr(self):
        xml = '<div text="keep" bounds="[0,0][1080,1920]"><p>child</p></div>'
        result = simplify_structure(xml)
        root = ET.fromstring(result)
        assert root.tag == "div"
        assert root.attrib["text"] == "keep"


class TestRemoveEmptyBounds:
    def test_removes_zero_bounds(self):
        root = ET.fromstring('<div><p bounds="[0,0][0,0]">x</p><p bounds="[10,10][100,100]">y</p></div>')
        remove_nodes_with_empty_bounds(root)
        children = list(root)
        assert len(children) == 1
        assert children[0].attrib["bounds"] == "[10,10][100,100]"


class TestRemoveRedundancies:
    def test_deduplicates_scroll_children(self):
        # scroll must be a descendant (not root) for findall(".//scroll") to find it
        xml = '<div><scroll><div id="a" /><div id="a" /><div id="b" /></scroll></div>'
        result = remove_redundancies(xml)
        root = ET.fromstring(result)
        scroll = root.find(".//scroll")
        children = list(scroll)
        assert len(children) == 2  # one "a" removed, "b" kept


class TestPipeline:
    def test_parse_to_html_xml(self, simple_xml):
        result = parse_to_html_xml(simple_xml)
        assert result != ""
        root = ET.fromstring(result)
        assert root is not None

    def test_parse_to_html_xml_empty(self):
        assert parse_to_html_xml("") == ""

    def test_parse_to_html_xml_invalid(self):
        assert parse_to_html_xml("<bad xml!!!") == ""

    def test_encode_strips_attrs(self, simple_xml):
        result = encode_to_html_xml(simple_xml)
        assert result != ""
        root = ET.fromstring(result)
        for elem in root.iter():
            assert "bounds" not in elem.attrib
            assert "important" not in elem.attrib
            assert "class" not in elem.attrib

    def test_encode_empty(self):
        assert encode_to_html_xml("") == ""


class TestIndentXml:
    def test_indent(self):
        xml = '<div><p>Hello</p></div>'
        result = indent_xml(xml)
        assert "\n" in result
        assert "  " in result

    def test_indent_invalid(self):
        bad = "<not valid!!!"
        assert indent_xml(bad) == bad
