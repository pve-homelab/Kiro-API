"""AuthManager — the self-healing auth actor.

Solves V3's #1 requirement: never get stuck on a stale token. It is the single
authoritative source of login state and the only actor that triggers a refresh
or a login, so the shared single-account credential cache is touched by exactly
one owner (important given Kiro's one-active-session-per-OIDC-client behavior).

State machine:
    UNKNOWN → LOGGED_OUT → LOGGING_IN → LOGGED_IN → EXPIRING_SOON
            → REFRESHING → (LOGGED_IN | LOGGED_OUT)

Liveness is cross-checked from two signals:
  1. cheap/local: the smallest `expiresAt` across discovered token cache files
     (~/.aws/sso/cache, ~/.aws/login/cache) → time-to-expiry.
  2. authoritative: `kiro-cli whoami` (short timeout, cached).

Token files are referenced by path/existence; only non-secret metadata
(expiresAt/region/startUrl) is read, never token values.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("kiro-api.auth")

# States
UNKNOWN = "unknown"
LOGGED_OUT = "logged_out"
LOGGING_IN = "logging_in"
LOGGED_IN = "logged_in"
EXPIRING_SOON = "expiring_soon"
REFRESHING = "refreshing"


def _cache_dirs() -> list[Path]:
    home = Path(os.environ.get("HOME", str(Path.home())))
    dirs = []
    kiro_cfg = os.environ.get("KIRO_CONFIG_DIR")
    if kiro_cfg:
        dirs.append(Path(kiro_cfg))
    dirs += [
        home / ".aws" / "sso" / "cache",
        home / ".aws" / "login" / "cache",
        home / ".kiro" / "cache",
        home / ".kiro",
    ]
    return dirs


def _parse_expires_at(value) -> float | None:
    """Parse an ISO-8601 or epoch expiresAt into a UTC epoch float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        # epoch-as-string
        if text.isdigit():
            return float(text)
        # ISO-8601, tolerate trailing Z
        try:
            text = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def discover_earliest_expiry(dirs: list[Path] | None = None) -> float | None:
    """Return the earliest token expiry epoch found across cache dirs, or None.

    Reads only the `expiresAt` field; never reads/logs token values.
    """
    dirs = dirs or _cache_dirs()
    earliest: float | None = None
    for d in dirs:
        try:
            if not d.is_dir():
                continue
            for f in d.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                exp = _parse_expires_at(
                    data.get("expiresAt") or data.get("expires_at") or data.get("expiration")
                )
                if exp is None:
                    continue
                if earliest is None or exp < earliest:
                    earliest = exp
        except OSError:
            continue
    return earliest


@dataclass
class AuthState:
    state: str = UNKNOWN
    detail: str = "not checked"
    expires_at: float | None = None
    last_check: float = 0.0

    @property
    def logged_in(self) -> bool:
        return self.state in {LOGGED_IN, EXPIRING_SOON}

    def seconds_to_expiry(self) -> float | None:
        if self.expires_at is None:
            return None
        return self.expires_at - time.time()

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "detail": self.detail,
            "logged_in": self.logged_in,
            "expires_at": self.expires_at,
            "seconds_to_expiry": self.seconds_to_expiry(),
            "last_check": self.last_check,
        }


class AuthManager:
    def __init__(
        self,
        kiro_cli_bin: str,
        refresh_margin: int = 300,
        poll_interval: int = 30,
        whoami_ttl: float = 10.0,
    ) -> None:
        self.kiro_cli_bin = kiro_cli_bin
        self.refresh_margin = refresh_margin
        self.poll_interval = poll_interval
        self.whoami_ttl = whoami_ttl
        self.state = AuthState()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._login_state: dict = {"running": False, "output": "", "returncode": None}

    # -- lifecycle --
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="auth-watchdog")
            log.info("auth watchdog started (margin=%ss poll=%ss)", self.refresh_margin, self.poll_interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh_state()
                sec = self.state.seconds_to_expiry()
                if (
                    self.state.logged_in
                    and sec is not None
                    and sec <= self.refresh_margin
                ):
                    await self.try_refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # watchdog must never die
                log.warning("auth watchdog error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    # -- probes --
    async def _whoami(self) -> tuple[bool, str]:
        if not shutil.which(self.kiro_cli_bin) and not Path(self.kiro_cli_bin).exists():
            return False, f"kiro-cli not found at '{self.kiro_cli_bin}'"
        try:
            proc = await asyncio.create_subprocess_exec(
                self.kiro_cli_bin, "whoami",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
            except TimeoutError:
                proc.kill()
                return False, "whoami timed out"
            if proc.returncode == 0:
                return True, (out or b"").decode("utf-8", "replace").strip() or "logged in"
            return False, (err or b"").decode("utf-8", "replace").strip() or "not logged in"
        except FileNotFoundError:
            return False, f"kiro-cli not found at '{self.kiro_cli_bin}'"

    async def refresh_state(self, force: bool = False) -> AuthState:
        now = time.time()
        if not force and (now - self.state.last_check) < self.whoami_ttl:
            return self.state
        async with self._lock:
            expiry = discover_earliest_expiry()
            logged_in, detail = await self._whoami()
            self.state.last_check = time.time()
            self.state.expires_at = expiry
            if logged_in:
                sec = expiry - now if expiry else None
                if sec is not None and sec <= self.refresh_margin:
                    self.state.state = EXPIRING_SOON
                else:
                    self.state.state = LOGGED_IN
                self.state.detail = detail
            else:
                # Don't clobber an in-flight login.
                if self.state.state != LOGGING_IN:
                    self.state.state = LOGGED_OUT
                self.state.detail = detail
        return self.state

    # -- refresh (proactive, before workers hit a stale token) --
    async def try_refresh(self) -> bool:
        async with self._lock:
            if self.state.state == REFRESHING:
                return False
            self.state.state = REFRESHING
        log.info("token expiring soon — attempting proactive refresh")
        ok = await self._run_refresh_command()
        await self.refresh_state(force=True)
        if ok and self.state.logged_in:
            log.info("token refreshed; new expiry in %ss", int(self.state.seconds_to_expiry() or 0))
        else:
            log.warning("proactive refresh did not renew the session; awaiting re-login")
        return ok

    async def _run_refresh_command(self) -> bool:
        """Try a non-interactive refresh. kiro-cli/SSO may auto-refresh via
        whoami/refresh token; we attempt a lightweight command and fall back to
        marking logged-out (never blocks; never crashes)."""
        for args in (["whoami"], ["login", "--refresh"]):
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.kiro_cli_bin, *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=30)
                except TimeoutError:
                    proc.kill()
                    continue
                if proc.returncode == 0:
                    return True
            except (FileNotFoundError, OSError):
                return False
        return False

    # -- device-flow login (backbone) --
    async def start_device_login(self) -> dict:
        if self._login_state["running"]:
            return dict(self._login_state)
        self._login_state.update(running=True, output="", returncode=None)
        async with self._lock:
            self.state.state = LOGGING_IN
        asyncio.create_task(self._run_device_login())
        return dict(self._login_state)

    async def _run_device_login(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.kiro_cli_bin, "login", "--use-device-flow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            chunks: list[str] = []
            assert proc.stdout is not None
            opened = False
            async for line in proc.stdout:
                text = line.decode("utf-8", "replace")
                chunks.append(text)
                self._login_state["output"] = "".join(chunks)[-4000:]
                if not opened:
                    url = _extract_url(text)
                    if url:
                        opened = True
                        _maybe_open_browser(url)
            await proc.wait()
            self._login_state["returncode"] = proc.returncode
        except FileNotFoundError:
            self._login_state["output"] = f"kiro-cli not found at '{self.kiro_cli_bin}'"
            self._login_state["returncode"] = 127
        finally:
            self._login_state["running"] = False
            await self.refresh_state(force=True)

    def login_status(self) -> dict:
        return dict(self._login_state)


def _extract_url(text: str) -> str | None:
    import re
    m = re.search(r"https?://[^\s'\"]+", text)
    return m.group(0) if m else None


def _maybe_open_browser(url: str) -> None:
    """xdg-open the verification URL if a desktop session is present; else skip."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    opener = shutil.which("xdg-open")
    if not opener:
        return
    try:
        subprocess.Popen(
            [opener, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("opened login URL in browser")
    except OSError:
        pass
