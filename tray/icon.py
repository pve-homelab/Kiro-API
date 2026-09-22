"""Kiro ghost tray icon + status-dot overlay generation.

The base ghost is drawn with Pillow so there are no binary assets to ship and
it works fully offline. `make_icon(state)` returns a PIL image with the ghost
plus a colored status dot in the lower-right:

    red    = API not running
    yellow = running but not logged in to kiro-cli
    orange = error (check dashboard)
    green  = online and healthy

If a real logo file exists at tray/assets/kiro-ghost.png it is used instead of
the drawn ghost, so you can drop in a nicer asset later.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 64
ASSET = Path(__file__).parent / "assets" / "kiro-ghost.png"

DOT_COLORS = {
    "red": (235, 87, 87, 255),
    "yellow": (242, 201, 76, 255),
    "orange": (242, 153, 74, 255),
    "green": (53, 208, 127, 255),
    "grey": (139, 139, 160, 255),
}

_GHOST_BODY = (160, 107, 255, 255)   # Kiro purple
_GHOST_EYE = (15, 15, 20, 255)
_WHITE = (245, 245, 250, 255)


def _draw_ghost(size: int = SIZE) -> Image.Image:
    """Draw a simple rounded ghost with a wavy bottom and two eyes."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    m = size // 8          # margin
    w = size - 2 * m       # body width
    top = m
    body_bottom = size - m - w // 6

    # Rounded head + rectangular body
    d.rounded_rectangle([m, top, m + w, body_bottom], radius=w // 2, fill=_GHOST_BODY)
    d.rectangle([m, top + w // 2, m + w, body_bottom], fill=_GHOST_BODY)

    # Wavy feet (three bumps)
    bumps = 3
    bump_w = w / bumps
    for i in range(bumps):
        x0 = m + i * bump_w
        x1 = x0 + bump_w
        d.pieslice(
            [x0, body_bottom - bump_w / 2, x1, body_bottom + bump_w / 2],
            start=0, end=180, fill=_GHOST_BODY,
        )

    # Eyes
    eye_r = max(2, size // 14)
    eye_y = top + w // 2 - eye_r
    left_x = m + w // 3
    right_x = m + 2 * w // 3
    for cx in (left_x, right_x):
        d.ellipse([cx - eye_r, eye_y - eye_r, cx + eye_r, eye_y + eye_r], fill=_WHITE)
        d.ellipse(
            [cx - eye_r // 2, eye_y - eye_r // 2, cx + eye_r // 2, eye_y + eye_r // 2],
            fill=_GHOST_EYE,
        )
    return img


def _base_ghost(size: int = SIZE) -> Image.Image:
    if ASSET.exists():
        return Image.open(ASSET).convert("RGBA").resize((size, size), Image.LANCZOS)
    return _draw_ghost(size)


def make_icon(state: str = "grey", size: int = SIZE) -> Image.Image:
    """Return the ghost icon with a status dot overlay."""
    img = _base_ghost(size).copy()
    d = ImageDraw.Draw(img)

    dot_r = size // 5
    cx = size - dot_r - 2
    cy = size - dot_r - 2
    color = DOT_COLORS.get(state, DOT_COLORS["grey"])

    # white ring so the dot reads against any desktop background
    d.ellipse([cx - dot_r - 2, cy - dot_r - 2, cx + dot_r + 2, cy + dot_r + 2], fill=_WHITE)
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=color)
    return img


def save_previews(out_dir: Path) -> list[Path]:
    """Write one PNG per state — used for docs and for verifying the asset."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for state in ("red", "yellow", "orange", "green"):
        p = out_dir / f"tray-{state}.png"
        make_icon(state, 128).save(p)
        paths.append(p)
    # a plain ghost too
    p = out_dir / "kiro-ghost.png"
    _base_ghost(128).save(p)
    paths.append(p)
    return paths


if __name__ == "__main__":
    import sys

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "previews"
    for p in save_previews(out):
        print(p)
