# Changelog

All notable changes to Kiro-API are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses
[Semantic Versioning](https://semver.org/).

## [3.0.0] — 2026-09-23

A ground-up rewrite: from a Docker/tray/dashboard stack (V2) to a headless,
CLI-driven, systemd-installable Linux service.

### Added
- **Elastic worker pool** of long-lived `kiro-cli acp` subprocesses — real
  streaming *and* true parallelism (≈10 typical, ~100 max), with a bounded queue
  and backpressure (`503`/`429`).
- **Protocol breadth**: OpenAI (`/v1/chat/completions`, `/v1/responses`,
  `/v1/models`), Anthropic (`/v1/messages`, `/v1/messages/count_tokens`), native
  **ACP** (`/acp/chat`, `/acp/chat/stream`), plus MCP passthrough.
- **Real incremental streaming** with SSE keepalives, reasoning/tool-activity
  surfacing, and native error shapes per protocol.
- **Self-healing auth**: an AuthManager with a token-expiry watchdog that
  refreshes proactively (SSO) or detects expiry via `whoami` (Builder ID),
  device-flow login, and browser open when a desktop is present. The service
  never gets stuck on a stale token and never crashes on logout.
- **CLI**: `serve`, `login`, `status`, `stats`, `config` (+ `set`/`get`/`path`),
  `set-host`, `set-port`, `models`, `acp`, `install-service`, `doctor`, `version`.
- **`doctor`** self-check (kiro-cli present, logged in, ACP works, bind available,
  config sane).
- **systemd** integration (`Type=notify`, `WatchdogSec`, `Restart=on-failure`,
  `TasksMax`, journald) via `install-service [--user]`.
- **Security**: constant-time bridge-key comparison, request body-size limit
  (413), and optional per-client rate limiting (429 + `Retry-After`).
- **ACP compatibility check**: the negotiated protocol version is validated at
  worker startup and fails loudly if a `kiro-cli` change breaks the contract.
- **Reliability**: per-turn idle + hard timeouts (no request hangs forever),
  dead-worker replacement, bind validation before startup, graceful `SIGTERM`
  drain with zero orphaned processes.
- Optional **read-only tray companion** (status dot + copy endpoints).
- Full docs (architecture, configuration, user guide, dependencies, diagrams),
  a test suite with an ACP stub, and CI.

### Changed
- Deployment is now bare-metal + systemd (no Docker).
- The tray is read-only (was a control surface in V2).
- Observability is CLI (`status`/`stats`) + journald (no web dashboard).

### Removed
- Docker + Compose files, the airgapped bundle image, and Dockerfiles.
- The web dashboard and the tray control surface.
- The process-per-request `kiro-cli chat` runner (replaced by the ACP pool).

### Verified
- Validated end to end against a real, logged-in `kiro-cli` 2.23.1: OpenAI +
  Anthropic + streaming responses, the live model catalogue, concurrent turns on
  a single account, and a clean `SIGTERM` with no orphans.
- Real systemd `--user` lifecycle: readiness via `sd_notify`, restart-on-crash,
  and journald logging.
