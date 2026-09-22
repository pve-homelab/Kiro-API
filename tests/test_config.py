"""Unit tests for config env parsing."""
from __future__ import annotations

import importlib

import app.config as config_mod


def _reload_with(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(config_mod)
    return config_mod.Config()


def test_bool_parsing(monkeypatch):
    cfg = _reload_with(monkeypatch, KIRO_API_REJECT_WHEN_BUSY="true")
    assert cfg.reject_when_busy is True
    cfg = _reload_with(monkeypatch, KIRO_API_REJECT_WHEN_BUSY="no")
    assert cfg.reject_when_busy is False


def test_int_parsing_and_fallback(monkeypatch):
    cfg = _reload_with(monkeypatch, KIRO_API_PORT="9000")
    assert cfg.port == 9000
    cfg = _reload_with(monkeypatch, KIRO_API_PORT="not-a-number")
    assert cfg.port == 8787  # falls back to default


def test_auth_required_toggle(monkeypatch):
    cfg = _reload_with(monkeypatch, KIRO_API_AUTH_KEY="")
    assert cfg.auth_required is False
    cfg = _reload_with(monkeypatch, KIRO_API_AUTH_KEY="secret")
    assert cfg.auth_required is True


def test_derived_urls(monkeypatch):
    cfg = _reload_with(monkeypatch, KIRO_API_ADVERTISED_HOST="localhost",
                       KIRO_API_PORT="8787", KIRO_DASHBOARD_PORT="8788")
    assert cfg.v1_base_url == "http://localhost:8787/v1"
    assert cfg.dashboard_url == "http://localhost:8788"


def teardown_module(_module):
    # Restore the module to the test environment's config for other tests.
    importlib.reload(config_mod)
