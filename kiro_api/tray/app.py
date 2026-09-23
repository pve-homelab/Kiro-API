"""Read-only system-tray companion.

A thin monitor for the running service — it does NOT control it. It:
  * polls /stats to compute a status dot (red/yellow/orange/green) + reason,
  * offers copy-to-clipboard for every endpoint URL (OpenAI, Anthropic, ACP,
    health, stats),
  * shows auth state and worker counts.

Requires the optional [tray] extra (pystray, Pillow, requests). Kept entirely
separate from the core service so the service has zero GUI dependencies.
"""
from __future__ import annotations

import os
import sys
import threading
import time

API_HOST = os.environ.get("KIRO_API_ADVERTISED_HOST") or os.environ.get("KIRO_API_HOST", "127.0.0.1")
if API_HOST in {"0.0.0.0", "::", ""}:
    API_HOST = "localhost"
API_PORT = int(os.environ.get("KIRO_API_PORT", "8787"))
STATS_URL = f"http://{API_HOST}:{API_PORT}/stats"

_COLORS = {
    "red": (0xE7, 0x4C, 0x3C),
    "yellow": (0xF1, 0xC4, 0x0F),
    "orange": (0xE6, 0x7E, 0x22),
    "green": (0x2E, 0xCC, 0x71),
}


def _poll() -> tuple[str, str, dict]:
    import requests
    try:
        r = requests.get(STATS_URL, timeout=3)
    except requests.RequestException:
        return "red", "service not running", {}
    if r.status_code != 200:
        return "orange", f"HTTP {r.status_code}", {}
    snap = r.json()
    auth = snap.get("auth", {})
    pool = snap.get("pool", {})
    if not auth.get("logged_in"):
        return "yellow", f"not logged in ({auth.get('detail', '')})", snap
    if pool.get("workers_busy", 0) >= pool.get("max_workers", 1):
        return "orange", "at capacity", snap
    return "green", "online · healthy", snap


def _icon_image(color: str):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, 56, 56], fill=_COLORS.get(color, _COLORS["red"]))
    return img


def _copy(text: str) -> None:
    try:
        import subprocess
        for tool in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b"]):
            try:
                subprocess.run(tool, input=text.encode(), timeout=3, check=True)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
    except Exception:
        pass


def main() -> int:
    try:
        import pystray
    except ImportError:
        print("tray requires the [tray] extra: pip install 'kiro-api[tray]'", file=sys.stderr)
        return 1

    state = {"color": "red", "reason": "starting", "snap": {}}

    def build_menu():
        import pystray
        snap = state["snap"]
        urls = snap.get("urls", {})
        items = [
            pystray.MenuItem(f"● {state['color'].upper()}: {state['reason']}", None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
        for name, url in urls.items():
            items.append(pystray.MenuItem(f"Copy {name}: {url}",
                                          (lambda u: (lambda icon, item: _copy(u)))(url)))
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
        ]
        return pystray.Menu(*items)

    icon = pystray.Icon("kiro-api", _icon_image("red"), "Kiro-API", menu=build_menu())

    def updater():
        while True:
            color, reason, snap = _poll()
            state.update(color=color, reason=reason, snap=snap)
            try:
                icon.icon = _icon_image(color)
                icon.title = f"Kiro-API: {reason}"
                icon.menu = build_menu()
            except Exception:
                pass
            time.sleep(3)

    threading.Thread(target=updater, daemon=True).start()
    icon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
