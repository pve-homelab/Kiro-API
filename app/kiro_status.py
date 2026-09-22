"""Check kiro-cli login status for the health endpoint / tray status dot.

Runs `kiro-cli whoami` with a short timeout and caches the result briefly so
the dashboard polling and tray polling don't spawn a process every second.
"""
from __future__ import annotations

import asyncio
import time

from .config import config

_CACHE_TTL = 10.0
_cache: dict[str, object] = {"ts": 0.0, "logged_in": False, "detail": "unknown"}


async def check_login(force: bool = False) -> dict:
    now = time.time()
    if not force and (now - float(_cache["ts"])) < _CACHE_TTL:
        return {"logged_in": _cache["logged_in"], "detail": _cache["detail"]}

    logged_in = False
    detail = "unknown"
    try:
        proc = await asyncio.create_subprocess_exec(
            config.kiro_cli_bin,
            "whoami",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except TimeoutError:
            proc.kill()
            detail = "whoami timed out"
        else:
            out = (stdout or b"").decode("utf-8", "replace").strip()
            err = (stderr or b"").decode("utf-8", "replace").strip()
            if proc.returncode == 0:
                logged_in = True
                detail = out or "logged in"
            else:
                detail = err or "not logged in"
    except FileNotFoundError:
        detail = f"kiro-cli not found at '{config.kiro_cli_bin}'"

    _cache.update(ts=now, logged_in=logged_in, detail=detail)
    return {"logged_in": logged_in, "detail": detail}
