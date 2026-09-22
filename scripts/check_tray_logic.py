#!/usr/bin/env python3
"""Headless test of tray logic (no display needed).

Verifies:
  * EnvFile updates keys in place and preserves comments.
  * icon.make_icon returns a valid PIL image for each state.
  * Controller.poll maps a live /health response to the right status state.

Controller.poll is only checked if an API base is passed as argv[1].
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tray.envfile import EnvFile  # noqa: E402
from tray.icon import make_icon    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# --- EnvFile ---
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / ".env"
    p.write_text("# comment\nKIRO_API_PORT=8787\n# another\nKIRO_API_AUTH_KEY=\n", encoding="utf-8")
    env = EnvFile(p)
    check("env read port", env.get("KIRO_API_PORT") == "8787", env.get("KIRO_API_PORT"))
    env.set_many({"KIRO_API_PORT": "9999", "KIRO_API_ADVERTISED_HOST": "localhost"})
    txt = p.read_text(encoding="utf-8")
    check("env update in place", "KIRO_API_PORT=9999" in txt)
    check("env preserves comments", "# comment" in txt and "# another" in txt)
    check("env appends new key", "KIRO_API_ADVERTISED_HOST=localhost" in txt)
    check("env old port gone", "8787" not in txt)

# --- icon ---
for state in ("red", "yellow", "orange", "green", "grey"):
    img = make_icon(state)
    check(f"icon {state}", img.size == (64, 64) and img.mode == "RGBA")

# --- controller (optional, needs running API) ---
if len(sys.argv) > 1:
    from tray.controller import Controller
    c = Controller(api_base=sys.argv[1])
    st = c.poll()
    check("controller poll state", st.state in {"red", "yellow", "orange", "green"}, st.state)
    check("controller poll label", bool(st.label), st.label)

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("TRAY LOGIC CHECKS PASSED")
