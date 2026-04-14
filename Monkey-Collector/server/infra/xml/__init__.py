"""XML adapters: UI tree and structured parser."""

from server.infra.xml.parser import Parser, StructuredXmlParser
from server.infra.xml.ui_tree import (
    UIElement,
    UITree,
    parse_uiautomator_xml,
)

__all__ = [
    "UIElement",
    "UITree",
    "parse_uiautomator_xml",
    "Parser",
    "StructuredXmlParser",
]
