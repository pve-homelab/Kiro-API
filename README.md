# Kiro-API

CLI command: `kiro-api` (lowercase). Display name: **Kiro-API**.

Local **OpenAI-compatible `/v1` API** backed by the **Kiro CLI** (`kiro-cli`), with a **Ratatui TUI** to start/stop the service, edit config, watch logs, and track estimated token usage.

```text
Your app  →  http://127.0.0.1:8788/v1/chat/completions  →  kiro-cli chat --no-interactive
                 ↑
            Kiro-API TUI (optional control panel)
```

**Important:** do **not** run interactive `kiro-cli` chat as your server. Login once, then start **Kiro-API** (`kiro-api`) — that is the control panel. API calls spawn short headless `kiro-cli chat --no-interactive` jobs; you stay in the TUI.

Default listen port is **8788** (so it can run beside Cursor-API on **8787** without colliding).

---

## Windows (quick start)

1. Install [Rust](https://rustup.rs/) if needed.
2. Install + log in to Kiro CLI (one-time):

```powershell
irm 'https://cli.kiro.dev/install.ps1' | iex
# new terminal
kiro-cli login --social google --use-device-flow
kiro-cli whoami
```

3. Build and start Kiro-API (TUI + API):

```powershell
cd $env:USERPROFILE\OneDrive\Desktop\Kiro-API
cargo build --release
.\target\release\kiro-api.exe
# or: .\target-build\release\kiro-api.exe
```

Open the TUI window. API: `http://127.0.0.1:8788/v1`

*(Headless only: `kiro-api serve` — not recommended for first-time use.)*

---

## macOS / Linux

```bash
curl -fsSL https://cli.kiro.dev/install | bash
kiro-cli login
cd ~/Kiro-API   # or your clone path
cargo build --release
./target/release/kiro-api
```

---

## If something fails

| Symptom | Fix |
|---------|-----|
| Stuck in `kiro-cli` / login UI | That is **not** the bridge. Finish or Ctrl+C, then run `kiro-api` |
| `kiro-cli` not found | Install from https://kiro.dev/docs/cli/ , reopen the terminal |
| Auth / Not logged in | `kiro-cli login --use-device-flow` or set `KIRO_API_KEY` |
| Can’t connect to `8788` | TUI shows RUNNING; press `s` if stopped. Confirm port in Config |
| `failed to spawn kiro-cli` | Run `kiro-api doctor` |
| Port conflict with Cursor-API | Keep Kiro on **8788**, Cursor on **8787** |

---

## Commands

| Command | Description |
|---------|-------------|
| `kiro-api` | **TUI + start HTTP API** (default) |
| `kiro-api tui --no-autostart` | TUI only; press `s` to start API |
| `kiro-api serve` | Headless HTTP only (no TUI) |
| `kiro-api doctor` | Locate/probe `kiro-cli` |
| `kiro-api doctor --full` | Probe + live smoke completion |
| `kiro-api config-path` | Print `config.toml` location |

---

## Config

| OS | Path |
|----|------|
| Linux | `~/.config/kiro-api/config.toml` |
| macOS | `~/Library/Application Support/kiro-api/config.toml` |
| Windows | `%APPDATA%\kiro-api\config.toml` |

Useful env vars: `BRIDGE_HOST`, `BRIDGE_PORT` (default **8788**), `BRIDGE_API_KEY`, `BRIDGE_TIMEOUT_SECS`, `BRIDGE_DEFAULT_MODEL`, `BRIDGE_MAX_CONTEXT_TOKENS`, `KIRO_API_KEY`, `CURSOR_WORKSPACE` (workspace cwd).

Headless calls use:

```text
kiro-cli chat --no-interactive [--trust-tools= | --trust-all-tools] [--model …]
```

Large prompts are written to **stdin** (Kiro supports this; avoids Windows argv length limits).

---

## TUI tabs

Dashboard · Config · Logs · Usage · Agent · Help · CLI

- **Agent** — interactive `kiro-cli` (separate from `/v1` jobs)
- **CLI** — generic shell (far right)

Leave PTY tabs with **F1–F7** or **Ctrl+←/→**. Quit with **Ctrl+Q**.
