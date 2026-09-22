"""Unit tests for the tray .env editor."""
from __future__ import annotations

from tray.envfile import EnvFile


def test_read_values(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nB=two\n", encoding="utf-8")
    env = EnvFile(p)
    assert env.get("A") == "1"
    assert env.get("B") == "two"
    assert env.get("MISSING", "def") == "def"


def test_update_in_place_preserves_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# header\nA=1\n# mid\nB=2\n", encoding="utf-8")
    env = EnvFile(p)
    env.set_many({"A": "9"})
    txt = p.read_text(encoding="utf-8")
    assert "A=9" in txt
    assert "# header" in txt and "# mid" in txt
    assert "B=2" in txt


def test_append_new_key(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n", encoding="utf-8")
    env = EnvFile(p)
    env.set_many({"NEW": "val"})
    assert "NEW=val" in p.read_text(encoding="utf-8")


def test_missing_file_read_returns_empty(tmp_path):
    env = EnvFile(tmp_path / "nope.env")
    assert env.read() == {}
