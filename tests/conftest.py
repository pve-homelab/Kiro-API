"""Shared pytest fixtures.

The app reads configuration from environment variables at import time, so the
environment (including pointing KIRO_CLI_BIN at the offline stub) must be set
BEFORE any app module is imported. We do that at collection time here, then
import app modules lazily inside fixtures.

No real kiro-cli and no Docker are required — everything runs against
scripts/kiro-cli-stub.sh.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STUB = PROJECT_ROOT / "scripts" / "kiro-cli-stub.sh"


def _ensure_stub_executable() -> None:
    if STUB.exists():
        mode = STUB.stat().st_mode
        STUB.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# Set env before app import. pytest imports conftest very early, so module-level
# assignment here runs before test modules import app.*.
os.environ.setdefault("KIRO_CLI_BIN", str(STUB))
os.environ.setdefault("KIRO_STUB_LOGGED_IN", "1")
os.environ.setdefault("KIRO_STUB_DELAY", "0")
os.environ.setdefault("KIRO_CLI_WORKSPACE", "/tmp")
os.environ.setdefault("KIRO_API_MAX_CONCURRENCY", "4")
os.environ.setdefault("KIRO_API_TIMEOUT_SHORT", "15")
_ensure_stub_executable()


@pytest.fixture(scope="session")
def stub_path() -> Path:
    return STUB


@pytest.fixture
def client():
    """A FastAPI TestClient with the app lifespan (reaper) active."""
    _ensure_stub_executable()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_auth():
    """Ensure each test starts with auth disabled (some tests toggle it)."""
    from app.config import config
    config.auth_key = ""
    yield
    config.auth_key = ""
