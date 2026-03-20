"""Generate state diff annotations: compare consecutive XML dumps.

Produces QA pairs describing what changed between two consecutive
screen states by comparing element presence and attribute changes.
"""

from loguru import logger

from collection.annotation.xml_parser import UIElement


def _element_key(elem: UIElement) -> str:
    """Unique key for matching elements across states.

    Uses resource_id + class_name when available, falls back to
    bounds + class_name + index for anonymous elements.
    """
    if elem.resource_id:
        return f"{elem.resource_id}:{elem.class_name}"
    return f"{elem.bounds}:{elem.class_name}:{elem.index}"


def generate(
    before_elements: list[UIElement],
    after_elements: list[UIElement],
    resolution: tuple[int, int] = (1080, 2400),
    min_changes: int = 1,
) -> dict | None:
    """Generate state diff annotation between consecutive states.

    Compares two element lists to find added, removed, and modified
    elements, then produces a natural language description of changes.

    Args:
        before_elements: UI elements from the earlier state.
        after_elements: UI elements from the later state.
        resolution: Screen resolution (width, height).
        min_changes: Minimum number of changes to produce output.

    Returns:
        Annotation dict with conversations and task_type, or None
        if changes are below the threshold.
    """
    before_map = {_element_key(e): e for e in before_elements}
    after_map = {_element_key(e): e for e in after_elements}

    before_keys = set(before_map.keys())
    after_keys = set(after_map.keys())

    added = after_keys - before_keys
    removed = before_keys - after_keys
    common = before_keys & after_keys

    changed: list[tuple[UIElement, list[str]]] = []
    for key in common:
        b = before_map[key]
        a = after_map[key]
        diffs: list[str] = []
        if b.text != a.text:
            diffs.append(f"text changed from '{b.text}' to '{a.text}'")
        if b.checked != a.checked:
            diffs.append(
                f"checked changed from {b.checked} to {a.checked}"
            )
        if b.selected != a.selected:
            diffs.append(
                f"selected changed from {b.selected} to {a.selected}"
            )
        if b.enabled != a.enabled:
            diffs.append(
                f"enabled changed from {b.enabled} to {a.enabled}"
            )
        if diffs:
            changed.append((after_map[key], diffs))

    total_changes = len(added) + len(removed) + len(changed)
    if total_changes < min_changes:
        return None

    # Build description (limit to 5 per category for readability)
    parts: list[str] = []
    if added:
        for key in list(added)[:5]:
            elem = after_map[key]
            parts.append(
                f"New element appeared: {elem.display_name} ({elem.short_class})"
            )
    if removed:
        for key in list(removed)[:5]:
            elem = before_map[key]
            parts.append(
                f"Element removed: {elem.display_name} ({elem.short_class})"
            )
    if changed:
        for elem, diffs in changed[:5]:
            for d in diffs:
                parts.append(f"{elem.display_name}: {d}")

    diff_desc = "\n".join(parts)

    logger.debug(
        f"State diff: {len(added)} added, {len(removed)} removed, "
        f"{len(changed)} changed"
    )

    return {
        "conversations": [
            {
                "role": "user",
                "content": "<image>\nWhat changed on this screen?",
            },
            {"role": "assistant", "content": diff_desc},
        ],
        "task_type": "state_diff",
    }
