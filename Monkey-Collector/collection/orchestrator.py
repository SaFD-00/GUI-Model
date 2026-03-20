"""Collection orchestrator: coordinates Smart Explorer with data collection."""

import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .adb.client import AdbClient
from .explorer.smart_explorer import SmartExplorer
from .annotation.xml_parser import UITree
from .fallback.monitor import FallbackMonitor
from .server import CollectionServer
from .storage.writer import DataWriter

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    package: str
    name: str
    source: str = "system"
    apk: Optional[str] = None
    max_events: int = 100


@dataclass
class SessionResult:
    session_id: str
    app_package: str
    total_steps: int
    external_app_events: int
    success: bool
    error: Optional[str] = None


class CollectionOrchestrator:
    """Orchestrates the full data collection pipeline using Smart Explorer."""

    def __init__(self, config_path: str = "configs/collection/default.yaml"):
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        col_cfg = self.config["collection"]
        self.adb = AdbClient()
        self.writer = DataWriter(col_cfg["storage"]["output_dir"])
        self.server_cfg = col_cfg["server"]
        self.smart_explorer_cfg = col_cfg.get("smart_explorer", {})
        self.fallback_cfg = col_cfg.get("fallback", {})

    def run_session(self, app: AppConfig) -> SessionResult:
        """Run a single collection session for one app using Smart Explorer."""
        session_id = str(uuid.uuid4())[:8]
        logger.info(f"=== Session {session_id}: {app.name} ({app.package}) ===")

        monitor = FallbackMonitor(
            max_back_attempts=self.fallback_cfg.get("max_back_attempts", 3),
            allowed_packages=self.fallback_cfg.get("allowed_packages", []),
        )

        resolution = self.adb.get_device_resolution()
        self.writer.init_session(session_id, app.package, {
            "app_name": app.name,
            "resolution": list(resolution),
            "exploration_mode": "smart_explorer",
        })

        # Setup callbacks
        def on_screenshot(data: bytes):
            self.writer.save_screenshot(data)

        def on_xml(xml: str, top_pkg: str, target_pkg: str):
            if top_pkg != app.package and top_pkg not in monitor.allowed_packages:
                monitor.on_external_app(self.writer.step_count, {
                    "top": top_pkg, "target": target_pkg,
                })
                self.writer.log_external_app({"top": top_pkg, "target": target_pkg})
            else:
                monitor.on_valid_step()
            self.writer.save_xml(xml, top_pkg, target_pkg)

        def on_external_app(payload: dict):
            monitor.on_external_app(self.writer.step_count, payload)
            self.writer.log_external_app(payload)

        finished = [False]

        def on_finish():
            finished[0] = True

        # Start TCP server
        server = CollectionServer(
            host=self.server_cfg["host"],
            port=self.server_cfg["port"],
            on_screenshot=on_screenshot,
            on_xml=on_xml,
            on_external_app=on_external_app,
            on_finish=on_finish,
        )
        server.start()

        # Initialize Smart Explorer with screen resolution
        sm_config = dict(self.smart_explorer_cfg)
        sm_config["screen_width"] = resolution[0]
        sm_config["screen_height"] = resolution[1]
        smart_explorer = SmartExplorer(self.adb, sm_config)

        try:
            # 1. Launch target app
            self.adb.force_stop(app.package)
            time.sleep(0.5)
            self.adb.launch_app(app.package)
            time.sleep(2.0)

            # 2. Get initial XML (try TCP first, then ADB fallback)
            xml_str = server.wait_for_xml(timeout=5.0)
            if xml_str is None:
                logger.info("No initial XML from TCP, using ADB dump")
                xml_str = self.adb.dump_xml()

            # 3. Smart Explorer exploration loop
            for step in range(app.max_events):
                try:
                    # 3a. Parse UI tree
                    ui_tree = UITree.from_xml_string(xml_str)
                    if len(ui_tree) == 0:
                        logger.warning(f"Step {step}: Empty UI tree, using ADB dump")
                        xml_str = self.adb.dump_xml()
                        ui_tree = UITree.from_xml_string(xml_str)

                    # 3b. Select action
                    action = smart_explorer.select_action(ui_tree)

                    # 3c. Execute action via ADB
                    smart_explorer.execute_action(action)

                    # 3d. Log event (compatible with events.jsonl format)
                    event_data = action.to_dict()
                    event_data["type"] = action.action_type
                    event_data["step"] = step
                    self.writer.log_event(event_data)

                    logger.debug(
                        f"Step {step}: {action.action_type} "
                        f"(clickable={len(ui_tree.get_clickable_elements())}, "
                        f"editable={len(ui_tree.get_editable_elements())})"
                    )

                    # 3e. Wait for Android app to capture and send
                    time.sleep(smart_explorer.action_delay_ms / 1000.0)
                    new_xml = server.wait_for_xml(timeout=10.0)
                    if new_xml is not None:
                        xml_str = new_xml
                    else:
                        logger.debug(f"Step {step}: TCP timeout, using ADB dump")
                        xml_str = self.adb.dump_xml()

                    # 3f. Check for app escape
                    if smart_explorer.has_left_app(app.package):
                        logger.info(f"Step {step}: Left target app, returning")
                        if monitor.should_force_restart():
                            logger.warning(
                                f"Step {step}: {monitor.consecutive_escapes} consecutive escapes, force restarting"
                            )
                            self.adb.force_stop(app.package)
                            time.sleep(0.5)
                            self.adb.launch_app(app.package)
                            time.sleep(2.0)
                            monitor.on_valid_step()
                        else:
                            smart_explorer.return_to_app(app.package)
                            time.sleep(1.0)
                        xml_str = self.adb.dump_xml()

                except Exception as e:
                    logger.warning(f"Step {step} error: {e}")
                    smart_explorer.recover(app.package)
                    time.sleep(1.0)
                    xml_str = self.adb.dump_xml()

            # Wait for remaining TCP data
            time.sleep(2.0)

        except Exception as e:
            logger.error(f"Session error: {e}")
            return SessionResult(
                session_id=session_id,
                app_package=app.package,
                total_steps=self.writer.step_count,
                external_app_events=monitor.total_escapes,
                success=False,
                error=str(e),
            )
        finally:
            server.stop()
            self.writer.finalize_session()

        result = SessionResult(
            session_id=session_id,
            app_package=app.package,
            total_steps=self.writer.step_count,
            external_app_events=monitor.total_escapes,
            success=True,
        )
        logger.info(f"Session complete: {result}")
        return result

    def run_batch(self, apps_config_path: str = "configs/collection/apps.yaml") -> list[SessionResult]:
        """Run collection for all apps in config."""
        with open(apps_config_path, "r") as f:
            apps_data = yaml.safe_load(f)

        results = []
        for app_data in apps_data["apps"]:
            app = AppConfig(**app_data)

            # Install APK if needed
            if app.source == "apk" and app.apk:
                if not self.adb.is_package_installed(app.package):
                    logger.info(f"Installing {app.name} from {app.apk}")
                    self.adb.install(app.apk)

            result = self.run_session(app)
            results.append(result)

            # Brief pause between apps
            time.sleep(1.0)

        return results
