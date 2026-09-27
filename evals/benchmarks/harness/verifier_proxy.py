"""Short-lived host relay for verifier downloads in Docker Desktop containers.

Docker Desktop can connect to a host loopback listener through
``host.docker.internal``, but forwarding a container connection straight into
the host's local proxy can break the TLS tunnel. This relay opens the upstream
connection from the host itself and copies bytes without interpreting them.
"""

from __future__ import annotations

import select
import socket
import socketserver
import threading


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        host, port = self.server.upstream  # type: ignore[attr-defined]
        try:
            upstream = socket.create_connection((host, port), timeout=5)
        except OSError:
            return
        upstream.settimeout(None)
        with upstream:
            readable = [self.request, upstream]
            while readable:
                try:
                    ready, _, _ = select.select(readable, [], [])
                    for source in ready:
                        destination = upstream if source is self.request else self.request
                        data = source.recv(65536)
                        if data:
                            destination.sendall(data)
                        else:
                            readable.remove(source)
                            destination.shutdown(socket.SHUT_WR)
                except OSError:
                    return


class _RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    upstream: tuple[str, int]


class VerifierProxyRelay:
    """Expose a host-local HTTP proxy on an ephemeral host loopback port."""

    def __init__(self, host: str, port: int) -> None:
        self._server = _RelayServer(("127.0.0.1", 0), _RelayHandler)
        self._server.upstream = (host, port)
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    def __enter__(self) -> VerifierProxyRelay:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join()
