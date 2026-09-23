"""Structured logging to stdout/journald. Text (default) or JSON.

Under systemd, stdout is captured by the journal, so we log to stdout and let
journald handle rotation/persistence. Request IDs and worker tags are attached
via `extra=` and rendered by both formatters.
"""
from __future__ import annotations

import json
import logging
import sys
import time

_EXTRA_KEYS = ("request_id", "worker", "component")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _EXTRA_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        tags = []
        for key in _EXTRA_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                tags.append(f"{key}={val}")
        return f"{base} [{' '.join(tags)}]" if tags else base


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Quiet noisy libraries.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
