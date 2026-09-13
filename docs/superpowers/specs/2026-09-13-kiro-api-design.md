# Kiro-API — Ingest Hub + TUI Control Plane

**Status:** Ported from Cursor-API patterns (2026-09-13)  
**Goal:** Local ingestion hub for **Kiro CLI** + polished TUI so ATO RMS and other clients can use `/v1`.

Default port: **8788** (Cursor-API remains on **8787** when both run).

## Architecture

```
Client → HTTP /v1 → flatten prompt → kiro-cli chat --no-interactive (stdin)
TUI Agent tab → separate interactive kiro-cli PTY
```

Same product shape as Cursor-API: TUI control plane + OpenAI-compatible API, different CLI backend.

## Config

`%APPDATA%\kiro-api\config.toml` (Windows) — see README.
