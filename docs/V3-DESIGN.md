# Kiro-API V3 — Design Document

**Status:** design → implementation
**Target:** a CLI-driven, systemd-installable Linux service (no Docker) that exposes a locally-authenticated `kiro-cli` as OpenAI-, Anthropic-, and ACP-compatible HTTP APIs, built for semi-production development workloads: ~10 concurrent agents typical, scalable to ~100 for stress testing, mixing long-running and short sessions.

---

## 1. Goals and non-goals

### Goals
- **A real Linux service.** Installed and supervised by systemd, auto-restart on failure, survives reboots, self-heals from transient faults.
- **Fully CLI-driven.** `kiro-api <command>` with `--help`; the CLI is both the operator interface and the service entrypoint.
- **Self-correcting state.** The #1 lesson from V2: never get *stuck* — not on an expired token, not on a dead worker, not on a bad config. Every failure mode has a detector and a recovery path.
- **Protocol breadth** (adopted from kiro-gateway, re-implemented): OpenAI (`/v1/chat/completions`, `/v1/responses`, `/v1/models`), Anthropic (`/v1/messages`, `/v1/messages/count_tokens`), native ACP (`/acp/chat`, `/acp/chat/stream`), MCP passthrough, real incremental streaming with keepalives, reasoning/tool-activity surfacing, native error shapes.
- **Concurrency that scales both ways.** Elastic worker pool: cheap at ~10, capable of ~100, configurable via CLI/env/config.
- **Configurable networking that never crashes.** Any host IP (not just localhost) and any port; a bad bind fails loud and clean, not with a wedged process.
- **Single AWS/Kiro account, many workers.** No multi-account pooling.
- **Thin read-only tray companion.** Status dots + reason + copy-paste endpoints for all protocols. No control surface (except an optional Login trigger later).

### Non-goals
- No Docker (V3 is bare-metal + systemd).
- No account pooling / no circumventing Kiro rate limits.
- No web dashboard (replaced by CLI `status`/`stats` + journald + tray).
- macOS/launchd is out of scope for now (Linux only).

---

## 2. The core architecture problem and its solution

Two facts are in tension:

1. **Real incremental streaming** (token deltas, reasoning, tool events, MCP) requires the **ACP** interface — a long-lived `kiro-cli acp` subprocess speaking JSON-RPC over stdio. This is how kiro-gateway streams.
2. **~100 parallel agents** cannot go through a *single* long-lived process: one stdio pipe + one write lock serializes everything, and one stall takes down all sessions. That is kiro-gateway's structural ceiling.

**V3's answer: a pool of long-lived ACP workers.**

```
                         ┌────────────────────────────────────────────┐
   HTTP clients          │                Kiro-API V3                  │
   (Herdr → pi/omp/      │                                             │
    Hermes, Cursor,      │   ┌───────────────┐   ┌──────────────────┐  │
    Claude Code, …)      │   │  HTTP layer   │   │  AuthManager     │  │
        │                │   │  OpenAI shim  │   │  - discovery     │  │
        │  OpenAI /      │   │  Anthropic    │   │  - expiry watch  │  │
        │  Anthropic /   │   │  ACP native   │   │  - device login  │  │
        │  ACP           │   │  MCP passthru │   └────────┬─────────┘  │
        ▼                │   └───────┬───────┘            │            │
   ┌─────────┐           │           │ normalized turn    │ creds state│
   │  uvicorn│───────────┼──────────▶│                    ▼            │
   └─────────┘           │   ┌───────▼─────────────────────────────┐  │
                         │   │           Scheduler                  │  │
                         │   │  queue + backpressure + fair pick    │  │
                         │   └───────┬──────────────────────────────┘  │
                         │           │ lease a worker                   │
                         │   ┌───────▼───────────────────────────────┐ │
                         │   │            WorkerPool (elastic)        │ │
                         │   │  W1   W2   W3  … Wn   (each = one      │ │
                         │   │  ┌──┐ ┌──┐ ┌──┐     `kiro-cli acp`     │ │
                         │   │  │  │ │  │ │  │       subprocess)      │ │
                         │   │  └──┘ └──┘ └──┘                        │ │
                         │   │  health-checked, restarted, reaped     │ │
                         │   └────────────────────────────────────────┘│
                         │   ┌────────────────────────────────────────┐│
                         │   │  Supervisor/Reaper: worker liveness,    ││
                         │   │  leak watch, systemd watchdog ping      ││
                         │   └────────────────────────────────────────┘│
                         └────────────────────────────────────────────┘
   Tray companion (separate process) ── polls /health, /stats ──▶ dots + copy endpoints
```

- **Each worker** is one `kiro-cli acp` subprocess (long-lived) wrapping the ACP client logic: `initialize` once, then `session/new` per request, `session/prompt`, `session/cancel` on disconnect. This gives streaming, reasoning, tool events, and MCP registration.
- **The pool** runs N workers → N-way true parallelism. Each worker handles **one active turn at a time** (conservative default) so a heavy turn can't head-of-line-block anything but itself. (Optionally >1 session per worker later, gated by measurement.)
- **The scheduler** owns a bounded queue: a request leases a free worker or waits; when the queue is full it returns a native-shaped 429/503 with `Retry-After`. This is the backpressure valve that keeps the service responsive under overload instead of collapsing.

This is deliberately more than either project: kiro-gateway's streaming worker, V2's process-pool parallelism, joined by a scheduler.

### 2.1 Elastic sizing (cheap at 10, capable at 100)
- Config: `min_workers` (default `0`), `max_workers` (default `8`), `worker_idle_timeout` (default `300s`).
- Workers are **spawned lazily** up to `max_workers` as demand arrives, and **retired** after `worker_idle_timeout` back down to `min_workers`. So the ~10-agent case runs a handful of workers; the stress test sets `--workers 100` and the pool grows to match.
- `max_workers` is settable via CLI (`--workers`), env (`KIRO_API_MAX_WORKERS`), or config file.
- **Honesty about the ceiling:** even at 100 workers, Kiro's single-account rate limits bound real throughput. The service surfaces upstream 429s natively and backs off; it does not pretend to have infinite capacity.

---

## 3. Self-healing state (the anti-"stuck" design)

V2's worst failures were *stuck* states: a crashed part, or a running service holding an expired token. V3 treats every long-lived resource as a small supervised state machine with a detector and a recovery.

### 3.1 Auth / token lifecycle

Grounded in the AWS SSO model: IAM Identity Center caches a bearer access token as JSON under `~/.aws/sso/cache/` (filename derived from the `sso_start_url`), with an `expiresAt` timestamp. Legacy config is a fixed 8-hour session with **no** auto-refresh; the SSO token-provider config supports automatic refresh (the token is checked hourly and refreshed via the refresh token). Kiro's own auth may also write under `~/.kiro/` and newer AWS login under `~/.aws/login/cache`. Sources: [SSO token cache / legacy config](https://docs.aws.amazon.com/sdkref/latest/guide/feature-sso-credentials.html), [session expiration & refresh](https://docs.aws.amazon.com/sdkref/latest/guide/understanding-sso.html), [hourly refresh](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso-concepts.html). *(Content rephrased for compliance with licensing restrictions.)*

**AuthManager** — one actor, authoritative for auth state:

- **Discovery** (startup + periodic): locate the active token by scanning, in priority order, `$KIRO_CONFIG_DIR`, `~/.kiro/`, `~/.aws/sso/cache/*.json`, `~/.aws/login/cache/*.json`. It reads only non-secret metadata (`expiresAt`, `region`, `startUrl`) — it never logs or echoes token values, referencing files by path/existence.
- **State**: `UNKNOWN → LOGGED_OUT → LOGGING_IN → LOGGED_IN → EXPIRING_SOON → REFRESHING → (LOGGED_IN | LOGGED_OUT)`.
- **Liveness truth**: two signals, cross-checked:
  1. **Cheap/local**: parse the smallest `expiresAt` across discovered token files → compute time-to-expiry.
  2. **Authoritative**: run `kiro-cli whoami` (short timeout, cached ~10s), the same probe V2 uses.
- **Proactive refresh**: when `expiresAt` is within `refresh_margin` (default 300s), transition to `REFRESHING` and attempt a refresh **before** any worker uses a stale token. This is the direct fix for V2's "stuck on old token" bug. Refresh is done once, by the AuthManager only (never by workers), so the shared single-account cache is touched by exactly one actor (important given Kiro's one-active-session-per-OIDC-client constraint — verified as a design assumption; workers must share creds, never re-auth).
- **On expiry with no refresh possible** (legacy 8h session): state → `LOGGED_OUT`, tray → yellow, requests get a clean native `401/503` telling the client to authenticate. The **service stays up** — it does not crash, and it recovers automatically the moment a valid token reappears (re-login via CLI/tray).
- **Worker coupling**: workers subscribe to auth state. On `LOGGED_OUT`/`REFRESHING`, the scheduler stops leasing (fast-fail or brief queue) rather than dispatching doomed turns; on return to `LOGGED_IN`, dispatch resumes. No worker ever runs against a token it discovered itself.

### 3.2 Login flows

- **Device flow (backbone, headless-safe):** `kiro-cli login --use-device-flow` prints a verification URL + code. The service captures that output (as V2's `/admin/login` already does) and exposes it via:
  - `kiro-api login` → prints URL+code in the terminal (works over SSH),
  - `/health` / `/admin/login/status` → so tray and clients can show it,
  - optional **auto-`xdg-open`** of the URL when a desktop session is detected (`$DISPLAY`/`$WAYLAND_DISPLAY`), else print-only.
- **Tray Login (optional, later):** a single menu item that triggers the service's device-flow login and `xdg-open`s the URL. (Deferred; tray is read-only at first.)
- **Browser-callback flow (nice-to-have, not default):** a loopback listener `http://127.0.0.1:<port>/auth/callback` if/when kiro-cli supports an auth-code redirect. More seamless on desktops, useless headless — so it's strictly optional.

### 3.3 Worker lifecycle
- Each worker is health-pinged (a cheap ACP round-trip or liveness flag). A worker that is dead, unresponsive past a deadline, or repeatedly erroring is **killed and replaced** (whole process group killed, à la V2's reaper). One bad worker never wedges the pool.
- A worker mid-turn on client disconnect gets `session/cancel`, then a bounded drain, then reuse.

### 3.4 Config lifecycle (host/port that never crashes)
- Host/port/workers/auth are read from **CLI > env > config file > defaults** and shown by `kiro-api config`.
- **Bind safety**: on `serve`, the socket bind is attempted explicitly and validated *before* the pool spins up. A bad address/port (in use, not permitted, malformed) → a clear error + non-zero exit, never a half-started zombie. `systemd` then restarts per policy, or the operator fixes the config. Any IPv4/IPv6 address and any port are allowed (localhost, LAN IP, 0.0.0.0).
- Changing host/port is: edit config/env (or `kiro-api config set`, later) → `systemctl restart kiro-api`. No in-process rebind (uvicorn can't do it cleanly); a supervised restart is the reliable path.

---

## 4. Reliability model ("AWS would ship this")

- **systemd unit** (`kiro-api.service`): `Type=notify` (the app signals readiness), `Restart=on-failure`, `RestartSec=2`, `WatchdogSec=30` (the app pings `sd_notify(WATCHDOG=1)`; if it hangs, systemd restarts it), `TasksMax` sized for `max_workers × children + headroom`, `LimitNOFILE` raised, `MemoryMax`/`CPUQuota` optional, `StandardOutput=journal`. Supports both **system** (`/etc/systemd/system`) and **`--user`** service installs.
- **In-process supervision**: the Supervisor/Reaper keeps V2's proven hygiene — zombie reaping, wedged-worker force-kill by process group, `/proc` child-count leak detection — adapted to the worker pool, plus per-worker restart/circuit-break.
- **Structured logging** to journald (text or JSON), with request IDs, per-worker tags, and a clear **error taxonomy** mapped to native API error shapes:

  | Condition | HTTP | OpenAI type | Anthropic type |
  |-----------|------|-------------|----------------|
  | Rate limit / throttle / quota | 429 | `rate_limit_error` | `rate_limit_error` |
  | Overloaded / no worker / capacity | 503 | `server_error` | `overloaded_error` |
  | Timeout / deadline | 504 | `server_error` | `api_error` |
  | Not logged in / auth expired | 401 | `authentication_error` | `authentication_error` |
  | Other | 502 | `server_error` | `api_error` |

- **Health split**: `/health` = process alive; `/ready` = logged in **and** ≥1 worker available. Monitors distinguish "up" from "serving."
- **Graceful drain**: SIGTERM → stop accepting, cancel/finish in-flight, kill workers cleanly, exit 0. No ghost `kiro-cli` processes (systemd `KillMode=mixed` + our own drain).

---

## 5. CLI specification

```
kiro-api --help
kiro-api --version

kiro-api serve            # start the HTTP service (default command; used by systemd)
    -H/--host <addr>          # any IP; default from config/env or 127.0.0.1
    -p/--port <port>          # default 8787
    --workers <n>             # max workers (elastic); default 8
    --min-workers <n>         # default 0
    --auth-key <key>          # optional bridge API key (empty = no auth)
    --model <id>              # default model (default: auto)
    --log-format <text|json>

kiro-api login                # run kiro-cli device-flow login; print URL+code; optional xdg-open
kiro-api status               # human-readable: auth state, pool, workers, endpoints, health
kiro-api stats [--json]       # machine-readable metrics for scripting
kiro-api config [get|set ...] # show/edit effective config (precedence-aware)
kiro-api models               # list models from kiro-cli's live catalogue
kiro-api acp                  # run as an ACP stdio agent (for ACP-native editors) [optional]
kiro-api install-service      # write + enable the systemd unit (system or --user)
    --user                    # install as a user service
    --uninstall               # remove it
kiro-api version              # kiro-api + kiro-cli versions
```

`serve`/`login`/`status`/`stats`/`config` are the tray's old menu items turned into commands. `--help` is provided at top level and per subcommand.

---

## 6. Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/chat/completions` | OpenAI chat (stream + non-stream) |
| `POST /v1/responses` | OpenAI Responses API |
| `GET /v1/models`, `GET /v1/models/{id}` | Live model catalogue |
| `POST /v1/messages` | Anthropic messages (stream + non-stream) |
| `POST /v1/messages/count_tokens` | Anthropic token estimate |
| `POST /acp/chat`, `POST /acp/chat/stream` | Native ACP |
| `GET /health`, `GET /ready` | Liveness / readiness |
| `GET /stats`, `WS /ws/stats` | Metrics (tray + scripting) |
| `POST /admin/login`, `GET /admin/login/status` | Device-flow login trigger/poll (localhost) |

MCP servers declared by a harness are discovered and forwarded to `kiro-cli` on `session/new` (passthrough; the gateway never executes tools itself).

---

## 7. Dependencies (lean)

- Core: `fastapi`, `uvicorn[standard]`, `pydantic`. Logging via stdlib `logging` (JSON formatter optional). systemd notify via a tiny `sd_notify` helper (no external dep needed — it's a socket write).
- Optional extra `[tray]`: `pystray`, `Pillow`, `requests` — so the core service has **zero GUI deps**.
- Dev: `pytest`, `pytest-asyncio`, `httpx`, `ruff`.
- Python **3.11+** (validated on 3.14). No pin to a bleeding-edge interpreter.

---

## 8. Keep / Remove / Add vs V2

**Keep (adapt):**
- Bounded-concurrency + queue/backpressure idea → generalized into the elastic worker pool + scheduler.
- Reaper (zombie reap, force-kill wedged jobs, leak detection) → adapted to workers.
- Clean coordinated shutdown / in-flight drain.
- `kiro-cli whoami` login probe → folded into AuthManager.
- Config-via-env + `.env` persistence → generalized to CLI>env>file precedence.
- `/health`, `/stats`, `/ws/stats`.
- Tray status dots + copy-endpoints (now read-only, all protocols).

**Remove:**
- Docker + Compose (both files), the airgapped bundle image, Dockerfiles.
- Web dashboard (`app/dashboard.py`, `static/dashboard.html`).
- Tray control surface (port/address/auth changes) — tray becomes read-only.
- Process-per-request `kiro-cli chat` runner → replaced by ACP worker pool.

**Add (from kiro-gateway, re-implemented AGPL-safe):**
- Anthropic shim, OpenAI Responses, native ACP routes.
- MCP discovery/passthrough.
- Real incremental streaming + SSE keepalives.
- Reasoning/thinking + tool-activity surfacing.
- Native error-shape mapping.
- ACP stdio mode (`kiro-api acp`).

**Add (new to V3):**
- Elastic worker pool + scheduler.
- Self-healing AuthManager with proactive token-expiry refresh.
- systemd install + watchdog integration.
- `/ready` vs `/health` split.
- Richer CLI.

**Licensing note:** kiro-gateway is AGPL-3.0. V3 studies its behavior and re-implements independently; no source is copied.

---

## 9. Package layout

```
kiro-api-docker/               (repo; V3 lives alongside, old app/ removed at the end)
├── kiro_api/
│   ├── __init__.py            # version
│   ├── cli.py                 # argparse CLI, subcommands
│   ├── config.py              # Config + precedence + bind validation
│   ├── logging_setup.py       # structured logging (text/json)
│   ├── errors.py              # error taxonomy → native shapes
│   ├── sdnotify.py            # systemd notify/watchdog helper
│   ├── auth.py                # AuthManager (discovery, expiry watch, login)
│   ├── acp/
│   │   ├── client.py          # one kiro-cli acp worker (JSON-RPC over stdio)
│   │   ├── models.py          # JSON-RPC + ACP wire models
│   │   └── events.py          # normalized event contract
│   ├── pool.py                # elastic WorkerPool + Scheduler + queue
│   ├── supervisor.py          # reaper/leak-watch/worker restart
│   ├── server.py              # FastAPI app assembly + lifespan + serve()
│   ├── routes/
│   │   ├── openai.py          # /v1/chat/completions, /v1/responses, /v1/models
│   │   ├── anthropic.py       # /v1/messages, count_tokens
│   │   ├── acp.py             # /acp/chat[/stream]
│   │   ├── control.py         # /health, /ready, /stats, /ws/stats
│   │   └── admin.py           # /admin/login[/status]
│   ├── shims/                 # request/response translation per protocol
│   ├── streaming.py           # SSE builders + keepalives
│   └── tray/                  # optional read-only companion
├── systemd/kiro-api.service   # unit template
├── tests/                     # unit + integration (kiro-cli acp stub)
├── scripts/kiro-cli-acp-stub.py  # fake ACP agent for tests/dev
├── pyproject.toml
└── docs/V3-DESIGN.md          # this file
```

---

## 10. Open verification items (to confirm during build/audit)
- Exact `kiro-cli acp` spawn flags and engine pin (V3 defaults to `--agent-engine v2`; v3 engine needs host-mediated auth not implemented).
- Whether one ACP worker can safely run >1 concurrent session (default: no; measure later).
- Kiro's one-active-session-per-OIDC-client behavior under many workers sharing one token cache (design assumes shared read-only creds + single refresh actor).
- Real token file field names across `~/.kiro` vs `~/.aws/sso/cache` vs `~/.aws/login/cache` (auto-detect + tolerate variants).
```
