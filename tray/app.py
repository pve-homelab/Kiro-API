"""Kiro-API V2 host system-tray agent.

Runs on the Ubuntu workspace host (not in the container). Draws the Kiro ghost
tray icon with a status dot and provides the control menu:

  * status line: current /v1 address + health
  * API Endpoints ▸  — copy a standard OpenAI endpoint URL to the clipboard
  * Open Dashboard   — open the live dashboard in a browser
  * Login to Kiro CLI — trigger device-flow login
  * Change Port…      — prompt, rewrite .env, restart the container
  * Change Address…   — prompt, rewrite .env, restart the container
  * Auth ▸            — set / clear the bridge API key
  * Restart / Stop
  * Quit

Dialogs use tkinter (stdlib) so there is no extra dependency for prompts.
Clipboard copy prefers tkinter; notifications use `notify-send` if present.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from pystray import MenuItem as Item

from .controller import Controller
from .envfile import EnvFile
from .icon import make_icon

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# Endpoints offered in the "API Endpoints" submenu (path suffixes under /v1).
ENDPOINT_PATHS = {
    "Chat Completions": "/chat/completions",
    "Completions": "/completions",
    "Models": "/models",
}

POLL_INTERVAL = 5.0


class TrayApp:
    def __init__(self) -> None:
        self.env = EnvFile(ENV_PATH)
        self._load_addr()
        self.controller = Controller(api_base=self._api_base())
        self.status_state = "red"
        self.status_label = "starting…"
        self.v1_url = self._v1_url()
        self.dashboard_url = self._dashboard_url()
        self.icon = pystray.Icon(
            "kiro-api",
            icon=make_icon("red"),
            title="Kiro-API",
            menu=self._build_menu(),
        )
        self._stop = threading.Event()

    # -- config helpers ---------------------------------------------------
    def _load_addr(self) -> None:
        env = self.env.read()
        self.host = env.get("KIRO_API_HOST", "127.0.0.1")
        self.advertised = env.get("KIRO_API_ADVERTISED_HOST", "localhost")
        self.port = env.get("KIRO_API_PORT", "8787")
        self.dash_port = env.get("KIRO_DASHBOARD_PORT", "8788")

    def _api_base(self) -> str:
        # The tray talks to the API on the host-published port via localhost.
        return f"http://127.0.0.1:{self.port}"

    def _v1_url(self) -> str:
        return f"http://{self.advertised}:{self.port}/v1"

    def _dashboard_url(self) -> str:
        return f"http://{self.advertised}:{self.dash_port}"

    # -- notifications / clipboard ---------------------------------------
    def _notify(self, message: str, title: str = "Kiro-API") -> None:
        try:
            self.icon.notify(message, title)
            return
        except Exception:
            pass
        if shutil.which("notify-send"):
            try:
                subprocess.Popen(["notify-send", title, message])
            except Exception:
                pass

    def _copy(self, text: str) -> bool:
        # Prefer wl-copy / xclip / xsel; fall back to tkinter clipboard.
        for tool, args in (("wl-copy", ["wl-copy"]), ("xclip", ["xclip", "-selection", "clipboard"]),
                           ("xsel", ["xsel", "--clipboard", "--input"])):
            if shutil.which(tool):
                try:
                    p = subprocess.Popen(args, stdin=subprocess.PIPE)
                    p.communicate(text.encode())
                    return p.returncode == 0
                except Exception:
                    continue
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.after(200, root.destroy)
            return True
        except Exception:
            return False

    def _prompt(self, title: str, prompt: str, initial: str = "") -> str | None:
        try:
            import tkinter as tk
            from tkinter import simpledialog
            root = tk.Tk()
            root.withdraw()
            val = simpledialog.askstring(title, prompt, initialvalue=initial)
            root.destroy()
            return val
        except Exception as exc:  # headless fallback
            self._notify(f"Prompt unavailable: {exc}")
            return None

    # -- menu actions -----------------------------------------------------
    def _copy_endpoint(self, name: str):
        def action(_icon, _item):
            url = self.v1_url + ENDPOINT_PATHS[name]
            if self._copy(url):
                self._notify(f"Copied to clipboard:\n{url}", "Endpoint copied")
            else:
                self._notify(f"Copy failed. URL: {url}")
        return action

    def _open_dashboard(self, _icon, _item):
        webbrowser.open(self.dashboard_url)
        self._notify(f"Opening {self.dashboard_url}")

    def _login(self, _icon, _item):
        ok, _ = self.controller.trigger_login()
        if not ok:
            ok2, msg = self.controller.host_login()
            self._notify(msg if ok2 else f"Login failed: {msg}")
            return
        self._notify("Login started. Approve in your browser if prompted.", "Kiro CLI login")

        def watch():
            for _ in range(30):
                time.sleep(2)
                st = self.controller.login_status()
                if not st.get("running") and st.get("returncode") is not None:
                    ok = st.get("returncode") == 0
                    self._notify("Logged in." if ok else "Login did not complete.",
                                 "Kiro CLI login")
                    return
        threading.Thread(target=watch, daemon=True).start()

    def _change_port(self, _icon, _item):
        val = self._prompt("Change API port", "New /v1 API port:", self.port)
        if not val or not val.strip().isdigit():
            return
        self.env.set_many({"KIRO_API_PORT": val.strip()})
        self._notify(f"Port set to {val.strip()}. Restarting…")
        self._reload_and_restart()

    def _change_address(self, _icon, _item):
        val = self._prompt("Change advertised address",
                           "Advertised host (shown to clients):", self.advertised)
        if not val or not val.strip():
            return
        self.env.set_many({"KIRO_API_ADVERTISED_HOST": val.strip()})
        self._notify(f"Address set to {val.strip()}. Restarting…")
        self._reload_and_restart()

    def _set_auth_key(self, _icon, _item):
        val = self._prompt("Set API key", "Bearer key (blank = disable auth):", "")
        if val is None:
            return
        # Persist to .env and apply live via admin API.
        self.env.set_many({"KIRO_API_AUTH_KEY": val.strip()})
        ok, _ = self.controller.set_auth_key(val.strip())
        self._notify(("Auth enabled." if val.strip() else "Auth disabled.")
                     + ("" if ok else " (restart to apply)"))

    def _restart(self, _icon, _item):
        self._notify("Restarting stack…")
        ok, out = self.controller.restart()
        self._notify("Restarted." if ok else f"Restart failed:\n{out[:200]}")

    def _stop_stack(self, _icon, _item):
        self._notify("Stopping stack…")
        self.controller.down()

    def _quit(self, _icon, _item):
        self._stop.set()
        self.icon.stop()

    def _reload_and_restart(self):
        def worker():
            ok, out = self.controller.restart()
            self._load_addr()
            self.v1_url = self._v1_url()
            self.dashboard_url = self._dashboard_url()
            self.controller.set_api_base(self._api_base())
            self.icon.menu = self._build_menu()
            self._notify("Applied." if ok else f"Restart failed:\n{out[:200]}")
        threading.Thread(target=worker, daemon=True).start()

    # -- menu construction ------------------------------------------------
    def _status_text(self, _item) -> str:
        return f"● {self.status_label}"

    def _addr_text(self, _item) -> str:
        return f"{self.v1_url}"

    def _build_menu(self) -> pystray.Menu:
        endpoints = pystray.Menu(
            *[Item(name, self._copy_endpoint(name)) for name in ENDPOINT_PATHS]
        )
        auth_menu = pystray.Menu(
            Item("Set / change API key…", self._set_auth_key),
            Item("Disable auth", lambda i, x: (self.env.set_many({"KIRO_API_AUTH_KEY": ""}),
                                               self.controller.set_auth_key(""),
                                               self._notify("Auth disabled."))),
        )
        return pystray.Menu(
            Item(self._status_text, None, enabled=False),
            Item(self._addr_text, self._copy_v1_base),
            pystray.Menu.SEPARATOR,
            Item("API Endpoints", endpoints),
            Item("Open Dashboard", self._open_dashboard),
            pystray.Menu.SEPARATOR,
            Item("Login to Kiro CLI", self._login),
            Item("Change Port…", self._change_port),
            Item("Change Address…", self._change_address),
            Item("Auth", auth_menu),
            pystray.Menu.SEPARATOR,
            Item("Restart", self._restart),
            Item("Stop", self._stop_stack),
            Item("Quit", self._quit),
        )

    def _copy_v1_base(self, _icon, _item):
        if self._copy(self.v1_url):
            self._notify(f"Copied:\n{self.v1_url}", "Base URL copied")

    # -- polling loop -----------------------------------------------------
    def _poll_loop(self):
        while not self._stop.is_set():
            st = self.controller.poll()
            changed = st.state != self.status_state
            self.status_state = st.state
            self.status_label = st.label
            if st.v1_url:
                self.v1_url = st.v1_url
            if st.dashboard_url:
                self.dashboard_url = st.dashboard_url
            try:
                self.icon.icon = make_icon(st.state)
                self.icon.title = f"Kiro-API · {st.label}"
                if changed:
                    self.icon.update_menu()
            except Exception:
                pass
            self._stop.wait(POLL_INTERVAL)

    def run(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.icon.run()


def main():
    TrayApp().run()


if __name__ == "__main__":
    main()
