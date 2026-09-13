# Cursor-API — Ingest Hub + TUI Control Plane

**Status:** Approved (user 2026-09-13)  
**Goal:** Long-term local ingestion hub for Cursor Agent CLI + polished TUI so ATO RMS and other clients can use `/v1` painlessly.

## Decisions locked

| Decision | Choice |
|----------|--------|
| Agent tab vs `/v1` | **A — separate lanes** (headless `agent -p` vs interactive Agent PTY; shared login/model/workspace only) |
| Token UX | **C — both** settable max context tokens + live usage |
| `/v1` ambition | **Kitchen sink** via **plugin adapters from day one** |
| Architecture | Normalize-first hub: adapters → `IngestRequest` → one Runner |

## §1 Core architecture

```
Client → HTTP router → Adapter plugin → IngestRequest → Runner → Cursor Agent CLI
TUI Agent tab → separate interactive agent PTY (never shares /v1 session)
```

- Adapters **never** spawn the agent.
- Runner owns: spawn, Windows long-prompt staging, concurrency, timeouts, usage, cancel-on-disconnect.
- Built-in adapters in-process; external adapters load from config dir via manifest + executable contract.

## §2 TUI

Tab order (CLI last):

1. Dashboard  
2. Config  
3. Logs  
4. Usage  
5. **Agent** (interactive `agent` chat PTY)  
6. Help  
7. **CLI** (generic shell PTY — far right)

- Show **max context tokens** (config) on Dashboard + Config.
- Show **live / last usage** (prompt≈, completion≈, total) on Dashboard + Usage.
- Agent tab: embed PTY running `agent` (or node+index.js) with workspace/model from config; Shift+R restart; F-keys / Ctrl+←→ leave tab.
- CLI tab stays generic shell for ad-hoc commands.

## §3 Plugin contract

```text
IngestRequest {
  model: String,
  prompt: String,
  stream: bool,
  json_mode: bool,
  meta: object  // adapter id, request_id, hints
}
```

**In-process trait:** `id`, `routes()` (method+path), `ingest(body/headers) -> IngestRequest`, optional `shape_response`.

**External plugin (long-term):**
- Dir: `%APPDATA%/cursor-api/adapters/<id>/`
- `manifest.toml`: id, routes, command, timeout
- Process: stdin = raw HTTP body (+ env for headers/method/path); stdout = JSON `IngestRequest`
- Same registry merges built-in + external.

## §4 Built-in adapters (rollout)

| Priority | Adapter | Route(s) |
|----------|---------|----------|
| P0 | `openai_chat` (forgiving) | `POST /v1/chat/completions` |
| P0 | `raw_ingest` | `POST /v1/ingest` |
| P1 | `openai_completions` | `POST /v1/completions` |
| P1 | `anthropic_messages` | `POST /v1/messages` |
| P2 | `multipart_files` | `POST /v1/ingest/files` |
| Later | webhook/form | as needed |

Forgiving OpenAI: string or array `content`, `prompt`/`input` aliases, system fold, json_mode from `response_format`.

## §5 Config / health

- `cursor.max_context_tokens` (default e.g. 128000) — advertised on `/health`, used for soft truncation warning / optional trim.
- `/health` includes `max_context_tokens`, `adapters: [{id, routes}]`, usage totals.
- Long prompts: stage to temp file when over Windows argv limit (os error 206).

## Non-goals

- Full OpenAI/Anthropic tool/vision parity
- Shared chat session between TUI Agent and `/v1`
