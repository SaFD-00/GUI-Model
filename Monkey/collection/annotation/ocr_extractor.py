"""Generate OCR annotations: text elements -> text recognition QA.

Produces QA pairs where the user asks what text is displayed at a
given bounding box and the assistant responds with the actual text
from the UI element.
"""

from loguru import logger

from collection.annotation.xml_parser import UIElement
from collection.annotation.grounding import normalize_bounds


def generate(
    elements: list[UIElement],
    resolution: tuple[int, int] = (1080, 2400),
    min_text_length: int = 1,
) -> list[dict]:
    """Generate OCR annotations from text-bearing UI elements.

    Args:
        elements: Parsed UI elements from XML.
        resolution: Screen resolution (width, height) for normalization.
        min_text_length: Minimum text length to include an element.

    Returns:
        List of annotation dicts with conversations and task_type.
    """
    results: list[dict] = []
    for elem in elements:
        text = elem.text.strip()
        if len(text) < min_text_length:
            continue
        if elem.area <= 0:
            continue

        bbox = normalize_bounds(elem.bounds, resolution)

        results.append({
            "conversations": [
                {
                    "role": "user",
                    "content": f"<image>\nWhat text is displayed at {bbox}?",
                },
                {"role": "assistant", "content": text},
            ],
            "task_type": "ocr",
        })

    logger.debug(f"Generated {len(results)} OCR annotations")
    return results
