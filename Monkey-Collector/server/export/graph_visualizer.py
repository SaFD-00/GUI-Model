"""PyVis-based page map visualization.

Generates interactive HTML graphs from page_graph.json, following the
same visual style as MobileGPT-V2's subtask graph visualizer.

Usage:
    Called automatically after collection, or manually via CLI:
        monkey-collect page-map --session <dir>
"""

from __future__ import annotations

import json
import os
import webbrowser
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pyvis.network import Network

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------


def _activity_color(activity: str, saturation: int = 70, lightness: int = 65) -> str:
    """Deterministic HSL color based on activity name hash."""
    hue = hash(activity) % 360
    return f"hsl({hue}, {saturation}%, {lightness}%)"


def _edge_color(count: int, max_count: int) -> str:
    """Blue gradient: light (#90CAF9) for count=1, dark (#1565C0) for max."""
    if max_count <= 1:
        return "#42A5F5"
    ratio = min((count - 1) / (max_count - 1), 1.0)
    # Interpolate between light blue and dark blue
    r = int(144 + (21 - 144) * ratio)
    g = int(202 + (101 - 202) * ratio)
    b = int(249 + (192 - 249) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


def _short_activity(activity: str) -> str:
    """Extract short class name from activity (e.g., '.MainActivity' → 'MainActivity')."""
    if "/" in activity:
        activity = activity.split("/", 1)[1]
    if "." in activity:
        parts = activity.rsplit(".", 1)
        return parts[-1]
    return activity or "Unknown"


# ---------------------------------------------------------------------------
# Visualization builder
# ---------------------------------------------------------------------------


def build_page_map_visualization(
    graph_data: dict,
    title: str = "Page Map",
) -> Network:
    """Build a PyVis Network from page graph data.

    Args:
        graph_data: Dict with 'nodes' and 'edges' keys (page_graph.json format).
        title: HTML page title.

    Returns:
        pyvis.network.Network instance.
    """
    from pyvis.network import Network

    net = Network(
        height="800px",
        width="100%",
        directed=True,
        notebook=False,
        cdn_resources="remote",
    )

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    if not nodes:
        return net

    # Outgoing edge count per node
    outgoing: dict[int, int] = {}
    max_edge_count = 1
    for edge in edges:
        fp = edge["from_page"]
        outgoing[fp] = outgoing.get(fp, 0) + 1
        cnt = edge.get("count", 1)
        if cnt > max_edge_count:
            max_edge_count = cnt

    # Add nodes
    for nd in nodes:
        nid = nd["id"]
        activity = nd.get("activity", "")
        visit_count = nd.get("visit_count", 1)
        out_count = outgoing.get(nid, 0)
        size = min(20 + out_count * 5, 50)

        short = _short_activity(activity)
        label = f"Page {nid}\n{short}"

        tooltip = (
            f"<b>Page {nid}</b><br>"
            f"<b>Activity:</b> {activity}<br>"
            f"<b>Visits:</b> {visit_count}<br>"
            f"<b>Outgoing:</b> {out_count}"
        )

        net.add_node(
            nid,
            label=label,
            title=tooltip,
            size=size,
            color=_activity_color(activity),
            font={"size": 12, "multi": "html"},
        )

    # Add edges
    action_abbrev = {
        "tap": "tap",
        "swipe": "swipe",
        "press_back": "back",
        "input_text": "input",
        "long_press": "long",
        "press_home": "home",
    }

    for edge in edges:
        action_type = edge.get("action_type", "unknown")
        count = edge.get("count", 1)
        element_info = edge.get("element_info", "")

        edge_label = action_abbrev.get(action_type, action_type)
        width = min(1 + count, 5)
        color = _edge_color(count, max_edge_count)

        tooltip = (
            f"<b>Action:</b> {action_type}<br>"
            f"<b>Element:</b> {element_info}<br>"
            f"<b>Count:</b> {count}"
        )

        net.add_edge(
            edge["from_page"],
            edge["to_page"],
            label=edge_label,
            title=tooltip,
            color=color,
            width=width,
            arrows="to",
        )

    # Layout settings (same as MobileGPT-V2)
    if len(nodes) > 15:
        net.set_options(json.dumps({
            "layout": {
                "hierarchical": {
                    "enabled": True,
                    "direction": "UD",
                    "sortMethod": "directed",
                    "nodeSpacing": 200,
                    "levelSeparation": 200,
                }
            },
            "physics": {
                "hierarchicalRepulsion": {
                    "nodeDistance": 200,
                }
            },
            "edges": {
                "smooth": {"type": "cubicBezier"},
                "font": {"size": 10, "align": "top"},
            },
        }))
    else:
        net.set_options(json.dumps({
            "physics": {
                "forceAtlas2Based": {
                    "gravitationalConstant": -100,
                    "centralGravity": 0.01,
                    "springLength": 200,
                    "springConstant": 0.02,
                },
                "solver": "forceAtlas2Based",
                "stabilization": {"iterations": 150},
            },
            "edges": {
                "smooth": {"type": "curvedCW", "roundness": 0.2},
                "font": {"size": 10, "align": "top"},
            },
        }))

    return net


# ---------------------------------------------------------------------------
# Session-level entry point
# ---------------------------------------------------------------------------


def visualize_session(
    session_dir: str,
    output_path: str | None = None,
    open_browser: bool = True,
) -> str:
    """Generate interactive HTML visualization for a session.

    Loads page_graph.json from session_dir and produces an HTML file.

    Args:
        session_dir: Path to session directory containing page_graph.json.
        output_path: Custom output path. Default: session_dir/page_graph.html.
        open_browser: Whether to open the result in a browser.

    Returns:
        Path to the generated HTML file.
    """
    graph_path = os.path.join(session_dir, "page_graph.json")
    if not os.path.exists(graph_path):
        logger.warning(f"No page_graph.json in {session_dir}")
        return ""

    with open(graph_path, encoding="utf-8") as f:
        graph_data = json.load(f)

    node_count = len(graph_data.get("nodes", []))
    edge_count = len(graph_data.get("edges", []))

    # Determine title from metadata
    metadata = graph_data.get("metadata", {})
    session_id = metadata.get("session_id", os.path.basename(session_dir))
    title = f"Page Map — {session_id}"

    net = build_page_map_visualization(graph_data, title=title)

    if output_path is None:
        output_path = os.path.join(session_dir, "page_graph.html")

    net.save_graph(output_path)

    # Inject tooltip CSS + MutationObserver (same as MobileGPT-V2)
    with open(output_path, encoding="utf-8") as f:
        html = f.read()

    custom_head = (
        "<style>\n"
        ".vis-tooltip {\n"
        "    max-width: 400px;\n"
        "}\n"
        "</style>\n"
    )
    custom_script = (
        "<script>\n"
        "new MutationObserver(function() {\n"
        "    var tip = document.querySelector('.vis-tooltip');\n"
        "    if (tip && tip.innerText.indexOf('<b>') !== -1) {\n"
        "        tip.innerHTML = tip.innerText;\n"
        "    }\n"
        "}).observe(document.body, { childList: true, subtree: true, characterData: true });\n"
        "</script>\n"
    )
    html = html.replace("</head>", custom_head + "</head>")
    html = html.replace("</body>", custom_script + "</body>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(
        f"Page map visualization: {node_count} pages, {edge_count} transitions "
        f"→ {output_path}"
    )

    if open_browser:
        webbrowser.open(f"file://{os.path.abspath(output_path)}")

    return output_path
