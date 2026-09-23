"""Minimal sd_notify implementation (no external dependency).

systemd sets NOTIFY_SOCKET when a service is Type=notify. We write datagrams to
it to signal readiness and to pet the watchdog. If the env var is absent (not
under systemd, e.g. dev/WSL), every call is a harmless no-op.
"""
from __future__ import annotations

import os
import socket


def _send(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    # Abstract namespace sockets start with '@' → NUL byte.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
        try:
            sock.connect(addr)
            sock.sendall(message.encode("utf-8"))
            return True
        finally:
            sock.close()
    except OSError:
        return False


def ready() -> bool:
    return _send("READY=1")


def watchdog() -> bool:
    return _send("WATCHDOG=1")


def status(text: str) -> bool:
    return _send(f"STATUS={text}")


def stopping() -> bool:
    return _send("STOPPING=1")


def watchdog_usec() -> int | None:
    """The watchdog interval systemd expects, in microseconds (None if unset)."""
    raw = os.environ.get("WATCHDOG_USEC")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
