"""Serves the slim live dashboard as a single self-contained HTML page.

No Node build step, no CDN — the HTML/CSS/JS is inlined so it works in a fully
airgapped environment. The page connects to the API's /ws/stats WebSocket and
falls back to polling /stats if the socket drops.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import HTMLResponse

_HTML_PATH = Path(__file__).parent / "static" / "dashboard.html"


def dashboard_html() -> HTMLResponse:
    html = _HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(html)
