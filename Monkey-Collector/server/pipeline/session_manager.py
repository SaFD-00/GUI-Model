"""Session lifecycle: init/resume at start, finalize at end."""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

from loguru import logger

from server.domain.page_graph import build_graph_from_session

if TYPE_CHECKING:
    from server.pipeline.collector import Collector


def wait_for_connection(collector: Collector, timeout_seconds: int = 120) -> bool:
    """Block until a device connects or timeout."""
    import time

    logger.info("Waiting for device to connect (press floating ▶ button)...")
    for _ in range(timeout_seconds):
        time.sleep(1)
        if collector.server.is_client_connected():
            logger.info("Device connected")
            return True
    logger.error(f"Device did not connect within {timeout_seconds} seconds")
    return False


def receive_target_package(collector: Collector, package: str | None) -> str | None:
    """Receive or confirm the target package from the client."""
    if package is None:
        logger.info("Waiting for target package from client...")
        pkg = collector.server.wait_for_package(timeout=30.0)
        if pkg is None:
            logger.error("No package received from client")
            return None
        return pkg

    received = collector.server.wait_for_package(timeout=5.0)
    return received or package


def init_or_resume_session(
    collector: Collector,
    package: str,
) -> tuple[str, int]:
    """Initialize a new session or resume an existing one for the package.

    Returns (session_id, resume_step).
    """
    collector.server.on_external_app = (
        lambda payload: collector.writer.log_external_app(payload)
    )

    existing = (
        None if collector._new_session
        else collector.writer.find_existing_session(package)
    )

    if existing:
        session_id = existing
        resume_step = collector.writer.resume_session(session_id)
        total_activities = collector.adb.get_declared_activities(package)
        if collector._activity_tracker is not None:
            collector._activity_tracker.resume(
                collector.writer.session_dir, total_activities, package,
            )
        if collector._cost_tracker is not None:
            collector._cost_tracker.resume(collector.writer.session_dir)
        logger.info(f"Resuming session: {session_id} from step {resume_step}")
        return session_id, resume_step

    session_id = package
    if collector._new_session:
        existing_dir = os.path.join(collector.writer.base_dir, session_id)
        if os.path.isdir(existing_dir):
            shutil.rmtree(existing_dir)
            logger.info(f"Removed existing session directory: {existing_dir}")
    collector.writer.init_session(session_id, package)
    if collector._activity_tracker is not None:
        total_activities = collector.adb.get_declared_activities(package)
        collector._activity_tracker.initialize(
            collector.writer.session_dir, total_activities, package,
        )
    if collector._cost_tracker is not None:
        collector._cost_tracker.initialize(collector.writer.session_dir)
    return session_id, 0


def finalize_session(collector: Collector, session_id: str) -> None:
    """Finalize: notify app, save session, rebuild page graph, visualize."""
    collector.server.send_session_end()
    collector.writer.finalize_session()

    rebuilt_graph = build_graph_from_session(collector.writer.session_dir)
    if rebuilt_graph.nodes:
        graph_data = rebuilt_graph.to_dict()
        graph_data["metadata"]["session_id"] = session_id
        collector.writer.save_page_graph(graph_data)
        try:
            from server.export.graph_visualizer import visualize_session
            visualize_session(
                collector.writer.session_dir, open_browser=False,
            )
        except Exception as e:
            logger.warning(f"Page map visualization failed: {e}")
