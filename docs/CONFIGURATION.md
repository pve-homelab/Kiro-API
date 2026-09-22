# Configuration & API reference

## Environment variables

All configuration is via environment variables (compose reads them from `.env`).

### Network

| Variable | Default | Notes |
|----------|---------|-------|
| `KIRO_API_HOST` | `127.0.0.1` (host) / `0.0.0.0` (in container) | Bind host |
| `KIRO_API_PORT` | `8787` | `/v1` API port |
| `KIRO_DASHBOARD_PORT` | `8788` | Dashboard port |
| `KIRO_API_ADVERTISED_HOST` | `localhost` | Host shown to clients / in dashboard |

### Auth

| Variable | Default | Notes |
|----------|---------|-------|
| `KIRO_API_AUTH_KEY` | *(empty)* | Empty = no auth. Set = require `Authorization: Bearer <key>` |

### Concurrency

| Variable | Default | Notes |
|----------|---------|-------|
| `KIRO_API_MAX_CONCURRENCY` | `8` | Max parallel `kiro-cli` jobs |
| `KIRO_API_MAX_QUEUE` | `0` | Queue depth for backpressure; 0 = unlimited |
| `KIRO_API_REJECT_WHEN_BUSY` | `false` | `true` → 429 when queue full instead of waiting |

### Timeout tiers

| Variable | Default | Notes |
|----------|---------|-------|
| `KIRO_API_TIMEOUT_SHORT` | `120` | Seconds; default request tier |
| `KIRO_API_TIMEOUT_LONG` | `900` | Seconds; long tier (`X-Kiro-Long: 1`) |

### Model & CLI

| Variable | Default | Notes |
|----------|---------|-------|
| `KIRO_API_DEFAULT_MODEL` | `auto` | Passed to `kiro-cli --model` when not `auto` |
| `KIRO_CLI_BIN` | `/usr/local/bin/kiro-cli` | CLI path inside the container |
| `KIRO_CLI_WORKSPACE` | `/work` | Working dir for CLI runs |

### Host mounts (compose)

| Variable | Default | Notes |
|----------|---------|-------|
| `HOST_KIRO_CLI_BIN` | `/usr/local/bin/kiro-cli` | Host binary (standard config) |
| `HOST_KIRO_CONFIG_DIR` | `${HOME}/.kiro` | Host kiro-cli config/creds |
| `HOST_AWS_DIR` | `${HOME}/.aws` | Host AWS dir (if used for auth) |
| `PYTHON_IMAGE` | `python:3.12-slim` | Override base / point at a mirror |
| `OFFLINE` | `0` | `1` = install Python deps from `wheelhouse/` |

---

## HTTP endpoints

### OpenAI-compatible

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | Chat completion; `stream: true` for SSE |
| POST | `/v1/completions` | Legacy text completion |
| GET | `/v1/models` | List available model IDs |

### Control

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Status, login, pool + reaper stats |
| GET | `/stats` | Live snapshot (pool + reaper) |
| WS | `/ws/stats` | Stats pushed every 2s (used by dashboard) |
| GET | `/` , `/dashboard` | Dashboard HTML |

### Admin (used by the tray)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/config` | Effective config |
| POST | `/admin/auth-key` | `{"key": "..."}` — set/clear auth (live) |
| POST | `/admin/login` | Start `kiro-cli login --use-device-flow` |
| GET | `/admin/login/status` | Poll login output/return code |

---

## OpenAI compatibility matrix

| Feature | Supported |
|---------|-----------|
| `/v1/chat/completions` sync | Yes |
| `/v1/chat/completions` stream (SSE) | Yes |
| `/v1/completions` | Yes |
| `/v1/models` | Yes |
| `response_format` JSON | Yes (strips markdown fences) |
| Final stream `usage` chunk | Yes |
| `Authorization: Bearer` | Yes (optional) |
| `X-Kiro-Long` long-timeout hint | Yes |
| `temperature` / `max_tokens` | Ignored (CLI has no equivalent) |
| Tool / function calling | No |

Token counts in `usage` are estimates (~4 chars/token) until kiro-cli exposes
real billing usage.
