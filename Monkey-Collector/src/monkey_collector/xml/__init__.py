"""XML adapters: UI tree and structured parser."""

from monkey_collector.xml.parser_base import Parser
from monkey_collector.xml.structured_parser import StructuredXmlParser
from monkey_collector.xml.ui_tree import (
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
