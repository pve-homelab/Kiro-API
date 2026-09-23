"""Test fixtures. Wires the ACP stub as `kiro-cli` so the whole stack runs
without real auth or network."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STUB = REPO / "scripts" / "kiro-cli-acp-stub.py"


@pytest.fixture
def stub_bin() -> str:
    """A shell-invocable 'kiro-cli' that runs our stub via the current Python.

    We return a small wrapper script path so argv[0] semantics match a real
    binary (the worker calls it with subcommands).
    """
    wrapper = REPO / "scripts" / "_kiro_cli_stub_wrapper.sh"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nexec "{sys.executable}" "{STUB}" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o755)
    return str(wrapper)


@pytest.fixture
def config(stub_bin, monkeypatch):
    from kiro_api.config import Config
    monkeypatch.setenv("KIRO_CLI_BIN", stub_bin)
    cfg = Config()
    cfg.kiro_cli_bin = stub_bin
    cfg.min_workers = 0
    cfg.max_workers = 4
    cfg.auth_poll_interval = 1
    return cfg
