"""AuthManager: token discovery, expiry parsing, state transitions."""
from __future__ import annotations

import json
import time

from kiro_api.auth import (
    EXPIRING_SOON,
    LOGGED_IN,
    LOGGED_OUT,
    AuthManager,
    _parse_expires_at,
    discover_earliest_expiry,
)


def test_parse_expires_at_iso():
    ts = _parse_expires_at("2030-01-01T00:00:00Z")
    assert ts is not None and ts > time.time()


def test_parse_expires_at_epoch():
    assert _parse_expires_at(1900000000) == 1900000000.0
    assert _parse_expires_at("1900000000") == 1900000000.0


def test_parse_expires_at_bad():
    assert _parse_expires_at("not-a-date") is None
    assert _parse_expires_at(None) is None


def test_discover_earliest_expiry(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"expiresAt": "2030-01-01T00:00:00Z"}))
    (d / "b.json").write_text(json.dumps({"expiresAt": "2028-01-01T00:00:00Z"}))
    (d / "junk.json").write_text("not json")
    earliest = discover_earliest_expiry([d])
    assert earliest is not None
    # 2028 is earlier than 2030
    assert earliest == _parse_expires_at("2028-01-01T00:00:00Z")


def test_discover_ignores_missing_dirs(tmp_path):
    assert discover_earliest_expiry([tmp_path / "nope"]) is None


async def test_refresh_state_logged_in(stub_bin, monkeypatch):
    monkeypatch.delenv("KIRO_STUB_LOGGED_OUT", raising=False)
    auth = AuthManager(stub_bin, refresh_margin=300, whoami_ttl=0)
    st = await auth.refresh_state(force=True)
    assert st.state in (LOGGED_IN, EXPIRING_SOON)
    assert st.logged_in is True


async def test_refresh_state_logged_out(stub_bin, monkeypatch):
    monkeypatch.setenv("KIRO_STUB_LOGGED_OUT", "1")
    auth = AuthManager(stub_bin, whoami_ttl=0)
    st = await auth.refresh_state(force=True)
    assert st.state == LOGGED_OUT
    assert st.logged_in is False
