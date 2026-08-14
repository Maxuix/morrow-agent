from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def network_guard(monkeypatch, request):
    if request.node.get_closest_marker("live"):
        return

    def blocked(*args, **kwargs):
        raise AssertionError("unexpected network access in an offline test")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
