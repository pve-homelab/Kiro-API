# Kiro-API architecture

Local OpenAI-compatible `/v1` bridge backed by the Kiro CLI, with a Ratatui TUI control plane.

Default port: **8788** (companion to [Cursor-API](https://github.com/pve-homelab/Cursor-API) on **8787**).

## Overview

```text
Client → HTTP router → Adapter → IngestRequest → Runner → kiro-cli chat --no-interactive
TUI Agent tab → separate interactive kiro-cli PTY (never shares /v1 session)
```

- Adapters normalize request bodies; they never spawn the CLI.
- The runner owns spawn, prompt staging, concurrency, timeouts, usage, and cancel-on-disconnect.
- Built-in adapters run in-process; optional external adapters load from the config directory.

## TUI

Tab order:

1. Dashboard  
2. Config  
3. Logs  
4. Usage  
5. Help  
6. **Agent** (interactive `kiro-cli` PTY)  
7. **CLI** (generic shell PTY)

Dashboard and Config expose max context tokens. Usage shows live / recent token estimates. Agent and `/v1` are separate lanes that only share login, model, and workspace settings.

By default the runner **queues** when concurrency slots are full (`reject_when_busy=false`). The `long_running` profile uses concurrency 1 with queueing so additional `/v1` callers wait instead of getting HTTP 429. `/health` reports `available_concurrency` so operators can distinguish “legitimately busy” from “stuck”.

Bind host/port come from `config.toml` or product-specific `KIRO_API_HOST` / `KIRO_API_PORT` only — shared `BRIDGE_*` env vars are ignored to avoid collisions with Cursor-API on the same machine.

## Ingest adapters

Requests are normalized to:

```text
IngestRequest {
  model: String,
  prompt: String,
  stream: bool,
  json_mode: bool,
  meta: object  // adapter id, request_id, hints
}
```

Built-in routes include forgiving OpenAI chat (`POST /v1/chat/completions`), raw ingest (`POST /v1/ingest`), and additional completion / messages shapes as registered in the adapter registry.

External adapters (optional): `%APPDATA%/kiro-api/adapters/<id>/` on Windows, or the platform config dir equivalent — `manifest.toml` plus a command that reads the HTTP body on stdin and writes JSON `IngestRequest` on stdout.

## Config and health

- `cursor.max_context_tokens` (default `128000`) is advertised on `/health`.
- `/health` also reports adapters, default model, mode, and usage totals.
- Large prompts are staged via stdin / temp files to avoid Windows command-line length limits.

## Non-goals

- Full OpenAI/Anthropic tool or vision parity
- Shared chat session between the TUI Agent tab and `/v1`
