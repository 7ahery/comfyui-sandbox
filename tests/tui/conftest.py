"""Fixtures for the TUI tests."""

from __future__ import annotations

import json

import pytest

from comfyui_sandbox.winapi import PortOwner


@pytest.fixture(autouse=True)
def answering_sandbox_ports_have_listeners(winapi, http, home) -> None:
    """A sandbox's port that answers has its container listening on it (WSL's relay), as on a real
    machine. Home reads a sandbox's state by asking who listens first (a closed port makes the
    HTTP request wait out its timeout), so a fake that answers on a sandbox's port with no
    listener would read as Stopped. Ports no sandbox uses stay free, and listeners the test
    scripts itself (`winapi.port_owners`) win."""
    scripted = winapi.port_owner

    def sandbox_ports() -> set[int]:
        path = home / ".comfyui-sandbox" / "installs.json"
        return {i["port"] for i in json.loads(path.read_text("utf-8"))["installs"]} if path.exists() else set()

    def port_owner(port: int) -> PortOwner | None:
        if scripted(port) is None and http.replies.get(port, "") is not None and port in sandbox_ports():
            return PortOwner(900, "wslrelay.exe")
        return scripted(port)

    winapi.port_owner = port_owner
