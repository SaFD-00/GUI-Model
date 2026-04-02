"""TCP server for receiving data from Android AccessibilityService app."""

import json
import socket
import threading
from typing import Callable, Optional

from loguru import logger

BUFFER_SIZE = 65536


class CollectionServer:
    """TCP server that receives screenshots and XML from the Android app.

    Protocol (App → Server):
      S + size_line + binary_data   = Screenshot
      X + top_pkg + target_pkg + size_line + xml_data = XML hierarchy
      E + json_line                 = External app detection
      F                             = Session finish

    Protocol (Server → App):
      action_json + \\r\\n           = Action command
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 12345,
        on_screenshot: Optional[Callable] = None,
        on_xml: Optional[Callable] = None,
        on_external_app: Optional[Callable] = None,
        on_finish: Optional[Callable] = None,
    ):
        self.host = host
        self.port = port
        self.on_screenshot = on_screenshot
        self.on_xml = on_xml
        self.on_external_app = on_external_app
        self.on_finish = on_finish
        self._server_socket: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[socket.socket] = None
        # SmartExplorer synchronization
        self._xml_event = threading.Event()
        self._latest_xml: Optional[str] = None
        self._latest_xml_meta: Optional[dict] = None
        # Package name from client
        self._package_event = threading.Event()
        self._target_package: Optional[str] = None

    def start(self):
        """Start the server in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"Collection server started on {self.host}:{self.port}")

    def stop(self):
        """Stop the server."""
        self._running = False
        if self._client:
            try:
                self._client.close()
            except OSError:
                pass
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Collection server stopped")

    def is_client_connected(self) -> bool:
        return self._client is not None

    def send_action(self, action: dict) -> bool:
        """Send an action command to the connected App."""
        if not self._client:
            logger.warning("No client connected, cannot send action")
            return False
        try:
            data = json.dumps(action, ensure_ascii=False) + "\r\n"
            self._client.sendall(data.encode("utf-8"))
            return True
        except (OSError, BrokenPipeError) as e:
            logger.error(f"Failed to send action: {e}")
            return False

    def wait_for_xml(
        self, timeout: float = 15.0
    ) -> Optional[tuple[str, dict]]:
        """Block until the next XML is received from the Android app.

        Returns (xml_string, meta_dict) or None on timeout.
        """
        self._xml_event.clear()
        if self._xml_event.wait(timeout):
            meta = self._latest_xml_meta or {}
            return self._latest_xml, meta
        return None

    def _run(self):
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.settimeout(1.0)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(1)
        logger.info(f"Listening on {self.host}:{self.port}")

        while self._running:
            try:
                client, addr = self._server_socket.accept()
                self._client = client
                logger.info(f"Client connected from {addr[0]}:{addr[1]}")
                self._handle_client(client)
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, client: socket.socket):
        client.settimeout(60.0)
        try:
            while self._running:
                try:
                    msg_type = client.recv(1)
                except socket.timeout:
                    continue

                if not msg_type:
                    logger.info("Client disconnected")
                    break

                msg_type = msg_type.decode("ascii")

                if msg_type == "P":
                    self._handle_package_name(client)
                elif msg_type == "S":
                    self._handle_screenshot(client)
                elif msg_type == "X":
                    self._handle_xml(client)
                elif msg_type == "E":
                    self._handle_external_app(client)
                elif msg_type == "F":
                    logger.info("Received finish signal from client")
                    if self.on_finish:
                        self.on_finish()
                    break
                else:
                    logger.warning(f"Unknown message type: {msg_type!r}")
        except (ConnectionResetError, BrokenPipeError) as e:
            logger.warning(f"Client disconnected: {e}")
        except socket.timeout:
            logger.warning("Client connection timed out")
        finally:
            client.close()
            self._client = None

    def _recv_text_line(self, client: socket.socket) -> str:
        original_timeout = client.gettimeout()
        client.settimeout(30.0)
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = client.recv(1)
                if not chunk:
                    break
                data += chunk
            return data.decode("utf-8").strip()
        except socket.timeout:
            logger.warning("Timeout receiving text line")
            raise
        finally:
            client.settimeout(original_timeout)

    def _recv_binary(self, client: socket.socket) -> bytes:
        size_str = self._recv_text_line(client)
        file_size = int(size_str)
        data = b""
        remaining = file_size
        while remaining > 0:
            chunk = client.recv(min(remaining, BUFFER_SIZE))
            if not chunk:
                break
            data += chunk
            remaining -= len(chunk)
        return data

    def wait_for_package(self, timeout: float = 120.0) -> Optional[str]:
        """Block until the client sends target package name via P message."""
        self._package_event.clear()
        if self._package_event.wait(timeout):
            return self._target_package
        return None

    def _handle_package_name(self, client: socket.socket):
        """Receive target package name from client."""
        package_name = self._recv_text_line(client)
        self._target_package = package_name
        self._package_event.set()
        logger.info(f"Target package received: {package_name}")

    def _handle_screenshot(self, client: socket.socket):
        image_data = self._recv_binary(client)
        if self.on_screenshot:
            self.on_screenshot(image_data)
        logger.debug(f"Received screenshot: {len(image_data)} bytes")

    def _handle_xml(self, client: socket.socket):
        top_package = self._recv_text_line(client)
        target_package = self._recv_text_line(client)
        xml_data = self._recv_binary(client)
        raw_xml = xml_data.decode("utf-8").strip()
        raw_xml = raw_xml.replace('class=""', 'class="unknown"')

        if self.on_xml:
            self.on_xml(raw_xml, top_package, target_package)

        self._latest_xml = raw_xml
        self._latest_xml_meta = {
            "top_package": top_package,
            "target_package": target_package,
        }
        self._xml_event.set()
        logger.debug(
            f"Received XML: top={top_package}, target={target_package}, "
            f"size={len(raw_xml)} bytes"
        )

    def _handle_external_app(self, client: socket.socket):
        payload_str = self._recv_text_line(client)
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            payload = {"raw": payload_str}

        if self.on_external_app:
            self.on_external_app(payload)
        logger.warning(f"External app detected: {payload}")
