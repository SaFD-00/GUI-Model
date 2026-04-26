"""XML adapters: UI tree and structured parser."""

from server.xml.parser_base import Parser
from server.xml.structured_parser import StructuredXmlParser
from server.xml.ui_tree import (
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
