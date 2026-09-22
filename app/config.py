"""Runtime configuration for the Kiro-API V2 service.

All values are read from environment variables (see .env.example). The tray
agent edits the .env file and restarts the container to apply changes, so this
module only needs to read the environment at process start.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Config:
    # Network
    host: str = field(default_factory=lambda: os.environ.get("KIRO_API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _get_int("KIRO_API_PORT", 8787))
    dashboard_port: int = field(default_factory=lambda: _get_int("KIRO_DASHBOARD_PORT", 8788))

    # The address advertised to clients / shown in the dashboard + tray. Inside a
    # container we bind 0.0.0.0, but clients connect via localhost on the host.
    advertised_host: str = field(
        default_factory=lambda: os.environ.get("KIRO_API_ADVERTISED_HOST", "localhost")
    )

    # Auth — empty means no auth required.
    auth_key: str = field(default_factory=lambda: os.environ.get("KIRO_API_AUTH_KEY", "").strip())

    # Concurrency
    max_concurrency: int = field(default_factory=lambda: _get_int("KIRO_API_MAX_CONCURRENCY", 8))
    max_queue: int = field(default_factory=lambda: _get_int("KIRO_API_MAX_QUEUE", 0))
    reject_when_busy: bool = field(default_factory=lambda: _get_bool("KIRO_API_REJECT_WHEN_BUSY", False))

    # Timeout tiers (seconds)
    timeout_short: int = field(default_factory=lambda: _get_int("KIRO_API_TIMEOUT_SHORT", 120))
    timeout_long: int = field(default_factory=lambda: _get_int("KIRO_API_TIMEOUT_LONG", 900))

    # Model
    default_model: str = field(default_factory=lambda: os.environ.get("KIRO_API_DEFAULT_MODEL", "auto"))

    # kiro-cli integration
    kiro_cli_bin: str = field(default_factory=lambda: os.environ.get("KIRO_CLI_BIN", "kiro-cli"))
    kiro_cli_workspace: str = field(
        default_factory=lambda: os.environ.get("KIRO_CLI_WORKSPACE", "/work")
    )

    @property
    def auth_required(self) -> bool:
        return bool(self.auth_key)

    @property
    def v1_base_url(self) -> str:
        return f"http://{self.advertised_host}:{self.port}/v1"

    @property
    def dashboard_url(self) -> str:
        return f"http://{self.advertised_host}:{self.dashboard_port}"


# Singleton loaded at import time.
config = Config()
