# Dependencies

Kiro-API V3 is intentionally lean — a small, well-known runtime footprint suited
to a long-running service.

## Runtime (required)

| Package | Version | Why |
|---------|---------|-----|
| `fastapi` | `>=0.110` | HTTP framework for the OpenAI/Anthropic/ACP routes. |
| `uvicorn[standard]` | `>=0.27` | ASGI server (the `standard` extra adds fast HTTP/WebSocket support). |
| `pydantic` | `>=2.6` | Request/response models and validation (pulled in by FastAPI; pinned explicitly). |

Install with either:

```bash
pip install -r requirements.txt   # runtime only
pip install -e .                  # the package (recommended; installs the `kiro-api` command)
```

The service also uses only the Python **standard library** for logging,
`sd_notify` (systemd readiness/watchdog is a plain socket write — no external
dep), subprocess management, and token-cache discovery.

## System requirements

| Requirement | Notes |
|-------------|-------|
| **Python** | 3.11 or newer (validated on 3.14). |
| **Kiro CLI** (`kiro-cli`) | Installed and on `$PATH`. Not a pip package — install from [kiro.dev](https://kiro.dev). This is what the service drives. |
| **Linux + systemd** | Required only for `install-service`. The service itself runs anywhere Python does. |

## Optional extras

### Tray companion — `pip install "kiro-api[tray]"`

| Package | Version | Why |
|---------|---------|-----|
| `pystray` | `>=0.19` | System-tray icon. |
| `Pillow` | `>=10.0` | Renders the status-dot icon. |
| `requests` | `>=2.31` | Polls `/stats`. |

Kept as an extra so the core service has **zero GUI dependencies**. Requires a
desktop session (X11/Wayland).

### Development — `pip install "kiro-api[dev]"`

| Package | Version | Why |
|---------|---------|-----|
| `pytest` | `>=8.0` | Test runner. |
| `pytest-asyncio` | `>=0.23` | Async test support. |
| `httpx` | `>=0.27` | ASGI test client for the API tests. |
| `ruff` | `>=0.5` | Lint + import sorting. |

## Dependency policy

- Pinned with lower bounds, no exotic packages, no bleeding-edge interpreter
  requirement — appropriate for a service that runs constantly.
- The core install has three direct dependencies; everything else is standard
  library. This keeps the attack surface and upgrade burden small.
