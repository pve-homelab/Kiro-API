"""Read/write the project's .env file.

The tray agent is the human control surface for the stack. Port and address
changes are applied by rewriting .env and restarting the container (uvicorn
cannot cleanly rebind its listen port in-process), per the design decision that
a sub-second restart is acceptable.

This is a minimal, comment-preserving .env editor — it updates existing keys in
place and appends new ones, leaving comments and ordering intact.
"""
from __future__ import annotations

from pathlib import Path


class EnvFile:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if not self.path.exists():
            return values
        for line in self.path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, _, val = s.partition("=")
            values[key.strip()] = val.strip()
        return values

    def get(self, key: str, default: str = "") -> str:
        return self.read().get(key, default)

    def set_many(self, updates: dict[str, str]) -> None:
        """Update keys in place; append any that don't exist. Preserves comments."""
        lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        remaining = dict(updates)
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    out.append(f"{key}={remaining.pop(key)}")
                    continue
            out.append(line)
        for key, val in remaining.items():
            out.append(f"{key}={val}")
        self.path.write_text("\n".join(out) + "\n", encoding="utf-8")
