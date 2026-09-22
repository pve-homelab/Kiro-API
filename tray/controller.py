"""Controls and monitors the kiro-api container from the host.

Responsibilities:
  * Poll /health to compute the tray status (red/yellow/orange/green).
  * Restart the stack after a port/address change (docker compose up -d).
  * Trigger a kiro-cli login (prefers the container's /admin/login so it uses
    the same CLI + mounted creds the API uses; falls back to a host terminal).
  * Toggle the bridge auth key at runtime via /admin/auth-key.

Everything is defensive: the tray must never crash because Docker or the API is
momentarily unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Status:
    state: str          # red | yellow | orange | green
    label: str
    v1_url: str = ""
    dashboard_url: str = ""
    model: str = ""
    auth_required: bool = False
    detail: str = ""


class Controller:
    def __init__(self, api_base: str, compose_file: str = "docker-compose.yml") -> None:
        self.api_base = api_base.rstrip("/")
        self.compose_file = compose_file

    # -- URLs -------------------------------------------------------------
    def set_api_base(self, api_base: str) -> None:
        self.api_base = api_base.rstrip("/")

    # -- status -----------------------------------------------------------
    def poll(self) -> Status:
        try:
            r = requests.get(f"{self.api_base}/health", timeout=3)
        except requests.RequestException:
            return Status("red", "API not running", detail="cannot reach /health")
        if r.status_code != 200:
            return Status("orange", f"error · HTTP {r.status_code}")
        h = r.json()
        v1 = h.get("v1_url", "")
        dash = h.get("dashboard_url", "")
        model = h.get("default_model", "")
        auth = h.get("auth_required", False)
        if not h.get("logged_in", False):
            return Status("yellow", "not logged in", v1, dash, model, auth,
                          h.get("login_detail", ""))
        reap = h.get("reaper", {})
        pool = h.get("pool", {})
        if reap.get("leak_warning") or pool.get("total_errors", 0) > 0:
            return Status("orange", "error · check dashboard", v1, dash, model, auth,
                          reap.get("detail", ""))
        return Status("green", "online · healthy", v1, dash, model, auth)

    # -- docker compose ---------------------------------------------------
    def _compose(self, *args: str, timeout: int = 120) -> tuple[int, str]:
        docker = shutil.which("docker") or "docker"
        cmd = [docker, "compose", "-f", self.compose_file, *args]
        try:
            proc = subprocess.run(
                cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout
            )
            return proc.returncode, (proc.stdout + proc.stderr).strip()
        except Exception as exc:  # noqa
            return 1, str(exc)

    def restart(self) -> tuple[bool, str]:
        code, out = self._compose("up", "-d")
        return code == 0, out

    def up(self) -> tuple[bool, str]:
        code, out = self._compose("up", "-d")
        return code == 0, out

    def down(self) -> tuple[bool, str]:
        code, out = self._compose("down")
        return code == 0, out

    # -- admin API --------------------------------------------------------
    def set_auth_key(self, key: str) -> tuple[bool, str]:
        try:
            r = requests.post(f"{self.api_base}/admin/auth-key", json={"key": key}, timeout=5)
            return r.status_code == 200, r.text
        except requests.RequestException as exc:
            return False, str(exc)

    def trigger_login(self) -> tuple[bool, str]:
        """Ask the container to run kiro-cli login (uses mounted creds)."""
        try:
            r = requests.post(f"{self.api_base}/admin/login", json={}, timeout=5)
            return r.status_code == 200, r.text
        except requests.RequestException as exc:
            return False, str(exc)

    def login_status(self) -> dict:
        try:
            return requests.get(f"{self.api_base}/admin/login/status", timeout=5).json()
        except requests.RequestException:
            return {"running": False, "output": "", "returncode": None}

    def host_login(self) -> tuple[bool, str]:
        """Fallback: open kiro-cli login in a host terminal (device flow)."""
        kiro = shutil.which("kiro-cli")
        if not kiro:
            return False, "kiro-cli not found on host PATH"
        for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                try:
                    subprocess.Popen([term, "-e", f"{kiro} login --use-device-flow"])
                    return True, f"Opened {term} for login"
                except Exception as exc:  # noqa
                    return False, str(exc)
        return False, "no terminal emulator found"
