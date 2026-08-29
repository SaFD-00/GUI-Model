"""Vendored html-like structured XML parser — the EXP08 export dialect.

PROVENANCE
----------
Copied verbatim from
``.claude/references/parser/structured_xml_parser_v2_htmllike.py``.
FIVE deltas, enumerated below; every transform is byte-for-byte the original.
Nothing that computes a coordinate, a tag or an attribute was touched.

1. ``from base_parser import Parser`` -> ``from .parser_base import Parser``.
   The reference import was dead: no ``base_parser.py`` has ever existed in any
   sibling project (git history shows the module was only ever named
   ``parser_base.py``). The real ABC lives next door in ``parser_base.py``,
   copied from ``Monkey-Collector/src/monkey_collector/xml/parser_base.py``;
   ``StructuredXmlParser`` already satisfies it.
2. Pillow is now imported LAZILY. The module-scope
   ``from PIL import Image, ImageDraw, ImageFont`` is gone; ``ImageDraw`` and
   ``ImageFont`` are imported inside ``_add_mark`` (the only body that touches
   them, one added comment line above the import) and ``Image`` survives as a
   ``TYPE_CHECKING``-only annotation, which also added ``TYPE_CHECKING`` to the
   ``from typing import ...`` line and the ``if TYPE_CHECKING:`` block after the
   imports. ``parse()`` never touched PIL, so importing this module — the whole
   export path — no longer requires Pillow.
3. ``from __future__ import annotations`` added at the top. This is what MAKES
   delta 2 safe rather than a cosmetic extra: with postponed evaluation the
   ``screenshot: Image.Image`` annotations on ``_add_mark`` and ``SoM`` are never
   evaluated at runtime, so ``Image`` can stay ``TYPE_CHECKING``-only. Without it
   this module would import Pillow again the moment those defs are executed.
4. Line endings normalised CRLF -> LF (the reference file is CRLF). No source
   construct in this file depends on its own line endings, so this is an EOL
   convention change only.
5. This docstring.

Re-verify at any time — the only hunks should be the import header (deltas 1-3)
and the lazy ``ImageDraw``/``ImageFont`` import inside ``_add_mark`` (delta 2)::

    tr -d '\r' < <reference> > /tmp/ref.lf.py
    diff -u /tmp/ref.lf.py <this file with the docstring stripped>

COORD MODES
-----------
``parse()`` has four. Only ``coord_mode="resized"`` produces the EXP08 contract:
it calls ``smart_resize_dims(orig_height, orig_width, max_pixels)`` and then
applies NON-UNIFORM scaling (``x_scale = new_w / orig_w``,
``y_scale = new_h / orig_h``). For the target Pixel 6,
``smart_resize_dims(2400, 1080) == (1876, 840)``. The DEFAULT
``coord_mode="absolute"`` emits raw device pixels and is WRONG for export —
debugging only. ``"resized"`` and ``"normalized"`` raise ``ValueError`` when
``width``/``height`` are omitted. Note the axis order trap: ``smart_resize_dims``
takes ``(height, width)`` while ``parse`` takes ``(width, height)``; the package
wrappers in ``__init__.py`` pass everything by keyword.

THE FOREST / JSON DOOR (recorded, deliberately NOT implemented)
---------------------------------------------------------------
``.claude/references/parser/forest_to_structured.py`` is the JSON-forest variant:
it builds an XML tree from a serialized accessibility forest and then runs the
same stage-2 transform. That stage 2 is byte-identical to this file's except that
it (a) omits ``_normalize_ws`` and (b) HARDCODES the ``_renumber`` tail. It has no
``coord_mode`` parameter at all, so it emits ``bounds="[l,t][r,b]"`` plus ``index``
and CANNOT produce ``data-bbox`` as-is — it would need the ``_add_point_*`` /
``_resize_and_add_point`` tail grafted on. Atlas-Collector is HOST-PULL and reads
``uiautomator dump`` XML directly, so the forest path is not needed; this note
exists so a future device-side/forest source can be wired in without
re-deriving the difference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple
import math
import re
import xml.etree.ElementTree as ET

from .parser_base import Parser
from xml.dom import minidom

if TYPE_CHECKING:  # pragma: no cover - annotation-only; PIL stays lazy at runtime
    from PIL import Image


_WS_COLLAPSE = re.compile(r"[\r\n\t]+")


def _normalize_ws(root):
    """Collapse \\r \\n \\t runs inside attribute values and inner text to a single space.

    `el.tail` (whitespace between siblings for pretty-printing) is left untouched.
    """
    for el in root.iter():
        for k, v in list(el.attrib.items()):
            if isinstance(v, str) and _WS_COLLAPSE.search(v):
                el.attrib[k] = _WS_COLLAPSE.sub(" ", v)
        if el.text and _WS_COLLAPSE.search(el.text):
            stripped = el.text.strip()
            if stripped:
                el.text = _WS_COLLAPSE.sub(" ", el.text)


# Android widget class → HTML tag mapping. See forest_to_structured.py for
# rationale. Applied in _reformat when the class doesn't already fall into a
# higher-priority branch (EditText, checkable, clickable).
INTERACTIVE_MAP = {
    "SeekBar":   ("input", {"type": "range"}),
    "RatingBar": ("input", {"type": "range"}),
}

SCROLL_CLASSES = {
    "ScrollView", "HorizontalScrollView", "NestedScrollView",
    "RecyclerView", "SDRecyclerView", "ListView", "GridView",
    "ExpandableListView", "ViewPager", "ViewPager2",
    "StaggeredGridLayoutManager",
}

CONTAINER_CLASSES = {
    "FrameLayout", "LinearLayout", "RelativeLayout", "ViewGroup",
    "ConstraintLayout", "CoordinatorLayout",
    "LinearLayoutCompat", "AppBarLayout", "DrawerLayout",
    "CardView", "MaterialCardView", "Toolbar", "MaterialToolbar",
    "GridLayout", "TableLayout", "TableRow",
    "unknown",
}


_INTERACTIVE_PRIORITY = {"input": 3, "button": 2, "p": 1, "img": 1, "div": 0}


def _more_interactive_tag(parent_tag, child_tag):
    if _INTERACTIVE_PRIORITY.get(child_tag, 0) > _INTERACTIVE_PRIORITY.get(parent_tag, 0):
        return child_tag
    return parent_tag


def _collapse_same_bbox_wrappers(root):
    """Opt1: merge single-child parent/child pairs at the SAME bounds.

    Safety guards:
      - parent has `data-scroll="true"` → skip (scroll semantic must stay
        distinct from interactive tap targets)
      - parent+child are {button, input} in any order (different interactive
        semantics — likely intentional)
      - both have conflicting aria-labels → skip
    Preserves the MORE interactive tag when merging (button beats div, etc.).
    """
    def walk(elem):
        for c in list(elem):
            walk(c)
        if len(list(elem)) != 1:
            return
        child = elem[0]
        eb = elem.attrib.get("bounds")
        cb = child.attrib.get("bounds")
        if not eb or eb != cb:
            return
        if elem.attrib.get("data-scroll") == "true":
            return
        if child.attrib.get("data-scroll") == "true":
            return
        pa = elem.attrib.get("aria-label")
        ca = child.attrib.get("aria-label")
        if pa and ca and pa != ca:
            return
        if {elem.tag, child.tag} == {"button", "input"}:
            return

        winning_tag = _more_interactive_tag(elem.tag, child.tag)
        if winning_tag == child.tag and winning_tag != elem.tag:
            for k in ("type", "value", "checked", "alt", "role"):
                if k in child.attrib:
                    elem.attrib[k] = child.attrib[k]
            elem.tag = child.tag
        if not pa and ca:
            elem.attrib["aria-label"] = ca
        for k in ("role",):
            if k in child.attrib and k not in elem.attrib:
                elem.attrib[k] = child.attrib[k]
        if (elem.text is None or not elem.text.strip()) and child.text:
            elem.text = child.text
        grandkids = list(child)
        elem.remove(child)
        for gk in grandkids:
            elem.append(gk)

    walk(root)


def _promote_button_ptext(root):
    """Opt3: merge <button|input> containing exactly one leaf <p> with text.
      - button aria-label absent   → promote p.text to become button inner text
      - button aria-label == p.text → drop the duplicate <p> (aria-label suffices)
    """
    for parent in root.iter():
        if parent.tag not in ("button", "input"):
            continue
        kids = list(parent)
        if len(kids) != 1:
            continue
        c = kids[0]
        if c.tag != "p":
            continue
        if len(list(c)) != 0:
            continue
        if not (c.text and c.text.strip()):
            continue
        if parent.text and parent.text.strip():
            continue
        pa = parent.attrib.get("aria-label")
        if pa and pa.strip() == c.text.strip():
            parent.remove(c)
        else:
            parent.text = c.text
            ca = c.attrib.get("aria-label")
            if not pa and ca and ca.strip() != c.text.strip():
                parent.set("aria-label", ca)
            parent.remove(c)


def _collapse_and_promote(xml_string):
    """Post-cleanup pass: run Opt1 (same-bbox wrapper collapse) then
    Opt3 (button>p text promotion)."""
    root = ET.fromstring(xml_string)
    _collapse_same_bbox_wrappers(root)
    _promote_button_ptext(root)
    return ET.tostring(root, encoding="unicode")


# Qwen2-VL / Qwen2.5-VL image processor granularity: patch=14, merge=2 → 28-pixel factor.
IMAGE_FACTOR = 28


def smart_resize_dims(height: int, width: int, factor: int = IMAGE_FACTOR,
                      max_pixels: int = 1605632) -> Tuple[int, int]:
    """Compute Qwen-style resized dimensions.
    Returns (new_height, new_width), both multiples of `factor`, with h*w <= max_pixels.
    min_pixels bound is intentionally omitted (input resolution is always well above it).
    """
    h_bar = round(height / factor) * factor
    w_bar = round(width / factor) * factor
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = math.floor(height / beta / factor) * factor
        w_bar = math.floor(width / beta / factor) * factor
    return h_bar, w_bar



class StructuredXmlParser(Parser):
    def __init__(self):
        super().__init__('structured_xml')
        self.bounds_cache = {}
        self.views = None

    def pretty_xml(self, xml_str):
        dom = minidom.parseString(xml_str)
        pretty = dom.toprettyxml(indent="  ")
        # Strip the leading `<?xml version="1.0" ?>` declaration line that
        # minidom auto-adds. Downstream consumers (making_stage1/2, making_som,
        # trainers) don't need it, and stripping keeps the on-disk file identical
        # to what the JSONL builders would emit anyway.
        if pretty.startswith('<?xml'):
            _, _, pretty = pretty.partition('\n')
        return pretty.lstrip()

    def _reformat(self, xml_string):
        xml_string = xml_string.replace('$', '_')
        tree = ET.fromstring(xml_string)
        _normalize_ws(tree)

        def process_element(element):
            # NOTE: simplify()가 'text'/'description' 존재 여부를 기준으로 wrapper를 줄이고,
            #       'Button'/'Checker'는 예외 처리함.
            #       따라서 여기서는 tag와 key(text/description)를 유지해 index 안정성을 보장한다.
            attrib_text = {
                "text": "text",
                "placeholder": "placeholder",    # EditText placeholder (raw hintText)
                "description": "content-desc",   # keep key name 'description' for _simplify compatibility
                "important": "important",
                "class": "class",
            }

            attrib_bool = {
                "checkable": "checkable",
                "clickable": "clickable",
                "scrollable": "scrollable",
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

            # Append bool/int attrib
            new_text_attrib.update(new_bool_attrib)
            new_text_attrib.update(new_int_attrib)

            # Tag selection (KEEP existing tags for simplify compatibility)
            class_name = element.attrib.get("class", "node") or "node"
            class_name_short = class_name.split(".")[-1]

            if class_name_short == "EditText":
                new_element = ET.Element("input", new_text_attrib)
                # keep existing behavior: move leaf text attr into inner text (later _clean will convert to value)
                if len(element) == 0 and "text" in new_element.attrib:
                    text = new_element.attrib.get("text", "")
                    del new_element.attrib["text"]
                    new_element.text = text

            elif new_text_attrib.get("checkable", "") == "true":
                # Keep tag name 'Checker' here; _clean will convert it to <input type="checkbox">
                new_text_attrib["checked"] = element.attrib.get("checked", "false")
                del new_text_attrib["checkable"]
                # RadioButton is checkable too but has radio semantics (mutex within
                # its RadioGroup). Mark type here so _clean's Checker→input rename
                # keeps type=radio via setdefault.
                if class_name_short == "RadioButton":
                    new_text_attrib["type"] = "radio"
                new_element = ET.Element("Checker", new_text_attrib)

            elif new_text_attrib.get("clickable", "") == "true":
                # Keep tag name 'Button' here; _clean will convert it to <button>
                del new_text_attrib["clickable"]
                new_element = ET.Element("Button", new_text_attrib)

            elif class_name_short in INTERACTIVE_MAP:
                html_tag, extra = INTERACTIVE_MAP[class_name_short]
                attribs = dict(new_text_attrib)
                attribs.update(extra)
                new_element = ET.Element(html_tag, attribs)

            elif class_name_short in SCROLL_CLASSES:
                attribs = dict(new_text_attrib)
                attribs["data-scroll"] = "true"
                new_element = ET.Element("div", attribs)

            elif class_name_short in CONTAINER_CLASSES:
                new_element = ET.Element("div", new_text_attrib)

            elif class_name_short == "ImageView":
                # Keep tag name 'Image' here; _clean will convert it to <img> + alt
                new_element = ET.Element("Image", new_text_attrib)

            elif class_name_short == "TextView":
                # Keep tag name 'TextField' here; _clean will convert it to <p>
                new_element = ET.Element("TextField", new_text_attrib)
                if len(element) == 0 and "text" in new_element.attrib:
                    text = new_element.attrib.get("text", "")
                    del new_element.attrib["text"]
                    new_element.text = text

            elif new_bool_attrib.get("scrollable", "") == "true":
                # Keep tag name 'Scroll' here; _clean will convert it to <div data-scroll="true">
                new_element = ET.Element("Scroll", new_text_attrib)

            elif "text" in new_text_attrib:
                if len(element) == 0:
                    new_element = ET.Element("TextField", new_text_attrib)
                    text = new_element.attrib.get("text", "")
                    del new_element.attrib["text"]
                    new_element.text = text
                else:
                    new_element = ET.Element("div", new_text_attrib)

            else:
                new_element = ET.Element("div", new_text_attrib)

            for child in element:
                new_child = process_element(child)
                if new_child is not None:
                    new_element.append(new_child)

            # skip leaf node that is meaningless e.g., no text or description attribute
            # IMPORTANT: keep the original condition (depends on 'description' key)
            # 'input' is always meaningful (interactive widget); 'placeholder' alone also keeps a node.
            if (
                new_element.tag not in ['Button', 'Checker', 'input']
                and len(element) == 0
                and 'description' not in new_element.attrib
                and 'placeholder' not in new_element.attrib
                and not new_element.text
            ):
                return None

            return new_element

        new_tree = process_element(tree)
        return ET.tostring(new_tree, encoding='unicode')


    def _simplify(self, xml_string):
        tree = ET.ElementTree(ET.fromstring(xml_string))
        root = tree.getroot()

        def is_meaningless_leaf(elem):
            # A leaf node is meaningless if it's not a Button/Checker/input and has no text/description/placeholder
            return (len(list(elem)) == 0 and
                   elem.tag not in ['Button', 'Checker', 'input'] and
                   'description' not in elem.attrib and
                   'placeholder' not in elem.attrib and
                   not elem.text)

        def remove_meaningless_leaves(elem):
            # Process children first (bottom-up)
            for child in list(elem):
                remove_meaningless_leaves(child)
            
            # Remove meaningless children
            for child in list(elem):
                if is_meaningless_leaf(child):
                    elem.remove(child)
            
            return bool(list(elem))  # Return True if element still has children

        def simplify_wrappers(elem):
            changed = False
            # While the current element has only one child and no important attributes
            while (len(list(elem)) == 1 and 
                   all(x not in elem.attrib for x in ['text', 'description'])):
                child = elem[0]
                if elem.tag not in ['Button', 'Checker']:
                    elem.tag = child.tag
                    elem.attrib = child.attrib
                    elem.text = child.text
                    elem[:] = child[:]  # Replace elem's children with child's children
                    changed = True
                else:
                    break

            # Process all children
            for child in list(elem):
                if simplify_wrappers(child):
                    changed = True
                    
            return changed

        # Iteratively simplify until no more changes can be made
        while True:
            changed = False
            
            # Remove meaningless leaves
            remove_meaningless_leaves(root)
            
            # Simplify wrappers
            if simplify_wrappers(root):
                changed = True
                
            if not changed:
                break

        return ET.tostring(root, encoding='unicode')

    def _remove_nodes_with_empty_bounds(self,element):
        for node in list(element):
            if node.get('bounds') == "[0,0][0,0]":
                element.remove(node)
            else:
                self._remove_nodes_with_empty_bounds(node)

    def _clean(self, xml_string):
        root = ET.fromstring(xml_string)
        self._remove_nodes_with_empty_bounds(root)

        # --- 1) Tag rename to standard HTML (after _simplify has finished) ---
        # IMPORTANT: Doing this here avoids breaking _simplify() logic.
        for el in root.iter():
            if el.tag == "TextField":
                el.tag = "p"
            elif el.tag == "Scroll":
                el.tag = "div"
                # preserve scroll semantics in HTML-like way
                el.attrib["data-scroll"] = "true"
            elif el.tag == "Image":
                el.tag = "img"
            elif el.tag == "Button":
                el.tag = "button"
            elif el.tag == "Checker":
                el.tag = "input"
                el.attrib.setdefault("type", "checkbox")
            elif el.tag == "View":          # ✅ 이 줄(블록) 추가
                el.tag = "div"
    
        # --- 2) Normalize/rename attributes to be HTML-like ---
        for el in root.iter():
            # remove legacy aux attributes
            for k in ["important", "class", "scrollable", "long-clickable", "clickable", "checkable",
                    "focusable", "focused", "visible"]:
                if k in el.attrib:
                    del el.attrib[k]

            # content-desc was stored as 'description' in _reformat for simplify compatibility
            if "description" in el.attrib:
                # For <img>, prefer alt
                if el.tag == "img":
                    # if alt already exists, keep alt and drop description
                    if "alt" not in el.attrib and el.attrib["description"]:
                        el.attrib["alt"] = el.attrib["description"]
                    del el.attrib["description"]
                else:
                    # 일반 요소는 aria-label로
                    if "aria-label" not in el.attrib and el.attrib["description"]:
                        el.attrib["aria-label"] = el.attrib["description"]
                    del el.attrib["description"]

            # Normalize checked for checkbox
            if el.tag == "input" and el.attrib.get("type") == "checkbox":
                checked_val = el.attrib.get("checked")
                if checked_val is None:
                    pass
                else:
                    if str(checked_val).lower() == "true":
                        el.attrib["checked"] = "checked"   # presence-like
                    else:
                        # false or other -> remove
                        del el.attrib["checked"]

            # Normalize text/value/placeholder for input type=text
            if el.tag == "input" and el.attrib.get("type") != "checkbox":
                # ensure default type
                if "type" not in el.attrib:
                    el.attrib["type"] = "text"
                # if inner text exists, move it to value
                if (el.text is not None) and el.text.strip():
                    if "value" not in el.attrib:
                        el.attrib["value"] = el.text.strip()
                    el.text = None
                # if legacy text attribute exists, move to value
                if "text" in el.attrib and el.attrib["text"]:
                    if "value" not in el.attrib:
                        el.attrib["value"] = el.attrib["text"]
                    del el.attrib["text"]
                # If Android returned the hint through getText(), value == placeholder.
                # In that case the field is really empty; keep only placeholder.
                if ("value" in el.attrib and "placeholder" in el.attrib
                        and el.attrib["value"] == el.attrib["placeholder"]):
                    del el.attrib["value"]

            # Normalize button label: prefer inner text; drop text attr
            if el.tag == "button":
                if "text" in el.attrib and el.attrib["text"]:
                    if (el.text is None) or (not el.text.strip()):
                        el.text = el.attrib["text"]
                    del el.attrib["text"]

        # --- 3) Attribute whitelist (keep minimal + useful) ---
        allowed_common = {"index", "bounds"}

        allowed_by_tag = {
            "button": {"aria-label"},
            "p": {"aria-label"},
            "div": {"data-scroll", "aria-label"},
            "img": {"alt"},  # keep alt only for images
            "input": {"type", "value", "placeholder", "checked", "aria-label", "role"},
        }

        for el in root.iter():
            allowed = set(allowed_common)
            allowed |= allowed_by_tag.get(el.tag, set())

            # Drop everything not allowed (including stray `hint` on non-input tags)
            for k in list(el.attrib.keys()):
                if k not in allowed:
                    del el.attrib[k]

        return ET.tostring(root, encoding='unicode')

        

    def _renumber(self, xml_string):
        """
        Reassigns sequential index numbers to all elements in the XML tree.

        Args:
            xml_string: The XML string to process

        Returns:
            The XML string with renumbered indices
        """
        root = ET.fromstring(xml_string)
        current_index = 0

        # Traverse the tree in pre-order and assign new indices
        for element in root.iter():
            element.attrib['index'] = str(current_index)
            current_index += 1

        return ET.tostring(root, encoding='unicode')

    def _add_point_absolute(self, xml_string):
        """Emit pixel bbox as data-bbox="x1 y1 x2 y2" (Qwen-VL HTML style).
        Remove legacy bounds attribute and index. No point attribute is emitted.
        """
        root = ET.fromstring(xml_string)
        for element in root.iter():
            bounds_str = element.attrib.get('bounds', '[0,0][0,0]')
            parts = bounds_str.strip('[]').replace('][', ',').split(',')
            left, top, right, bottom = (
                int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            )
            element.attrib['data-bbox'] = f'{left} {top} {right} {bottom}'
            if 'bounds' in element.attrib:
                del element.attrib['bounds']
            if 'index' in element.attrib:
                del element.attrib['index']
        return ET.tostring(root, encoding='unicode')

    def _normalize_and_add_point(self, xml_string, width, height):
        """Emit normalized (0-1000) data-bbox="x1 y1 x2 y2". Remove legacy bounds & index."""
        root = ET.fromstring(xml_string)
        for element in root.iter():
            bounds_str = element.attrib.get('bounds', '[0,0][0,0]')
            parts = bounds_str.strip('[]').replace('][', ',').split(',')
            left, top, right, bottom = (
                int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            )
            nl = (left * 1000) // width
            nt = (top * 1000) // height
            nr = (right * 1000) // width
            nb = (bottom * 1000) // height
            element.attrib['data-bbox'] = f'{nl} {nt} {nr} {nb}'
            if 'bounds' in element.attrib:
                del element.attrib['bounds']
            if 'index' in element.attrib:
                del element.attrib['index']
        return ET.tostring(root, encoding='unicode')

    def _resize_and_add_point(self, xml_string, orig_width, orig_height, max_pixels):
        """Scale bbox into Qwen-resized pixel space (h*w <= max_pixels) and emit as
        data-bbox="x1 y1 x2 y2". Remove legacy bounds & index.

        Non-uniform x/y scaling reflects the 28-pixel rounding that Qwen's processor applies.
        """
        new_h, new_w = smart_resize_dims(orig_height, orig_width, max_pixels=max_pixels)
        x_scale = new_w / orig_width
        y_scale = new_h / orig_height
        root = ET.fromstring(xml_string)
        for element in root.iter():
            bounds_str = element.attrib.get('bounds', '[0,0][0,0]')
            parts = bounds_str.strip('[]').replace('][', ',').split(',')
            left, top, right, bottom = (
                int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            )
            rl = round(left * x_scale)
            rt = round(top * y_scale)
            rr = round(right * x_scale)
            rb = round(bottom * y_scale)
            element.attrib['data-bbox'] = f'{rl} {rt} {rr} {rb}'
            if 'bounds' in element.attrib:
                del element.attrib['bounds']
            if 'index' in element.attrib:
                del element.attrib['index']
        return ET.tostring(root, encoding='unicode')
    
    def _add_mark(self, screenshot: Image.Image, bounds_str: str, index: str):
        """
        Parse the 'bounds' attribute, draw bounding box & index on screenshot.
        """
        # Typical format of bounds_str is "[left,top][right,bottom]" 
        # e.g., "[0,12][144,98]"
        try:
            coords = bounds_str.replace('][', ',').strip('[]').split(',')
            left, top, right, bottom = list(map(int, coords))
        except:
            return  # If parsing fails, just skip

        # Lazy: importing this module must not require Pillow (parse() never uses it).
        from PIL import ImageDraw, ImageFont

        draw = ImageDraw.Draw(screenshot)
        
        # Draw bounding rectangle in green
        x0 = min(left, right)
        y0 = min(top, bottom)
        x1 = max(left, right)
        y1 = max(top, bottom)
        
        # Draw bounding rectangle in green
        # 정렬된 좌표(x0, y0, x1, y1)를 사용합니다.
        draw.rectangle([(x0, y0), (x1, y1)], outline=(0, 255, 0), width=2)

        # Add a small rectangle to hold the text index
        text_bg_size = (35, 25)
        
        # 텍스트 배경을 그릴 때도 정렬된 좌표를 기준으로 사용합니다.
        draw.rectangle(
            [(x0, y0), (x0 + text_bg_size[0], y0 + text_bg_size[1])],
            fill=(255, 255, 255)  # White background
        )
        
        # Try a bold font, else fallback
        # Change font size
        font = ImageFont.load_default().font_variant(size=20)  

        # Put index in the corner box
        draw.text((x0 + 1, y0 + 1), index, fill=(0, 0, 0), font=font)

    def _clear_bounds(self, xml_string):
        # Clear existing bounds cache
        self.bounds_cache.clear()


        root = ET.fromstring(xml_string)
        # Iterate through all elements
        for element in root.iter():
            # Get bounds and index if they exist
            bounds = element.get('bounds')
            index = element.get('index')
            
            # If both bounds and index exist, cache the bounds and remove from element
            if bounds and index:
                self.bounds_cache[int(index)] = bounds
                element.attrib.pop('bounds')

        # Convert back to string
        return ET.tostring(root, encoding='unicode')
    
    def parse(self, raw_xml, coord_mode="absolute", width=None, height=None,
              max_pixels=1605632) -> str:
        """Parse raw dump XML into HTML-like structured XML.

        coord_mode:
          - "absolute" (default): emit data-bbox in absolute pixels, remove `index`
          - "normalized"        : emit data-bbox normalized to 0-1000 (needs width, height), remove `index`
          - "resized"           : emit data-bbox scaled into Qwen resized pixel space
                                  (h*w <= max_pixels, 28-multiple dims), remove `index`
          - "index"             : legacy — keep pixel bounds and re-assign sequential `index`
        Output format: `<tag data-bbox="x1 y1 x2 y2" ...>`. `point` attribute is not emitted.
        """
        reformatted_xml = self._reformat(raw_xml)
        simplified_xml = self._simplify(reformatted_xml)
        cleaned_xml = self._clean(simplified_xml)
        collapsed_xml = _collapse_and_promote(cleaned_xml)

        if coord_mode == "absolute":
            final_xml = self._add_point_absolute(collapsed_xml)
        elif coord_mode == "normalized":
            if width is None or height is None:
                raise ValueError("width and height required for coord_mode='normalized'")
            final_xml = self._normalize_and_add_point(collapsed_xml, width, height)
        elif coord_mode == "resized":
            if width is None or height is None:
                raise ValueError("width and height required for coord_mode='resized'")
            final_xml = self._resize_and_add_point(collapsed_xml, width, height, max_pixels)
        elif coord_mode == "index":
            final_xml = self._renumber(collapsed_xml)
        else:
            raise ValueError(f"unknown coord_mode: {coord_mode}")

        self.views = final_xml
        return self.pretty_xml(final_xml)
    
    def SoM(self, screenshot: Image.Image, raw_xml: str) -> Tuple[Image.Image, str]:
        """
        1) Load screenshot.
        2) Parse raw XML to produce simplified HTML-like structure.
        3) Draw bounding boxes from the final parsed structure.
        4) Save annotated screenshot and return with final HTML string.
        """
        
        # 2. Parse raw XML -> final simplified HTML string
        final_xml_str = self.parse(raw_xml)
        #final_xml_str = raw_xml
        # 3. Draw bounding boxes (if 'bounds' is present) on the screenshot
        root = ET.fromstring(final_xml_str)
        for element in root.iter():
            bounds = element.get('bounds')
            index = element.get('index')
            if bounds and index:
                self._add_mark(screenshot, bounds, index)
        screenshot.save("annotated_screenshot.png")
        # 4. Return the screenshot and the final simplified HTML string
        return screenshot, final_xml_str

    def find_element_by_index(self, index: int) -> ET.Element:
        """
        Find UI element in XML tree by its index attribute.
        
        Args:
            index: The index value to search for
            
        Returns:
            Element if found, None otherwise
        """
        if self.views is None:
            return None
        
        # Parse the XML string into an ElementTree
        root = ET.fromstring(self.views)
        tree = ET.ElementTree(root)
        
        for element in tree.iter():
            if element.get('index') == str(index):
                print(element)
                return element
        return None
    
    def find_element_by_bounds(self, bounds: str) -> ET.Element:
        """
        Find the smallest UI element that contains the given bounds.
        
        Args:
            bounds: The bounds value to search for
            xml_string: The XML string to search in
            
        Returns:
            Element if found, None otherwise
        """
        if self.views is None or bounds is None:
            return None
            
        # Parse bounds into coordinates
        try:
            target_coords = bounds.replace('][', ',').strip('[]').split(',')
            tx1, ty1, tx2, ty2 = map(int, target_coords)
        except:
            return None
            
        # Find all elements that contain the target bounds
        matching_elements = []
        root = ET.fromstring(self.views)
        
        for index, element_bounds in self.bounds_cache.items():
            if element_bounds == bounds:
                return self.find_element_by_index(int(index))
            try:
                coords = element_bounds.replace('][', ',').strip('[]').split(',')
                x1, y1, x2, y2 = map(int, coords)
                
                # Check if this element fully contains the target bounds
                if x1 <= tx1 and y1 <= ty1 and x2 >= tx2 and y2 >= ty2:
                    element = self.find_element_by_index(int(index))
                    if element is not None:
                        matching_elements.append(element)
            except:
                continue
        
        if not matching_elements:
            return None
        
        # Return element with smallest area
        return min(matching_elements, 
                  key=lambda e: self._get_area(self.bounds_cache.get(e.get('index'))))
                  
    def get_bounds(self, index: int) -> str:
        print(self.bounds_cache)
        print(index)
        return self.bounds_cache.get(index)
    


