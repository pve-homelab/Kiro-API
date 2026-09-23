"""systemd unit installer for the kiro-api service.

Writes a Type=notify unit with auto-restart + watchdog, sized for the worker
pool, and enables it. Supports system-wide (/etc/systemd/system) and per-user
(~/.config/systemd/user) installs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "kiro-api.service"


def _unit_text(exec_path: str, user_service: bool) -> str:
    # TasksMax covers max_workers subprocesses + their child trees + headroom.
    wanted_by = "default.target" if user_service else "multi-user.target"
    user_line = "" if user_service else "# Runs as the invoking user by default; set User= for a system account.\n"
    return f"""[Unit]
Description=Kiro-API V3 — OpenAI/Anthropic/ACP gateway for kiro-cli
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
{user_line}ExecStart={exec_path} serve
Restart=on-failure
RestartSec=2
WatchdogSec=30
TimeoutStopSec=25
KillMode=mixed
TasksMax=2048
LimitNOFILE=65536
Environment=KIRO_API_LOG_FORMAT=json
StandardOutput=journal
StandardError=journal

[Install]
WantedBy={wanted_by}
"""


def _exec_path() -> str:
    # Prefer the installed console script; fall back to `python -m`.
    found = shutil.which("kiro-api")
    if found:
        return found
    return f"{sys.executable} -m kiro_api.cli"


def _unit_dir(user: bool) -> Path:
    if user:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        return base / "systemd" / "user"
    return Path("/etc/systemd/system")


def _systemctl(*args: str, user: bool) -> tuple[int, str]:
    cmd = ["systemctl"]
    if user:
        cmd.append("--user")
    cmd += list(args)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 1, "systemctl not found (is this a systemd host?)"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def install_service(user: bool = False) -> int:
    unit_dir = _unit_dir(user)
    try:
        unit_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"permission denied writing {unit_dir}; run with sudo for a system service, "
              "or use --user", file=sys.stderr)
        return 1
    unit_path = unit_dir / SERVICE_NAME
    unit_path.write_text(_unit_text(_exec_path(), user), encoding="utf-8")
    print(f"wrote {unit_path}")

    rc, out = _systemctl("daemon-reload", user=user)
    if rc != 0:
        print(f"daemon-reload: {out}", file=sys.stderr)
    rc, out = _systemctl("enable", "--now", SERVICE_NAME, user=user)
    if rc != 0:
        print(f"enable failed: {out}", file=sys.stderr)
        print("You can start it manually with: "
              f"systemctl {'--user ' if user else ''}start {SERVICE_NAME}")
        return rc
    scope = "--user " if user else ""
    print(f"kiro-api service installed and started.\n"
          f"  status : systemctl {scope}status {SERVICE_NAME}\n"
          f"  logs   : journalctl {scope}-u {SERVICE_NAME} -f")
    return 0


def uninstall_service(user: bool = False) -> int:
    _systemctl("disable", "--now", SERVICE_NAME, user=user)
    unit_path = _unit_dir(user) / SERVICE_NAME
    if unit_path.exists():
        try:
            unit_path.unlink()
            print(f"removed {unit_path}")
        except OSError as exc:
            print(f"could not remove {unit_path}: {exc}", file=sys.stderr)
            return 1
    _systemctl("daemon-reload", user=user)
    return 0
