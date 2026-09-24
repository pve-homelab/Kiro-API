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
| `KIRO_API_AUTH_KEY` | *(empty)* | Optional API key clients must send (`Authorization: Bearer <key>` or `x-api-key`). Empty = no auth. This is a gateway key; it is unrelated to your Kiro login. Compared in constant time. |

### Security limits

| Variable | Default | Description |
|----------|---------|-------------|
| `KIRO_API_MAX_BODY_BYTES` | `8388608` (8 MiB) | Reject request bodies larger than this with `413`. Raise for image-heavy multimodal payloads. |
| `KIRO_API_RATE_LIMIT` | `0` (off) | Max requests per window per client (by `X-Forwarded-For` first hop, else peer IP). `429` + `Retry-After` when exceeded. |
| `KIRO_API_RATE_LIMIT_WINDOW` | `60` | Rate-limit window in seconds. |

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
| `KIRO_ACP_EFFORT` | *(unset)* | Service-wide default reasoning effort: `low`/`medium`/`high`/`xhigh`/`max`. Overridden per-request by `X-Kiro-Effort` header or body field. |
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

### Changing saved settings

| Command | Effect |
|---------|--------|
| `kiro-api set-host <ip>` | Persist the bind address (e.g. `0.0.0.0`, a LAN IP, `localhost`). |
| `kiro-api set-port <port>` | Persist the port. |
| `kiro-api config set <key> <value>` | Persist any settable key (`host`, `port`, `advertised_host`, `auth_key`, `max_workers`, `min_workers`, `max_queue`, `default_model`, `kiro_cli_bin`, `log_format`, `log_level`). |
| `kiro-api config get <key>` | Print the effective value of one setting. |
| `kiro-api config` | Print the full effective configuration (JSON). |
| `kiro-api config path` | Print the config file location. |

These write to the config file (default `~/.config/kiro-api/config.env`); restart
the service to apply. Environment variables and `serve` flags still override saved
values at runtime.

Other subcommands: `login`, `doctor`, `status`, `stats [--json]`, `models`, `acp`,
`install-service [--user] [--uninstall]`, `version`. `--help` works top-level and
per subcommand.

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
| GET | `/stats` | Metrics snapshot as JSON (auth, pool, endpoints). |
| GET | `/metrics` | Prometheus text-exposition metrics (counters + gauges). |
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
| `X-Kiro-Effort: <level>` | Reasoning effort for this turn: `low`/`medium`/`high`/`xhigh`/`max`. |

### Reasoning effort

Set per request (precedence: header > body). The service maps it to `kiro-cli`'s
`/effort` control for that session; unknown levels are ignored (the turn still
runs).

| Source | Field |
|--------|-------|
| Header | `X-Kiro-Effort: high` |
| OpenAI | `"reasoning_effort": "high"` (or `"reasoning": {"effort": "high"}`) |
| Anthropic | `"thinking": {"type": "enabled", "budget_tokens": N}` → mapped to a level by budget |

Levels: `low`, `medium`, `high`, `xhigh`, `max` (OpenAI `minimal` → `low`). A
service-wide default can be set with `KIRO_ACP_EFFORT`.

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

---

## Security & trust model

Kiro-API V3 is designed to run on a **trusted host** — your dev box or a
controlled server — for clients you operate (your agent fleet). Its built-in
protections are:

- **Bridge auth key** (`KIRO_API_AUTH_KEY`), compared in constant time.
- **Body-size limit** and optional **per-client rate limiting**.
- **Localhost-only bind by default** (`127.0.0.1`).

It intentionally does **not** implement TLS, user accounts, or OAuth — that is
the job of the layer in front of it. If you expose it beyond localhost:

1. **Bind to a specific interface**, not `0.0.0.0`, when you can
   (`kiro-api set-host <lan-ip>`).
2. **Always set `KIRO_API_AUTH_KEY`** when binding to a non-loopback address.
3. **Terminate TLS with a reverse proxy** (nginx, Caddy, Traefik) in front of the
   service, e.g. `https://kiro.internal → http://127.0.0.1:8787`. Set the proxy
   to forward `X-Forwarded-For` so per-client rate limiting sees the real client.
4. **Restrict network access** with a firewall / security group to known clients.
5. Treat the bridge key as a secret (env or config file with `600` perms), and
   rotate it with `kiro-api config set auth_key <new>` + restart.

The service talks to Kiro only through the official `kiro-cli` and never handles
your Kiro credentials directly.
