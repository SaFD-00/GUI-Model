"""Main collection loop: receive UI states from App, select actions, execute via ADB."""

import time
import uuid
from typing import Optional

from loguru import logger

from server.adb import AdbClient
from server.xml_parser import UITree
from server.explorer import SmartExplorer
from server.server import CollectionServer
from server.storage import DataWriter


class Collector:
    """Orchestrates data collection using App+Server architecture.

    Flow:
      1. Start TCP server and wait for client connection
      2. Receive target package name from client (P message)
      3. Loop:
         a. App detects screen change → sends screenshot + XML via TCP
         b. Server receives → parses XML → selects action
         c. Server executes action via ADB
         d. Saves screenshot/XML/event to disk
      4. Finalize session
    """

    def __init__(
        self,
        adb: AdbClient,
        explorer: SmartExplorer,
        server: CollectionServer,
        writer: DataWriter,
        max_steps: int = 100,
        action_delay: float = 1.0,
        xml_timeout: float = 15.0,
    ):
        self.adb = adb
        self.explorer = explorer
        self.server = server
        self.writer = writer
        self.max_steps = max_steps
        self.action_delay = action_delay
        self.xml_timeout = xml_timeout
        self._latest_screenshot: bytes | None = None

    def run(self, package: Optional[str] = None) -> str:
        """Run a single collection session.

        Args:
            package: Target app package. If None, received from client.

        Returns:
            session_id string.
        """
        # Setup callbacks
        self.server.on_screenshot = self._on_screenshot

        # Start TCP server
        self.server.start()

        # 1. Wait for client connection
        logger.info("Waiting for device to connect (press floating ▶ button)...")
        connected = False
        for _ in range(120):
            time.sleep(1)
            if self.server.is_client_connected():
                connected = True
                break
        if not connected:
            logger.error("Device did not connect within 120 seconds")
            self.server.stop()
            return ""

        logger.info("Device connected")

        # 2. Receive target package from client (if not specified)
        if package is None:
            logger.info("Waiting for target package from client...")
            package = self.server.wait_for_package(timeout=30.0)
            if package is None:
                logger.error("No package received from client")
                self.server.stop()
                return ""
        else:
            # Still consume the P message if client sends one
            received = self.server.wait_for_package(timeout=5.0)
            if received:
                package = received

        logger.info(f"Target package: {package}")

        # 3. Initialize session
        session_id = f"{package}_{uuid.uuid4().hex[:8]}"
        self.server.on_external_app = lambda payload: self.writer.log_external_app(payload)
        self.writer.init_session(session_id, package)

        logger.info(f"Starting session: {session_id}")
        logger.info(f"Target app: {package}, max_steps: {self.max_steps}")

        # 4. Collection loop
        total_actions = 0
        timeout_count = 0
        max_timeouts = 5

        for step in range(self.max_steps):
            try:
                # Wait for XML from App (App sends after transition detected)
                result = self.server.wait_for_xml(timeout=self.xml_timeout)
                if result is None:
                    timeout_count += 1
                    logger.warning(
                        f"Step {step}: XML timeout ({timeout_count}/{max_timeouts})"
                    )
                    if timeout_count >= max_timeouts:
                        logger.error("Too many timeouts, ending session")
                        break
                    # Tap screen center to trigger a new accessibility event
                    try:
                        w, h = self.adb.get_device_resolution()
                        self.adb.tap(w // 2, h // 2)
                    except Exception:
                        pass
                    continue

                timeout_count = 0
                xml_str, meta = result
                top_package = meta.get("top_package", "")
                target_package = meta.get("target_package", package)

                # Check for app escape
                if top_package and top_package != package:
                    logger.warning(f"Step {step}: external app {top_package}, recovering")
                    self.explorer.return_to_app(package)
                    continue

                # Save received data
                if self._latest_screenshot:
                    self.writer.save_screenshot(self._latest_screenshot)
                    self._latest_screenshot = None
                self.writer.save_xml(xml_str)

                # Parse UI tree
                ui_tree = UITree.from_xml_string(xml_str)
                if len(ui_tree) == 0:
                    logger.warning(f"Step {step}: no UI elements, pressing back")
                    self.adb.press_back()
                    continue

                # Select action
                action = self.explorer.select_action(ui_tree)
                logger.info(
                    f"Step {step}: {action.action_type} "
                    f"(element_index={action.element_index})"
                )

                # Execute action via ADB
                self.explorer.execute_action(action)
                total_actions += 1

                # Log event
                event = action.to_dict()
                event["step"] = step
                self.writer.log_event(event)

                # Wait before next action
                time.sleep(self.action_delay)

            except Exception as e:
                logger.error(f"Step {step}: error - {e}")
                try:
                    self.explorer.recover(package)
                except Exception:
                    pass

        # Finalize
        self.writer.finalize_session()
        self.server.stop()

        logger.info(
            f"Session complete: {session_id} | "
            f"steps={self.writer.step_count}, actions={total_actions}"
        )
        return session_id

    def _on_screenshot(self, image_data: bytes):
        """Callback: store latest screenshot for saving with next XML."""
        self._latest_screenshot = image_data
