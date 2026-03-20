"""TCP server for receiving data from Android AccessibilityService app."""

import json
import logging
import os
import socket
import struct
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

BUFFER_SIZE = 65536


class CollectionServer:
    """TCP server that receives screenshots and XML from the Android app."""

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
        # Smart Explorer synchronization
        self._xml_event = threading.Event()
        self._latest_xml: Optional[str] = None

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

    def _run(self):
        """Main server loop."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.settimeout(1.0)
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(1)

        while self._running:
            try:
                client, addr = self._server_socket.accept()
                self._client = client
                logger.info(f"Client connected: {addr}")
                self._handle_client(client)
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, client: socket.socket):
        """Handle messages from a connected client."""
        try:
            while self._running:
                msg_type = client.recv(1)
                if not msg_type:
                    break

                msg_type = msg_type.decode("ascii")

                if msg_type == "S":
                    self._handle_screenshot(client)
                elif msg_type == "X":
                    self._handle_xml(client)
                elif msg_type == "E":
                    self._handle_external_app(client)
                elif msg_type == "F":
                    logger.info("Received finish signal")
                    if self.on_finish:
                        self.on_finish()
                    break
                else:
                    logger.warning(f"Unknown message type: {msg_type}")
        except (ConnectionResetError, BrokenPipeError):
            logger.warning("Client disconnected")
        finally:
            client.close()
            self._client = None

    def _recv_text_line(self, client: socket.socket) -> str:
        """Receive text until newline."""
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(1)
            if not chunk:
                break
            data += chunk
        return data.decode("utf-8").strip()

    def _recv_binary(self, client: socket.socket) -> bytes:
        """Receive binary data with size prefix (size as text line)."""
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

    def _handle_screenshot(self, client: socket.socket):
        """Handle screenshot message: 'S' + size_line + binary_data."""
        image_data = self._recv_binary(client)
        if self.on_screenshot:
            self.on_screenshot(image_data)
        logger.debug(f"Received screenshot: {len(image_data)} bytes")

    def _handle_xml(self, client: socket.socket):
        """Handle XML message: 'X' + top_pkg_line + target_pkg_line + size_line + xml_data."""
        top_package = self._recv_text_line(client)
        target_package = self._recv_text_line(client)
        xml_data = self._recv_binary(client)
        raw_xml = xml_data.decode("utf-8").strip()
        # Normalize empty class attributes
        raw_xml = raw_xml.replace('class=""', 'class="unknown"')

        if self.on_xml:
            self.on_xml(raw_xml, top_package, target_package)
        # Signal Smart Explorer synchronization
        self._latest_xml = raw_xml
        self._xml_event.set()
        logger.debug(f"Received XML: top={top_package}, target={target_package}")

    def _handle_external_app(self, client: socket.socket):
        """Handle external app detection: 'E' + json_line."""
        payload_str = self._recv_text_line(client)
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            payload = {"raw": payload_str}

        if self.on_external_app:
            self.on_external_app(payload)
        logger.warning(f"External app detected: {payload}")

    def wait_for_xml(self, timeout: float = 15.0) -> Optional[str]:
        """Block until the next XML is received from the Android app.

        Returns the raw XML string, or None on timeout.
        Used by SmartExplorer orchestrator for step-by-step synchronization.
        """
        self._xml_event.clear()
        if self._xml_event.wait(timeout):
            return self._latest_xml
        return None
