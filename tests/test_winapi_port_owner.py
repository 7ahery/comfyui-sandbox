"""The real `WindowsApi.port_owner` (ticket 08): who listens on a TCP port, by IP Helper."""

import os
import socket
import sys
from pathlib import Path

import pytest

from comfyui_sandbox.winapi import PortOwner, WindowsApi

pytestmark = pytest.mark.windows_only


def listener(host: str, family=socket.AF_INET):
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.bind((host, 0))
    sock.listen(1)
    return sock


def test_a_port_this_process_listens_on_names_this_process():
    with listener("127.0.0.1") as sock:
        owner = WindowsApi().port_owner(sock.getsockname()[1])

    assert isinstance(owner, PortOwner)
    assert owner.pid == os.getpid()
    assert owner.name.lower() == Path(sys.executable).name.lower()


def test_a_wildcard_listener_counts_too():
    with listener("0.0.0.0") as sock:
        owner = WindowsApi().port_owner(sock.getsockname()[1])

    assert owner is not None and owner.pid == os.getpid()


def test_an_ipv6_listener_counts_too():
    with listener("::1", socket.AF_INET6) as sock:
        owner = WindowsApi().port_owner(sock.getsockname()[1])

    assert owner is not None and owner.pid == os.getpid()


def test_a_free_port_has_no_owner():
    with listener("127.0.0.1") as sock:
        port = sock.getsockname()[1]

    assert WindowsApi().port_owner(port) is None
