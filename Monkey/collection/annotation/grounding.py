"""Generate grounding annotations: element -> bbox + description.

Produces QA pairs where the user asks to locate a UI element by its
display name and the assistant responds with normalized bounding box
coordinates.
"""

from loguru import logger

from collection.annotation.xml_parser import UIElement


def normalize_bounds(
    bounds: tuple[int, int, int, int],
    resolution: tuple[int, int],
    scale: int = 1000,
) -> list[int]:
    """Normalize pixel bounds to [0, scale] coordinate range.

    Args:
        bounds: Pixel bounds (left, top, right, bottom).
        resolution: Screen resolution (width, height).
        scale: Target coordinate scale (default 1000).

    Returns:
        Normalized [left, top, right, bottom] as integers.
    """
    w, h = resolution
    if w == 0 or h == 0:
        return [0, 0, 0, 0]
    return [
        int(bounds[0] / w * scale),
        int(bounds[1] / h * scale),
        int(bounds[2] / w * scale),
        int(bounds[3] / h * scale),
    ]


def generate(
    elements: list[UIElement],
    resolution: tuple[int, int] = (1080, 2400),
    min_area: int = 100,
) -> list[dict]:
    """Generate grounding annotations from UI elements.

    Filters elements that are interactive or have visible text/description,
    normalizes their bounds, and creates QA conversation pairs.

    Args:
        elements: Parsed UI elements from XML.
        resolution: Screen resolution (width, height) for normalization.
        min_area: Minimum pixel area to include an element.

    Returns:
        List of annotation dicts with conversations and task_type.
    """
    results: list[dict] = []
    for elem in elements:
        if not (elem.clickable or elem.text or elem.content_desc):
            continue
        if elem.area < min_area:
            continue

        desc = elem.display_name
        bbox = normalize_bounds(elem.bounds, resolution)

        results.append({
            "conversations": [
                {
                    "role": "user",
                    "content": f"<image>\nFind the element: {desc}",
                },
                {"role": "assistant", "content": f"{bbox}"},
            ],
            "task_type": "grounding",
        })

    logger.debug(f"Generated {len(results)} grounding annotations")
    return results
