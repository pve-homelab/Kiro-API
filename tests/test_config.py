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


def test_write_config_value_creates_and_updates(tmp_path):
    from kiro_api.config import load_config_file, write_config_value

    path = tmp_path / "sub" / "config.env"
    write_config_value(path, "KIRO_API_HOST", "0.0.0.0")
    write_config_value(path, "KIRO_API_PORT", "9000")
    loaded = load_config_file(path)
    assert loaded["KIRO_API_HOST"] == "0.0.0.0"
    assert loaded["KIRO_API_PORT"] == "9000"

    # Updating an existing key replaces it, doesn't duplicate.
    write_config_value(path, "KIRO_API_PORT", "9100")
    text = path.read_text()
    assert text.count("KIRO_API_PORT=") == 1
    assert load_config_file(path)["KIRO_API_PORT"] == "9100"


def test_settable_keys_cover_host_and_port():
    from kiro_api.config import SETTABLE_KEYS

    assert SETTABLE_KEYS["host"] == "KIRO_API_HOST"
    assert SETTABLE_KEYS["port"] == "KIRO_API_PORT"


def test_saved_config_is_read_back(tmp_path, monkeypatch):
    from kiro_api.config import build_config, write_config_value

    # Ensure env doesn't shadow the file for this test.
    monkeypatch.delenv("KIRO_API_PORT", raising=False)
    monkeypatch.delenv("KIRO_API_HOST", raising=False)
    path = tmp_path / "config.env"
    write_config_value(path, "KIRO_API_PORT", "9000")
    write_config_value(path, "KIRO_API_HOST", "0.0.0.0")
    # build_config seeds env from the file (env not already set wins).
    cfg = build_config(config_file=path)
    assert cfg.port == 9000
    assert cfg.host == "0.0.0.0"
