#!/usr/bin/env bash
# Launch the Kiro-API host tray agent.
#
# One-time setup (host):
#   sudo apt-get install -y python3-venv python3-tk gir1.2-appindicator3-0.1 \
#        xclip libnotify-bin        # (wl-clipboard on Wayland instead of xclip)
#   cd <project> && python3 -m venv tray/.venv
#   tray/.venv/bin/pip install -r tray/requirements.txt
#
# Then run:  tray/run-tray.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PY="tray/.venv/bin/python3"
[[ -x "$PY" ]] || PY="python3"

exec "$PY" -m tray.app
