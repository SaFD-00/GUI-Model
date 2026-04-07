"""Abstract base class for XML parsers."""

from __future__ import annotations

import abc
import xml.etree.ElementTree as ET


class Parser(abc.ABC):
    """Base interface that all parser implementations must follow."""

    def __init__(self, name: str = ""):
        self.name = name

    @abc.abstractmethod
    def parse(self, raw_xml: str) -> str:
        """Parse raw uiautomator XML and return transformed XML string."""

    @abc.abstractmethod
    def find_element_by_index(self, index: int) -> ET.Element | None:
        """Find element by its index attribute."""

    @abc.abstractmethod
    def find_element_by_bounds(self, bounds: str) -> ET.Element | None:
        """Find the smallest element that contains the given bounds."""

    @abc.abstractmethod
    def get_bounds(self, index: int) -> str | None:
        """Return cached bounds string for a given element index."""

    def _get_area(self, bounds: str) -> int:
        """Calculate pixel area from a bounds string ``[x1,y1][x2,y2]``."""
        if not bounds:
            return float("inf")  # type: ignore[return-value]
        coords = bounds.replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = map(int, coords)
        return (x2 - x1) * (y2 - y1)
