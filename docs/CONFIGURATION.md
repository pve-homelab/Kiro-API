# Configuration & API Reference

Everything you can tune, and every endpoint the service exposes.

- [Configuration precedence](#configuration-precedence)
- [Environment variables](#environment-variables)
- [CLI flags](#cli-flags)
- [Config file](#config-file)
- [Endpoints](#endpoints)
- [Request headers](#request-headers)
- [Error shapes](#error-shapes)

---

## Configuration precedence

Settings are resolved in this order (highest wins):

**CLI flags > environment variables > config file > built-in defaults**

Inspect the effective configuration at any time:

```bash
kiro-api config
```

---

## Environment variables

### Network

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_HOST` | `127.0.0.1` | Bind address. Any IP: `127.0.0.1`, a LAN IP, or `0.0.0.0` for all interfaces. |
| `KIRO_API_PORT` | `8787` | Bind port. |
| `KIRO_API_ADVERTISED_HOST` | *(auto)* | Host shown to clients in `/stats` and the tray. Defaults to the bind host (`0.0.0.0` → `localhost`). |

### Auth (bridge key)

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_AUTH_KEY` | *(empty)* | Optional API key clients must send (`Authorization: Bearer <key>` or `x-api-key`). Empty = no auth. This is a gateway key; it is unrelated to your Kiro login. |

### Worker pool (concurrency)

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_MAX_WORKERS` | `8` | Maximum concurrent workers = the concurrency ceiling. Raise toward `100` for stress testing. |
| `KIRO_API_MIN_WORKERS` | `0` | Workers pre-warmed at startup (kept alive even when idle). |
| `KIRO_API_WORKER_IDLE_TIMEOUT` | `300` | Seconds an idle worker is kept before being retired (down to `min_workers`). |
| `KIRO_API_MAX_QUEUE` | `256` | Max requests that may wait for a busy pool before new ones are rejected with `503`/`429`. |

### Timeouts

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_TIMEOUT_SHORT` | `120` | Turn timeout (seconds) for normal requests; also the idle (no-output) ceiling. |
| `KIRO_API_TIMEOUT_LONG` | `900` | Turn timeout for long-running requests (model contains `long`, or `X-Kiro-Long: true`). |
| `KIRO_API_SSE_KEEPALIVE` | `15` | Seconds between SSE keepalive frames during upstream silence. `0` disables. |

### Auth watchdog

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_AUTH_REFRESH_MARGIN` | `300` | Refresh the token when it expires within this many seconds. |
| `KIRO_API_AUTH_POLL_INTERVAL` | `30` | How often the watchdog re-checks auth state. |

### Model & kiro-cli

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_DEFAULT_MODEL` | `auto` | Model used when a request does not specify one. |
| `KIRO_CLI_BIN` | `kiro-cli` | Path to the `kiro-cli` binary (override if not on `$PATH`). |
| `KIRO_ACP_ENGINE` | `v2` | `kiro-cli acp` engine pin. Keep `v2` (v3 needs host-mediated auth not implemented). |
| `KIRO_ACP_WORKSPACE` | *(process cwd)* | Fallback working directory for sessions. |
| `KIRO_ACP_TRUST_TOOLS` | `true` | Auto-approve `kiro-cli`'s built-in tool runs (file edits, commands). `false` = answer-only. |
| `KIRO_ACP_SURFACE_THINKING` | `true` | Surface reasoning/tool activity in each API's native reasoning shape. |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_LOG_FORMAT` | `text` | `text` or `json` (JSON is recommended under systemd/journald). |
| `KIRO_API_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |

---

## CLI flags

`serve` accepts overrides that beat env and file:

| Flag | Maps to | Notes |
|------|---------|-------|
| `-H`, `--host` | `KIRO_API_HOST` | Any IP. |
| `-p`, `--port` | `KIRO_API_PORT` | |
| `--workers` | `KIRO_API_MAX_WORKERS` | Concurrency ceiling. |
| `--min-workers` | `KIRO_API_MIN_WORKERS` | |
| `--auth-key` | `KIRO_API_AUTH_KEY` | |
| `--model` | `KIRO_API_DEFAULT_MODEL` | |
| `--log-format` | `KIRO_API_LOG_FORMAT` | |
| `--log-level` | `KIRO_API_LOG_LEVEL` | Available on every subcommand. |
| `--config-file` | — | Path to a config file (default `~/.config/kiro-api/config.env`). |

Other subcommands: `login`, `status`, `stats [--json]`, `config`, `models`,
`acp`, `install-service [--user] [--uninstall]`, `version`. `--help` works
top-level and per subcommand.

---

## Config file

A simple `KEY=VALUE` file (same names as the env vars). Default location:

```
~/.config/kiro-api/config.env
```

Example:

```ini
KIRO_API_HOST=0.0.0.0
KIRO_API_PORT=8787
KIRO_API_MAX_WORKERS=32
KIRO_API_AUTH_KEY=change-me
KIRO_API_LOG_FORMAT=json
```

Environment variables override file values; CLI flags override both.

---

## Endpoints

### OpenAI-compatible

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | Chat completions (stream + non-stream). |
| POST | `/v1/responses` | Responses API (non-streaming aggregate). |
| GET | `/v1/models` | Live model catalogue from `kiro-cli`. |
| GET | `/v1/models/{id}` | Single model object. |

### Anthropic-compatible

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/messages` | Messages (stream + non-stream). |
| POST | `/v1/messages/count_tokens` | Local token estimate. |

### Native ACP

| Method | Path | Description |
|--------|------|-------------|
| POST | `/acp/chat` | Aggregated turn with structured events. |
| POST | `/acp/chat/stream` | SSE stream of normalized events + keepalives. |

### Control

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness — process is up. |
| GET | `/ready` | Readiness — logged in **and** a worker is available (200/503). |
| GET | `/stats` | Metrics snapshot (auth, pool, endpoints). |
| WS | `/ws/stats` | Live metrics stream (used by the tray). |
| POST | `/admin/login` | Trigger device-flow login. |
| GET | `/admin/login/status` | Poll login output (URL + code). |

---

## Request headers

| Header | Purpose |
|--------|---------|
| `Authorization: Bearer <key>` / `x-api-key: <key>` | Bridge auth (when `KIRO_API_AUTH_KEY` is set). |
| `X-Kiro-Long: true` | Treat this request as long-running (use the long timeout tier). |
| `X-Kiro-Workspace: /abs/path` | Working directory for the session (for tool calls). |
| `X-Kiro-MCP-Servers: <json>` | MCP servers to register for this session (forwarded to `kiro-cli`). |

---

## Error shapes

Upstream failures are classified and returned in the caller's native envelope:

| Condition | HTTP | OpenAI `type` | Anthropic `type` |
|-----------|------|---------------|------------------|
| Rate limit / throttle / quota | 429 | `rate_limit_error` | `rate_limit_error` |
| Overloaded / no worker / capacity | 503 | `server_error` | `overloaded_error` |
| Timeout / deadline | 504 | `server_error` | `api_error` |
| Not logged in / auth expired | 401 | `authentication_error` | `authentication_error` |
| Any other failure | 502 | `server_error` | `api_error` |

A `Retry-After` header is added when the upstream message carries a retry hint
(most relevant for `429`). In streaming responses the error is delivered as a
terminal error event followed by `[DONE]`.
