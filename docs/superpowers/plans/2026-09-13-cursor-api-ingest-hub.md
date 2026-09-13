# Cursor-API Ingest Hub + TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. User asked **not** to git commit unless requested — **do not commit**.

**Goal:** Ship polished Cursor-API (plugin ingest hub + TUI Agent/CLI tabs + token budget) so ATO RMS testing can resume.

**Architecture doc:** `docs/superpowers/specs/2026-09-13-cursor-api-ingest-hub-design.md`

**Project root:** `C:\Users\chris\OneDrive\Desktop\Cursor-API`

**Global constraints:**
- No git commits
- Windows-safe prompt staging (already partially in `src/cursor.rs`)
- Separate Agent PTY vs `/v1` headless jobs
- Keep existing OpenAI `/v1/chat/completions` working for ATO (ai-gateway → `http://127.0.0.1:8787/v1`)

---

### File map

| Path | Responsibility |
|------|----------------|
| `src/ingest/mod.rs` | `IngestRequest`, runner glue |
| `src/ingest/adapter.rs` | `IngestAdapter` trait + `AdapterRegistry` |
| `src/ingest/external.rs` | Load external adapters from `%APPDATA%/cursor-api/adapters` |
| `src/ingest/builtin/*.rs` | openai_chat, raw_ingest, openai_completions, anthropic_messages |
| `src/server/routes.rs` | Mount registry routes; health lists adapters + max_context_tokens |
| `src/config.rs` | `max_context_tokens` field |
| `src/cursor.rs` | Prompt staging (done); ChatRequest used by runner |
| `src/tui/app.rs` | Tab enum order; Agent tab keys; token fields |
| `src/tui/ui.rs` | Dashboard/Config/Usage/Agent/CLI drawing |
| `src/tui/cli_term.rs` | Reuse for Agent + CLI PTYs (or thin wrapper) |
| `src/tui/help.rs` | Update tab docs |
| `README.md` | Troubleshoot 206; adapters; new tabs |

---

### Task 1: Prompt staging polish + config max_context_tokens

**Files:** `src/cursor.rs`, `src/config.rs`, `src/server/routes.rs` (health), profiles if any

- Verify `stage_prompt` compiles and is used by complete/stream
- Add `max_context_tokens: u32` to cursor config (default `128_000`), persist in toml, env `BRIDGE_MAX_CONTEXT_TOKENS`
- Expose on `/health` and Config field list
- Soft behavior: if prompt tokens estimate > max_context_tokens, log warn; optional truncate only if config `truncate_over_context = true` (default false for ATO safety)

**Test:** `cargo build --release` (use `CARGO_TARGET_DIR=target-build` if exe locked)

---

### Task 2: Adapter plugin core + built-ins

**Files:** new `src/ingest/*`, wire `main.rs` / `lib` modules, refactor `routes.rs`

- Define `IngestRequest` + `IngestAdapter` trait
- `AdapterRegistry::builtins()` registers openai_chat, raw_ingest, openai_completions, anthropic_messages
- External loader: scan `adapters/*/manifest.toml`, spawn command for matching route (even if zero plugins installed)
- Refactor chat completions to go: parse via openai_chat adapter → runner
- `POST /v1/ingest` accepts `{ "prompt"|"text"|"input": "...", "model"?, "stream"? }` or raw text body
- Response shaping stays OpenAI-compatible for chat route; ingest returns `{ "id", "model", "content", "usage" }`

**Test:** unit tests for openai forgiving parse + ingest body; `cargo test`

---

### Task 3: TUI tabs — reorder, token UX, Agent tab, CLI last

**Files:** `src/tui/*`

Tab order: Dashboard, Config, Logs, Usage, Agent, Help, CLI (keys 1–7 / F1–F7)

- Dashboard: show max_context_tokens + last usage line
- Config: edit max_context_tokens
- Agent tab: second PTY (or lazy-start) launching agent interactive (prefer same node+index.js resolve as doctor, without `-p`; cwd=workspace if set)
- CLI tab: existing shell PTY, last
- Help updated
- Escape from Agent/CLI: F-keys, Ctrl+←/→, Ctrl+Q

**Test:** `cargo build --release`; manual note in README

---

### Task 4: Rebuild, restart, ATO smoke

- Build release to unlocked target dir; replace/restart cursor-api on :8787
- Confirm `/health` shows adapters + max_context_tokens + timeout 900 from config
- Smoke: small `/v1/chat/completions` + large prompt (>4k chars) must not os error 206
- Confirm ATO ai-gateway still points at `http://127.0.0.1:8787/v1`

---

## Execution order

1 → 2 → 3 → 4 (sequential; shared files). No commits.
