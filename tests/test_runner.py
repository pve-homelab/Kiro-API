"""Unit tests for the runner's command construction and timeout tiers."""
from __future__ import annotations

from app.runner import KiroRunner


def test_build_cmd_default_model():
    r = KiroRunner()
    cmd = r._build_cmd("auto")
    assert cmd[-2:] == ["chat", "--no-interactive"]
    assert "--model" not in cmd


def test_build_cmd_named_model():
    r = KiroRunner()
    cmd = r._build_cmd("some-model")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "some-model"


def test_timeout_tiers():
    r = KiroRunner()
    short = r._timeout_for(False)
    long = r._timeout_for(True)
    assert long >= short
