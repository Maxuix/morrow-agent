"""Verifier proxy relay forwards bytes without changing verifier requests."""

from __future__ import annotations

import socket
import socketserver
import threading
import unittest

from harness.verifier_proxy import VerifierProxyRelay


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request.recv(1024)
        self.request.sendall(data.upper())


class VerifierProxyRelayTests(unittest.TestCase):
    def test_relays_bytes_to_host_loopback_upstream(self) -> None:
        with socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler) as upstream:
            thread = threading.Thread(target=upstream.serve_forever, daemon=True)
            thread.start()
            try:
                with VerifierProxyRelay("127.0.0.1", upstream.server_address[1]) as relay:
                    with socket.create_connection(("127.0.0.1", relay.port), timeout=5) as client:
                        client.sendall(b"verifier")
                        self.assertEqual(client.recv(1024), b"VERIFIER")
            finally:
                upstream.shutdown()
                thread.join()
