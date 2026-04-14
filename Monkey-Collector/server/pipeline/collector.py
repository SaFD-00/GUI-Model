"""Collector facade: wire TCP server, explorer, storage, and drive sessions.

Flow:
  1. Start TCP server and wait for client connection
  2. Receive target package name from client (P message)
  3. Loop:
     a. App detects screen change → sends screenshot + XML via TCP
        (or sends N signal if no visual change)
     b. Server receives → parses XML → selects action
     c. Server executes action via ADB
     d. Saves screenshot/XML/event to disk (skipped on no-change)
  4. Finalize session
"""

from __future__ import annotations

from loguru import logger

from server.domain.activity_coverage import ActivityCoverageTracker
from server.domain.cost_tracker import CostTracker
from server.infra.device.adb import AdbClient
from server.infra.network.server import CollectionServer
from server.infra.storage.storage import DataWriter
from server.pipeline.collection_loop import CollectionState, run_collection_loop
from server.pipeline.explorer import SmartExplorer
from server.pipeline.session_manager import (
    finalize_session,
    init_or_resume_session,
    receive_target_package,
    wait_for_connection,
)
from server.pipeline.text_generator import TextGenerator


class Collector:
    """Orchestrates data collection using App+Server architecture."""

    def __init__(
        self,
        adb: AdbClient,
        explorer: SmartExplorer,
        server: CollectionServer,
        writer: DataWriter,
        max_steps: int = 100,
        action_delay: float = 1.0,
        xml_timeout: float = 25.0,
        activity_coverage_tracker: ActivityCoverageTracker | None = None,
        cost_tracker: CostTracker | None = None,
        text_generator: TextGenerator | None = None,
        new_session: bool = False,
    ):
        self.adb = adb
        self.explorer = explorer
        self.server = server
        self.writer = writer
        self.max_steps = max_steps
        self.action_delay = action_delay
        self.xml_timeout = xml_timeout
        self._latest_screenshot: bytes | None = None
        self._activity_tracker = activity_coverage_tracker
        self._cost_tracker = cost_tracker
        self._text_generator = text_generator
        self._new_session = new_session

    def run(self, package: str | None = None) -> str:
        """Run a single collection session."""
        self.server.on_screenshot = self._on_screenshot
        self.server.start()
        try:
            session_id = self._run_session(package)
        finally:
            self.server.stop()
        return session_id

    def run_multi(self, package: str | None = None) -> list[str]:
        """Run multiple collection sessions without restarting server."""
        session_ids: list[str] = []
        self.server.on_screenshot = self._on_screenshot
        self.server.start()

        try:
            session_num = 0
            while True:
                session_num += 1
                logger.info(
                    f"=== Waiting for session #{session_num} "
                    f"(Ctrl+C to quit) ==="
                )

                if session_num > 1:
                    self.server.reset_for_new_session()
                self.explorer.clear_excluded()
                self._latest_screenshot = None

                try:
                    session_id = self._run_session(package)
                except KeyboardInterrupt:
                    logger.info("Interrupted during session")
                    break

                if session_id:
                    session_ids.append(session_id)
                    logger.info(
                        f"Session #{session_num} complete: {session_id}"
                    )
                else:
                    logger.warning(
                        f"Session #{session_num} ended without result"
                    )

                logger.info(f"Total sessions completed: {len(session_ids)}")
        except KeyboardInterrupt:
            logger.info("Shutting down server...")
        finally:
            self.server.stop()

        return session_ids

    def _run_session(self, package: str | None = None) -> str:
        """Run a single collection session (server must already be started)."""
        if not wait_for_connection(self):
            return ""

        pkg = receive_target_package(self, package)
        if pkg is None:
            return ""
        package = pkg
        logger.info(f"Target package: {package}")

        session_id, resume_step = init_or_resume_session(self, package)
        logger.info(f"Starting session: {session_id}")
        logger.info(f"Target app: {package}, max_steps: {self.max_steps}")

        state = CollectionState(
            step=resume_step,
            max_step=resume_step + self.max_steps,
        )

        try:
            run_collection_loop(self, state, package)
        finally:
            finalize_session(self, session_id)

        logger.info(
            f"Session complete: {session_id} | "
            f"steps={self.writer.step_count}, actions={state.total_actions}"
        )
        return session_id

    def _on_screenshot(self, image_data: bytes):
        """Callback: store latest screenshot for saving with next XML."""
        self._latest_screenshot = image_data
