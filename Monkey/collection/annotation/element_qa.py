"""Generate element QA annotations: element attributes -> QA pairs.

Produces QA pairs about individual UI element properties such as
type, clickability, enabled state, text content, and full descriptions.
Elements and templates are randomly sampled for diversity.
"""

import random
from typing import Callable

from loguru import logger

from collection.annotation.xml_parser import UIElement
from collection.annotation.grounding import normalize_bounds


def _describe(elem: UIElement) -> str:
    """Generate a natural language description of an element."""
    parts = [f"A {elem.short_class} element"]
    if elem.text:
        parts.append(f'with text "{elem.text}"')
    if elem.content_desc:
        parts.append(f'described as "{elem.content_desc}"')
    states: list[str] = []
    if elem.clickable:
        states.append("clickable")
    if elem.scrollable:
        states.append("scrollable")
    if elem.checkable:
        states.append(
            f"checkable ({'checked' if elem.checked else 'unchecked'})"
        )
    if not elem.enabled:
        states.append("disabled")
    if states:
        parts.append(f"that is {', '.join(states)}")
    return ". ".join(parts) + "."


TEMPLATES: list[tuple[str, Callable[[UIElement], str]]] = [
    ("What type of element is at {bbox}?", lambda e: e.short_class),
    (
        "Is the element at {bbox} clickable?",
        lambda e: "Yes" if e.clickable else "No",
    ),
    (
        "Is the element at {bbox} enabled?",
        lambda e: "Yes" if e.enabled else "No",
    ),
    (
        "What is the text of the element at {bbox}?",
        lambda e: e.text if e.text else "No text",
    ),
    ("Describe the element at {bbox}.", lambda e: _describe(e)),
]


def generate(
    elements: list[UIElement],
    resolution: tuple[int, int] = (1080, 2400),
    templates_per_screen: int = 5,
) -> list[dict]:
    """Generate element QA annotations.

    Samples interactive elements and applies random QA templates to
    produce diverse question-answer pairs about UI element attributes.

    Args:
        elements: Parsed UI elements from XML.
        resolution: Screen resolution (width, height) for normalization.
        templates_per_screen: Max number of QA pairs per screen.

    Returns:
        List of annotation dicts with conversations and task_type.
    """
    results: list[dict] = []
    interactive = [
        e for e in elements if e.clickable or e.text or e.content_desc
    ]

    if not interactive:
        return results

    # Sample elements and templates
    sampled = random.sample(
        interactive, min(templates_per_screen, len(interactive))
    )

    for elem in sampled:
        bbox = normalize_bounds(elem.bounds, resolution)
        template_q, template_a = random.choice(TEMPLATES)

        question = template_q.format(bbox=bbox)
        answer = template_a(elem)

        if not answer:
            continue

        results.append({
            "conversations": [
                {"role": "user", "content": f"<image>\n{question}"},
                {"role": "assistant", "content": answer},
            ],
            "task_type": "element_qa",
        })

    logger.debug(f"Generated {len(results)} element QA annotations")
    return results
