"""Runtime configuration with precedence: CLI args > env > config file > defaults.

The config object is built once at startup. `serve` validates the bind address
BEFORE spinning up the worker pool, so a bad host/port fails loud and clean
instead of leaving a half-started process (a core V3 reliability goal).
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field, fields
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val is not None else default


@dataclass
class Config:
    # --- Network ---
    host: str = field(default_factory=lambda: _env_str("KIRO_API_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("KIRO_API_PORT", 8787))
    advertised_host: str = field(
        default_factory=lambda: _env_str("KIRO_API_ADVERTISED_HOST", "")
    )

    # --- Auth (bridge API key; empty = no auth) ---
    auth_key: str = field(default_factory=lambda: _env_str("KIRO_API_AUTH_KEY", "").strip())

    # --- Worker pool (elastic) ---
    max_workers: int = field(default_factory=lambda: _env_int("KIRO_API_MAX_WORKERS", 8))
    min_workers: int = field(default_factory=lambda: _env_int("KIRO_API_MIN_WORKERS", 0))
    worker_idle_timeout: int = field(
        default_factory=lambda: _env_int("KIRO_API_WORKER_IDLE_TIMEOUT", 300)
    )
    max_queue: int = field(default_factory=lambda: _env_int("KIRO_API_MAX_QUEUE", 256))

    # --- Timeouts (seconds) ---
    timeout_short: int = field(default_factory=lambda: _env_int("KIRO_API_TIMEOUT_SHORT", 120))
    timeout_long: int = field(default_factory=lambda: _env_int("KIRO_API_TIMEOUT_LONG", 900))
    sse_keepalive_interval: int = field(
        default_factory=lambda: _env_int("KIRO_API_SSE_KEEPALIVE", 15)
    )

    # --- Auth watchdog ---
    auth_refresh_margin: int = field(
        default_factory=lambda: _env_int("KIRO_API_AUTH_REFRESH_MARGIN", 300)
    )
    auth_poll_interval: int = field(
        default_factory=lambda: _env_int("KIRO_API_AUTH_POLL_INTERVAL", 30)
    )

    # --- Model ---
    default_model: str = field(default_factory=lambda: _env_str("KIRO_API_DEFAULT_MODEL", "auto"))

    # --- kiro-cli integration ---
    kiro_cli_bin: str = field(default_factory=lambda: _env_str("KIRO_CLI_BIN", "kiro-cli"))
    acp_engine: str = field(default_factory=lambda: _env_str("KIRO_ACP_ENGINE", "v2"))
    workspace_dir: str = field(default_factory=lambda: _env_str("KIRO_ACP_WORKSPACE", ""))
    trust_tools: bool = field(default_factory=lambda: _env_bool("KIRO_ACP_TRUST_TOOLS", True))
    surface_thinking: bool = field(
        default_factory=lambda: _env_bool("KIRO_ACP_SURFACE_THINKING", True)
    )

    # --- Logging ---
    log_format: str = field(default_factory=lambda: _env_str("KIRO_API_LOG_FORMAT", "text"))
    log_level: str = field(default_factory=lambda: _env_str("KIRO_API_LOG_LEVEL", "INFO"))

    @property
    def auth_required(self) -> bool:
        return bool(self.auth_key)

    def base_host(self) -> str:
        """Host advertised to clients (falls back to bind host, mapping 0.0.0.0 → localhost)."""
        if self.advertised_host:
            return self.advertised_host
        if self.host in {"0.0.0.0", "::", ""}:
            return "localhost"
        return self.host

    def urls(self) -> dict[str, str]:
        h = self.base_host()
        base = f"http://{h}:{self.port}"
        return {
            "openai": f"{base}/v1",
            "anthropic": base,
            "acp": f"{base}/acp",
            "health": f"{base}/health",
            "stats": f"{base}/stats",
        }

    def apply_overrides(self, **overrides) -> Config:
        """Apply non-None CLI overrides (highest precedence)."""
        valid = {f.name for f in fields(self)}
        for key, value in overrides.items():
            if value is not None and key in valid:
                setattr(self, key, value)
        return self

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def validate_bind(self) -> None:
        """Raise BindError if host/port cannot be bound. Called before pool startup."""
        if not (0 <= self.port <= 65535):
            raise BindError(f"port {self.port} out of range 0-65535")
        try:
            infos = socket.getaddrinfo(
                self.host, self.port, proto=socket.IPPROTO_TCP, flags=socket.AI_PASSIVE
            )
        except socket.gaierror as exc:
            raise BindError(f"cannot resolve host '{self.host}': {exc}") from exc
        family, socktype, proto, _canon, sockaddr = infos[0]
        s = socket.socket(family, socktype, proto)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(sockaddr)
        except OSError as exc:
            raise BindError(
                f"cannot bind {self.host}:{self.port} ({exc.strerror or exc})"
            ) from exc
        finally:
            s.close()


class BindError(Exception):
    """Raised when the configured host/port cannot be bound."""


def load_config_file(path: Path) -> dict:
    """Load a simple KEY=VALUE .env-style config file into env-name→value dict."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def build_config(config_file: Path | None = None, **cli_overrides) -> Config:
    """Build config honoring precedence CLI > env > file > defaults."""
    # File values seed the environment only where env is unset (so env wins over file).
    if config_file is not None:
        for key, val in load_config_file(config_file).items():
            os.environ.setdefault(key, val)
    cfg = Config()  # reads env + defaults
    cfg.apply_overrides(**cli_overrides)  # CLI wins
    return cfg
