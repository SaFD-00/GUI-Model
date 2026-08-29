"""XML encoding for Monkey-Collector — raw ``uiautomator dump`` -> EXP08 html-like XML.

Ported from ``Atlas-Collector/src/atlas_collector/xml/`` verbatim (see
``structured_parser``'s docstring for the exact deltas that module carries
relative to the original reference). Nothing here was re-derived.

The transform itself lives, frozen and vendored, in :mod:`structured_parser`
(see that module's docstring for provenance and for the forest/JSON door).
This package exposes the small stable surface the collector and the exporter use;
callers should not reach for ``StructuredXmlParser.parse`` directly, because its
DEFAULT ``coord_mode="absolute"`` emits raw device pixels and is wrong for export.

Two coordinate spaces, never mix them:

- **export space** — :func:`parse_device_xml`. ``coord_mode="resized"``: bboxes are
  scaled into the Qwen-resized frame, ANISOTROPICALLY. For the target Pixel 6,
  1080x2400 device px -> an 840x1876 frame, x-scale 840/1080 = 0.7778 and
  y-scale 1876/2400 = 0.78167. A single scale factor is wrong. This is the EXP08
  contract: ``data-bbox="x1 y1 x2 y2"``, four space-separated ints, no ``bounds=``,
  no ``index=``.
- **device space** — :func:`parse_device_xml_absolute`. Raw device pixels, for
  debugging and for anything that has to tap the physical screen. NEVER export this.

Axis-order trap: :func:`smart_resize_dims` takes ``(height, width)`` while the
underlying ``parse`` takes ``(width, height)``. Both wrappers below pass every
dimension by keyword so the order cannot silently transpose; do the same at call
sites. A transposed call returns ``(840, 1876)`` instead of ``(1876, 840)``, which
still looks like a plausible answer.

GUARDS THE WRAPPER ADDS (the frozen transform has none of these)
---------------------------------------------------------------
1. **Frame cross-check.** The declared ``width``/``height`` are checked against the
   frame the dump itself was taken in — :func:`source_frame`, the bounds of the
   dump's largest node, normally the root ``[0,0][W,H]``. Passing a device size
   that disagrees with the dump used to be silent and produced a frame of the
   wrong size with every box plausibly inside it; it now raises ``ValueError``
   naming both frames. ``strict_frame=False`` restores the old silent behaviour
   for a caller that knowingly wants it.
2. **Typed empty-input error.** A well-formed but nodeless ``<hierarchy/>`` used to
   die inside ElementTree with ``AttributeError: 'NoneType' object has no
   attribute 'iter'``. Empty, whitespace-only and nodeless dumps now raise
   :class:`EmptyUiDumpError`; malformed XML raises ``ValueError`` naming the parse
   error.
3. **Degenerate-box warning.** A node whose ``bounds`` attribute is absent collapses
   to ``data-bbox="0 0 0 0"`` inside the frozen transform, in BOTH coord modes,
   with no signal. The wrapper counts zero-area boxes after parsing and logs a
   loguru WARNING with the count. It stays a warning: a real screen can legitimately
   contain a zero-area node. (A ``bounds`` value that is present but unparseable —
   ``bounds="garbage"`` — does NOT reach that default; it raises inside the frozen
   transform as a bare ``int()`` failure, which the wrapper re-raises chained with a
   message naming ``bounds``.)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable

from loguru import logger

from .parser_base import Parser
from .structured_parser import StructuredXmlParser, smart_resize_dims

#: Qwen2-VL / Qwen2.5-VL default visual-token budget. 1080x2400 under this budget
#: resolves to the 840x1876 frame the EXP08 records are written in.
DEFAULT_MAX_PIXELS = 1605632

__all__ = [
    "DEFAULT_MAX_PIXELS",
    "EmptyUiDumpError",
    "Parser",
    "StructuredXmlParser",
    "parse_device_xml",
    "parse_device_xml_absolute",
    "smart_resize_dims",
    "source_frame",
]


class EmptyUiDumpError(ValueError):
    """The dump carries no nodes to encode.

    Covers an empty string, a whitespace-only string, and a well-formed but
    childless ``<hierarchy/>``. A ``ValueError`` so that callers already guarding
    the parser's own ``ValueError`` keep working.
    """


#: uiautomator's bounds syntax: ``[left,top][right,bottom]``.
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_BBOX_RE = re.compile(r'data-bbox="([^"]*)"')


def _dump_root(raw_xml: str) -> ET.Element:
    """Parse a raw dump, raising a *readable* error for every degenerate input.

    Raises:
        EmptyUiDumpError: the input is empty, whitespace-only, or has no nodes.
        ValueError: the input is not well-formed XML (chained from ``ParseError``).
    """
    if not raw_xml.strip():
        raise EmptyUiDumpError(
            "uiautomator dump is empty (no XML at all), so it contained no nodes to encode"
        )
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise ValueError(f"malformed uiautomator XML dump, cannot be parsed as XML: {exc}") from exc
    if next(iter(root), None) is None:
        raise EmptyUiDumpError(
            f"uiautomator dump contained no nodes: root <{root.tag}> has no child elements"
        )
    return root


def _frame_from_root(root: ET.Element) -> tuple[int, int]:
    """``(width, height)`` of the largest ``bounds`` in the tree — the source frame.

    The uiautomator root node spans the whole screen (``[0,0][W,H]``), so the
    largest-area bounds IS the frame the dump was taken in. Deliberately not
    ``max(right), max(bottom)``: a scrolled child can report bounds past the display.
    """
    best_area = -1
    frame: tuple[int, int] | None = None
    for el in root.iter():
        raw_bounds = el.get("bounds")
        if not raw_bounds:
            continue
        match = _BOUNDS_RE.fullmatch(raw_bounds.strip())
        if match is None:
            continue
        left, top, right, bottom = (int(v) for v in match.groups())
        area = (right - left) * (bottom - top)
        if area > best_area:
            best_area = area
            frame = (right - left, bottom - top)
    if frame is None:
        raise ValueError(
            'could not derive the source frame: no node in the dump carries a parseable '
            'bounds="[left,top][right,bottom]"'
        )
    return frame


def source_frame(raw_xml: str) -> tuple[int, int]:
    """``(width, height)`` in device px of the frame the dump was taken in.

    Derived from the dump alone — never from a caller-declared size — so it can be
    used to cross-check ``wm size`` output against the screen actually dumped.
    """
    return _frame_from_root(_dump_root(raw_xml))


def _encode(call: Callable[[], str]) -> str:
    """Run the frozen transform, upgrading its bare ``int()`` blow-up into a message.

    A node whose ``bounds`` is present but unparseable (``bounds="garbage"``) reaches
    ``int()`` inside the vendored ``_add_point_*`` and dies as
    ``invalid literal for int() with base 10: 'garbage'`` — which names neither XML
    nor bounds. The transform stays untouched; only the message improves.
    """
    try:
        return call()
    except ValueError as exc:
        raise ValueError(
            "could not encode the uiautomator dump — a node very likely carries an "
            'unparseable bounds attribute (expected bounds="[left,top][right,bottom]"): '
            f"{exc}"
        ) from exc


def _warn_on_degenerate_boxes(encoded: str, *, mode: str) -> None:
    """WARN (never raise) when the encoded dump contains zero-area ``data-bbox`` boxes.

    A missing ``bounds`` attribute silently becomes ``data-bbox="0 0 0 0"`` in the
    frozen transform. Zero area is legal on a real screen, so this is a signal, not
    a failure.
    """
    boxes = _BBOX_RE.findall(encoded)
    degenerate = 0
    for raw in boxes:
        parts = raw.split()
        if len(parts) != 4:
            continue
        x1, y1, x2, y2 = (int(v) for v in parts)
        if x1 == x2 or y1 == y2:
            degenerate += 1
    if degenerate:
        logger.warning(
            f"{degenerate} of {len(boxes)} data-bbox boxes are degenerate (zero area) in the "
            f"{mode} encoding; a node with a missing or unparseable bounds attribute "
            'collapses to data-bbox="0 0 0 0"'
        )


def parse_device_xml(
    raw_xml: str,
    width: int,
    height: int,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    *,
    strict_frame: bool = True,
) -> str:
    """Encode a raw ``uiautomator dump`` into the EXP08 html-like dialect.

    THE export path. ``width``/``height`` are the DEVICE dimensions in pixels
    (e.g. 1080, 2400 — ``wm size`` order, width first); the resized frame is
    derived from them via :func:`smart_resize_dims`. Output carries
    ``data-bbox="x1 y1 x2 y2"`` in that resized frame and no ``bounds``/``index``.

    ``width``/``height`` are cross-checked against :func:`source_frame`, the frame
    the dump itself reports. They MUST agree: the scale factors are computed from
    the declared size, so a 1440x3120 declaration over a 1080x2400 dump used to
    emit an 840x1848 frame — every box ~25% too small, yet strictly inside the
    nominal bound, so containment checks stayed green. Pass ``strict_frame=False``
    to opt out explicitly and get that old silent behaviour back.

    ROTATION: ``wm size`` reports the PHYSICAL display (1080x2400) whatever the
    current rotation, but a landscape dump's root node is ``[0,0][2400,1080]``.
    Pass the rotated dimensions (or ``strict_frame=False``) for a landscape screen,
    or the cross-check will reject a perfectly good dump. Both fixtures in the
    suite are ``rotation="0"``, so nothing there exercises this.

    A fresh parser is built per call: ``StructuredXmlParser`` carries mutable
    ``bounds_cache``/``views`` state that must not leak between screens.

    Raises:
        EmptyUiDumpError: the dump is empty, whitespace-only, or has no nodes.
        ValueError: the dump is not well-formed XML, or (under ``strict_frame``)
            its own frame disagrees with ``width``/``height``.
    """
    root = _dump_root(raw_xml)
    if strict_frame:
        src_w, src_h = _frame_from_root(root)
        if (src_w, src_h) != (width, height):
            raise ValueError(
                f"frame mismatch: the dump was taken at {src_w}x{src_h} device px "
                f"(largest node bounds) but width/height declare {width}x{height}. "
                "Encoding anyway would scale every data-bbox by the wrong factor. "
                "Pass strict_frame=False to override."
            )
    encoded = _encode(
        lambda: StructuredXmlParser().parse(
            raw_xml,
            coord_mode="resized",
            width=width,
            height=height,
            max_pixels=max_pixels,
        )
    )
    _warn_on_degenerate_boxes(encoded, mode="resized")
    return encoded


def parse_device_xml_absolute(raw_xml: str) -> str:
    """Encode a raw dump with bboxes left in DEVICE pixels. Debugging only.

    Same tree, same tags, same attributes as :func:`parse_device_xml` — only the
    ``data-bbox`` numbers differ. Never write this into an export record.

    Takes no dimensions, so there is no frame to cross-check; the empty-input and
    degenerate-box guards apply here too.
    """
    _dump_root(raw_xml)
    encoded = _encode(lambda: StructuredXmlParser().parse(raw_xml, coord_mode="absolute"))
    _warn_on_degenerate_boxes(encoded, mode="absolute")
    return encoded
