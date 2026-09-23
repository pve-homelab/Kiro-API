"""Config precedence + bind validation."""
from __future__ import annotations

import socket

import pytest

from kiro_api.config import BindError, Config, build_config


def test_defaults():
    cfg = Config()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8787
    assert cfg.max_workers == 8


def test_env_override(monkeypatch):
    monkeypatch.setenv("KIRO_API_PORT", "9000")
    monkeypatch.setenv("KIRO_API_MAX_WORKERS", "42")
    cfg = Config()
    assert cfg.port == 9000
    assert cfg.max_workers == 42


def test_cli_overrides_win(monkeypatch):
    monkeypatch.setenv("KIRO_API_PORT", "9000")
    cfg = build_config(port=1234)
    assert cfg.port == 1234  # CLI beats env


def test_base_host_maps_wildcard():
    cfg = Config()
    cfg.host = "0.0.0.0"
    assert cfg.base_host() == "localhost"
    cfg.host = "192.168.1.5"
    assert cfg.base_host() == "192.168.1.5"


def test_urls_include_all_protocols():
    cfg = Config()
    urls = cfg.urls()
    assert set(urls) >= {"openai", "anthropic", "acp", "health", "stats"}


def test_validate_bind_ok_on_free_port():
    cfg = Config()
    cfg.host = "127.0.0.1"
    cfg.port = 0  # 0 = let OS choose; bind must succeed
    cfg.validate_bind()  # should not raise


def test_validate_bind_rejects_bad_port():
    cfg = Config()
    cfg.port = 99999
    with pytest.raises(BindError):
        cfg.validate_bind()


def test_validate_bind_rejects_used_port():
    # Hold a port, then assert bind fails.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    used_port = s.getsockname()[1]
    s.listen(1)
    try:
        cfg = Config()
        cfg.host = "127.0.0.1"
        cfg.port = used_port
        with pytest.raises(BindError):
            cfg.validate_bind()
    finally:
        s.close()


def test_validate_bind_rejects_unresolvable_host():
    cfg = Config()
    cfg.host = "no-such-host.invalid."
    cfg.port = 8787
    with pytest.raises(BindError):
        cfg.validate_bind()
