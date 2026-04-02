"""Main collection loop: receive UI states from App, select actions, execute via ADB."""

import time
from datetime import datetime

from loguru import logger

from server.actions import Action
from server.adb import AdbClient
from server.explorer import SmartExplorer
from server.server import CollectionServer
from server.storage import DataWriter
from server.xml_parser import UITree

MAX_NO_CHANGE_RETRIES = 3
MAX_EXTERNAL_APP_RETRIES = 10


class Collector:
    """Orchestrates data collection using App+Server architecture.

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

    def run(self, package: str | None = None) -> str:
        """Run a single collection session.

        Args:
            package: Target app package. If None, received from client.

        Returns:
            session_id string.
        """
        self.server.on_screenshot = self._on_screenshot
        self.server.start()
        try:
            session_id = self._run_session(package)
        finally:
            self.server.stop()
        return session_id

    def run_multi(self, package: str | None = None) -> list[str]:
        """Run multiple collection sessions without restarting server.

        The server stays alive between sessions. Each session runs until
        the app sends F (stop button) or max_steps is reached.
        Press Ctrl+C to stop the server.

        Args:
            package: Target app package. If None, received from client each session.

        Returns:
            List of session_id strings.
        """
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
        """Run a single collection session (server must already be started).

        Args:
            package: Target app package. If None, received from client.

        Returns:
            session_id string, or empty string on failure.
        """
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
            return ""

        logger.info("Device connected")

        # 2. Receive target package from client (if not specified)
        if package is None:
            logger.info("Waiting for target package from client...")
            pkg = self.server.wait_for_package(timeout=30.0)
            if pkg is None:
                logger.error("No package received from client")
                return ""
            package = pkg
        else:
            # Still consume the P message if client sends one
            received = self.server.wait_for_package(timeout=5.0)
            if received:
                package = received

        logger.info(f"Target package: {package}")

        # 3. Initialize session
        session_id = f"{package}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
        self.server.on_external_app = lambda payload: self.writer.log_external_app(payload)
        self.writer.init_session(session_id, package)

        logger.info(f"Starting session: {session_id}")
        logger.info(f"Target app: {package}, max_steps: {self.max_steps}")

        # 4. Collection loop
        step = 0
        total_actions = 0
        timeout_count = 0
        max_timeouts = 5
        no_change_retries = 0
        external_app_count = 0
        last_action: Action | None = None
        last_ui_tree: UITree | None = None
        last_raw_xml: str | None = None
        is_first_screen = False

        try:
            while step < self.max_steps:
                try:
                    # Wait for latest signal (skips stale queued signals)
                    result = self.server.get_latest_signal(
                        timeout=self.xml_timeout
                    )

                    if result is None:
                        # True timeout (no signal at all)
                        timeout_count += 1
                        logger.warning(
                            f"Step {step}: signal timeout "
                            f"({timeout_count}/{max_timeouts})"
                        )
                        if timeout_count >= max_timeouts:
                            logger.error("Too many timeouts, ending session")
                            break
                        try:
                            w, h = self.adb.get_device_resolution()
                            self.adb.tap(w // 2, h // 2)
                        except Exception:
                            pass
                        step += 1
                        continue

                    signal_type = result[0]

                    # Client sent finish or disconnected
                    if signal_type == "finish":
                        logger.info("Received finish signal, ending session")
                        break

                    if signal_type == "no_change":
                        # Screen did not change after last action
                        no_change_retries += 1
                        logger.info(
                            f"Step {step}: no visual change "
                            f"(retry {no_change_retries}/{MAX_NO_CHANGE_RETRIES})"
                        )

                        # Exclude the element that was just tried
                        if last_action is not None and last_action.element_index >= 0:
                            self.explorer.exclude_element(
                                last_action.element_index
                            )

                        if no_change_retries >= MAX_NO_CHANGE_RETRIES:
                            if is_first_screen:
                                logger.warning(
                                    f"Step {step}: {MAX_NO_CHANGE_RETRIES} "
                                    f"no-change retries, on first screen — tap instead of back"
                                )
                                self._tap_random_fallback()
                            else:
                                logger.warning(
                                    f"Step {step}: {MAX_NO_CHANGE_RETRIES} "
                                    f"no-change retries, pressing back"
                                )
                                self.adb.press_back()
                                time.sleep(0.5)
                                if self.explorer.has_left_app(package):
                                    logger.warning("press_back caused app exit, recovering")
                                    self.explorer.return_to_app(package)
                            self.server.clear_signal_queue()
                            no_change_retries = 0
                            self.explorer.clear_excluded()
                            last_action = None
                            last_ui_tree = None
                            time.sleep(self.action_delay)
                            step += 1
                            continue

                        # Try a different element on the same screen
                        if last_ui_tree is not None and len(last_ui_tree) > 0:
                            if last_raw_xml:
                                self.explorer.set_raw_xml(last_raw_xml)
                            action = self.explorer.select_action(last_ui_tree, step, is_first_screen=is_first_screen)
                            logger.info(
                                f"Step {step}: retry {action.action_type} "
                                f"(element_index={action.element_index})"
                            )
                            self.explorer.execute_action(action)
                            last_action = action
                            total_actions += 1

                            # Log retry event (no screenshot/xml saved)
                            event = action.to_dict()
                            event["step"] = step
                            event["no_change_retry"] = True
                            self.writer.log_event(event)

                            time.sleep(self.action_delay)
                        else:
                            if is_first_screen:
                                logger.info(f"Step {step}: no UI tree, on first screen — tap instead of back")
                                self._tap_random_fallback()
                            else:
                                self.adb.press_back()
                                time.sleep(0.5)
                                if self.explorer.has_left_app(package):
                                    logger.warning("press_back caused app exit, recovering")
                                    self.explorer.return_to_app(package)
                            no_change_retries = 0
                            self.explorer.clear_excluded()
                            last_action = None
                            time.sleep(self.action_delay)
                        step += 1
                        continue

                    if signal_type == "external_app":
                        external_app_count += 1
                        logger.warning(
                            f"Step {step}: external app detected "
                            f"({external_app_count}/{MAX_EXTERNAL_APP_RETRIES})"
                        )
                        if external_app_count >= MAX_EXTERNAL_APP_RETRIES:
                            logger.error("Too many external app detections, ending session")
                            break
                        try:
                            if external_app_count <= 3:
                                self.explorer.return_to_app(package)
                            else:
                                self.explorer.recover(package)
                        except Exception as e:
                            logger.error(f"Recovery attempt failed: {e}")
                        self.server.clear_signal_queue()
                        time.sleep(self.action_delay)
                        continue

                    # signal_type == "xml" — screen changed
                    timeout_count = 0
                    no_change_retries = 0
                    external_app_count = 0
                    self.explorer.clear_excluded()

                    _, xml_str, meta = result
                    top_package = meta.get("top_package", "")
                    target_package = meta.get("target_package", package)
                    is_first_screen = meta.get("is_first_screen", False)

                    # Check for app escape (client handles recovery via back press)
                    if top_package and top_package != package:
                        logger.info(
                            f"Step {step}: stale XML from {top_package} "
                            f"(expected {package}), skipping"
                        )
                        step += 1
                        continue

                    # Save received data
                    if self._latest_screenshot:
                        self.writer.save_screenshot(self._latest_screenshot)
                        self._latest_screenshot = None
                    self.writer.save_xml(xml_str)

                    # Parse UI tree
                    ui_tree = UITree.from_xml_string(xml_str)
                    if len(ui_tree) == 0:
                        if is_first_screen:
                            logger.warning(
                                f"Step {step}: no UI elements, on first screen — tap instead of back"
                            )
                            self._tap_random_fallback()
                        else:
                            logger.warning(
                                f"Step {step}: no UI elements, pressing back"
                            )
                            self.adb.press_back()
                            time.sleep(0.5)
                            if self.explorer.has_left_app(package):
                                logger.warning("press_back caused app exit, recovering")
                                self.explorer.return_to_app(package)
                        last_ui_tree = None
                        last_action = None
                        step += 1
                        continue

                    # Select action
                    self.explorer.set_raw_xml(xml_str)
                    action = self.explorer.select_action(ui_tree, step, is_first_screen=is_first_screen)
                    logger.info(
                        f"Step {step}: {action.action_type} "
                        f"(element_index={action.element_index})"
                    )

                    # Execute action via ADB
                    self.explorer.execute_action(action)
                    total_actions += 1

                    # Clear stale signals accumulated during action execution
                    self.server.clear_signal_queue()

                    # Track for potential retry
                    last_action = action
                    last_ui_tree = ui_tree
                    last_raw_xml = xml_str

                    # Log event
                    event = action.to_dict()
                    event["step"] = step
                    self.writer.log_event(event)

                    # Wait before next action
                    time.sleep(self.action_delay)
                    step += 1

                except Exception as e:
                    logger.error(f"Step {step}: error - {e}")
                    try:
                        self.explorer.recover(package)
                    except Exception:
                        pass
                    step += 1
        finally:
            # Finalize session even if interrupted
            self.writer.finalize_session()

        logger.info(
            f"Session complete: {session_id} | "
            f"steps={self.writer.step_count}, actions={total_actions}"
        )
        return session_id

    def _tap_random_fallback(self):
        """Tap center of screen as a fallback when back is suppressed on first screen."""
        try:
            w, h = self.adb.get_device_resolution()
            self.adb.tap(w // 2, h // 2)
        except Exception:
            pass

    def _on_screenshot(self, image_data: bytes):
        """Callback: store latest screenshot for saving with next XML."""
        self._latest_screenshot = image_data
