"""Convert uiautomator XML to HTML-style XML format.

Mapping rules:
- EditText -> <input>, with text as element.text
- checkable=true -> <checker>, with checked attribute
- clickable=true -> <button>
- FrameLayout/LinearLayout/RelativeLayout/ViewGroup/ConstraintLayout/unknown -> <div>
- ImageView -> <img>
- TextView -> <p>, with text as element.text
- scrollable=true -> <scroll>
- Others -> class short name

Encoded version: remove bounds, important, class attributes (for LLM consumption).
"""

import xml.etree.ElementTree as ET

from loguru import logger

_LAYOUT_CLASSES = frozenset({
    "FrameLayout",
    "LinearLayout",
    "RelativeLayout",
    "ViewGroup",
    "ConstraintLayout",
    "unknown",
})


def reformat_xml(xml_string: str) -> str:
    """Convert raw uiautomator XML to HTML-style element tree."""
    try:
        tree = ET.fromstring(xml_string)
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML for reformat: {e}")
        return ""

    def process_element(element: ET.Element) -> ET.Element | None:
        attrib_text = {
            "text": "text",
            "id": "resource-id",
            "description": "content-desc",
            "important": "important",
            "class": "class",
        }
        attrib_bool = {
            "checkable": "checkable",
            "clickable": "clickable",
            "data-scroll": "scrollable",
            "long-clickable": "long-clickable",
        }
        attrib_int = {
            "bounds": "bounds",
            "index": "index",
        }

        new_text_attrib = {
            key: element.attrib[value]
            for key, value in attrib_text.items()
            if value in element.attrib and element.attrib[value] != ""
        }
        new_bool_attrib = {
            key: element.attrib[value]
            for key, value in attrib_bool.items()
            if value in element.attrib and element.attrib[value] != "false"
        }
        new_int_attrib = {
            key: element.attrib[value]
            for key, value in attrib_int.items()
            if value in element.attrib
        }

        # Strip package prefix from resource-id
        if "id" in new_text_attrib:
            new_text_attrib["id"] = new_text_attrib["id"].split("/")[-1]

        new_text_attrib.update(new_bool_attrib)
        new_text_attrib.update(new_int_attrib)

        class_name = element.attrib.get("class", "unknown")
        class_short = class_name.split(".")[-1]

        if class_short == "EditText":
            new_element = ET.Element("input", new_text_attrib)
            if len(element) == 0 and "text" in new_element.attrib:
                new_element.text = new_element.attrib.pop("text")
        elif new_text_attrib.get("checkable") == "true":
            new_text_attrib["checked"] = element.attrib.get("checked", "false")
            del new_text_attrib["checkable"]
            new_element = ET.Element("checker", new_text_attrib)
        elif new_text_attrib.get("clickable") == "true":
            del new_text_attrib["clickable"]
            new_element = ET.Element("button", new_text_attrib)
        elif class_short in _LAYOUT_CLASSES:
            new_element = ET.Element("div", new_text_attrib)
        elif class_short == "ImageView":
            new_element = ET.Element("img", new_text_attrib)
        elif class_short == "TextView":
            new_element = ET.Element("p", new_text_attrib)
            if len(element) == 0 and "text" in new_element.attrib:
                new_element.text = new_element.attrib.pop("text")
        elif new_text_attrib.get("data-scroll") == "true":
            new_element = ET.Element("scroll", new_text_attrib)
        else:
            new_element = ET.Element(class_short, new_text_attrib)

        for child in element:
            new_child = process_element(child)
            if new_child is not None:
                new_element.append(new_child)

        # Prune empty leaf nodes (not buttons/checkers)
        if (
            new_element.tag not in ("button", "checker")
            and len(element) == 0
            and len(new_element.attrib) <= 4
        ):
            if not new_element.text:
                return None

        return new_element

    new_tree = process_element(tree)
    if new_tree is None:
        return ""
    return ET.tostring(new_tree, encoding="unicode")


def remove_nodes_with_empty_bounds(element: ET.Element) -> None:
    """Remove nodes with [0,0][0,0] bounds in-place."""
    for node in list(element):
        if node.get("bounds") == "[0,0][0,0]":
            element.remove(node)
        else:
            remove_nodes_with_empty_bounds(node)


def simplify_structure(xml_string: str) -> str:
    """Collapse single-child wrapper nodes."""
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML for simplification: {e}")
        return xml_string

    def simplify_element(elem: ET.Element) -> None:
        while (
            len(list(elem)) == 1
            and "text" not in elem.attrib
            and "description" not in elem.attrib
        ):
            if elem.tag in ("button", "checker"):
                break
            child = elem[0]
            elem.tag = child.tag
            elem.attrib = child.attrib
            elem.text = child.text
            elem[:] = child[:]

        for subelem in list(elem):
            simplify_element(subelem)

    simplify_element(root)
    return ET.tostring(root, encoding="unicode")


def remove_redundancies(xml_string: str) -> str:
    """Remove duplicate children within scroll containers."""
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML for redundancy removal: {e}")
        return xml_string

    def elem_key(elem: ET.Element) -> tuple:
        return (
            elem.tag,
            tuple(sorted(elem.attrib.items())),
            tuple(
                (child.tag, tuple(sorted(child.attrib.items())))
                for child in list(elem)
            ),
        )

    for scroll in root.findall(".//scroll"):
        seen: dict[tuple, ET.Element] = {}
        to_remove: list[ET.Element] = []
        for child in list(scroll):
            key = elem_key(child)
            if key in seen:
                to_remove.append(child)
            else:
                seen[key] = child
        for item in to_remove:
            scroll.remove(item)

    return ET.tostring(root, encoding="unicode")


def parse_to_html_xml(raw_xml: str) -> str:
    """Full parse pipeline: reformat -> simplify -> remove empty bounds."""
    reformatted = reformat_xml(raw_xml)
    if not reformatted:
        return ""

    simplified = simplify_structure(reformatted)

    try:
        root = ET.fromstring(simplified)
    except ET.ParseError as e:
        logger.error(f"Failed to parse simplified XML: {e}")
        return ""

    remove_nodes_with_empty_bounds(root)
    return ET.tostring(root, encoding="unicode")


def encode_to_html_xml(raw_xml: str) -> str:
    """Parse and encode: removes bounds, important, class attributes.

    Returns encoded HTML-style XML suitable for LLM consumption.
    """
    parsed = parse_to_html_xml(raw_xml)
    if not parsed:
        return ""

    try:
        tree = ET.fromstring(parsed)
    except ET.ParseError as e:
        logger.error(f"Failed to parse XML for encoding: {e}")
        return ""

    for element in tree.iter():
        for attr in ("bounds", "important", "class"):
            if attr in element.attrib:
                del element.attrib[attr]

    encoded = ET.tostring(tree, encoding="unicode")
    encoded = remove_redundancies(encoded)
    return encoded


def indent_xml(xml_string: str, indent: str = "  ") -> str:
    """Pretty-print XML with indentation."""
    try:
        root = ET.fromstring(xml_string)
        ET.indent(root, space=indent)
        return ET.tostring(root, encoding="unicode")
    except ET.ParseError:
        return xml_string
