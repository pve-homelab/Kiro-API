# Kiro-API

CLI command: `kiro-api` (lowercase). Display name: **Kiro-API**.

Local **OpenAI-compatible `/v1` API** backed by the **Kiro CLI** (`kiro-cli`), with a **Ratatui TUI** to start/stop the service, edit config, watch logs, and track estimated token usage.

Works on **Windows**, **macOS**, and **Linux**.

Default listen port is **8788** (so it can run beside [Cursor-API](https://github.com/pve-homelab/Cursor-API) on **8787** without colliding).

```text
Your app  →  http://127.0.0.1:8788/v1/chat/completions  →  kiro-cli chat --no-interactive
```

---

## Getting started

**Important:** do **not** run interactive `kiro-cli` chat as your server. Login once, then start **Kiro-API** (`kiro-api`) — that is the control panel. API calls spawn short headless `kiro-cli chat --no-interactive` jobs in the background; you stay in the TUI.

### Quick start

The same three commands build and start the project on every operating system:

```text
git clone https://github.com/pve-homelab/Kiro-API.git
cd Kiro-API
cargo run --release
```

The first build downloads Rust dependencies and may take a few minutes. When the TUI opens, the API is available at `http://127.0.0.1:8788/v1`. Keep that terminal open while using the API.

Before running those commands, complete the one-time setup for your operating system below.

### Shared prerequisites (all platforms)

1. Install **Rust**: https://rustup.rs/
2. Install the **Kiro CLI** (`kiro-cli`): https://kiro.dev/docs/cli/
3. Log in **once** (this is interactive — finish it, then close that prompt):

```bash
kiro-cli login --use-device-flow
kiro-cli whoami
```

---

### Windows (PowerShell)

Use **Windows Terminal** or PowerShell 7+ if you can.

**1. Install Rust** (if needed) — open https://rustup.rs/ and run the Windows installer, then reopen the terminal.

**2. Install + log in to Kiro CLI** (one-time):

```powershell
irm 'https://cli.kiro.dev/install.ps1' | iex
kiro-cli login --social google --use-device-flow
kiro-cli whoami
```

**3. Build and start Kiro-API (TUI + API)**

```powershell
git clone https://github.com/pve-homelab/Kiro-API.git
cd Kiro-API
cargo run --release
```

That opens the **TUI** and **starts the HTTP API automatically**. Leave this window open.

You should see a ready line like:

```text
Kiro-API ready · http://127.0.0.1:8788/v1 · /health → 200 · model=auto · mode=ask · …
```

Use `model=auto` (or whatever the banner shows) in your client.

**TUI keys:** `1–7` tabs · `s` stop/start API · `t` smoke test · `q` quit · `Ctrl+Q` always quits

*(Headless only, no TUI: `.\target\release\kiro-api.exe serve` — not recommended for first-time use.)*

**4. Health check + test prompt** (new PowerShell window)

```powershell
# Health (note default_model)
Invoke-RestMethod http://127.0.0.1:8788/health | Format-List status, v1_url, default_model, mode, healthy

# Send a prompt and print the reply
$body = @{
  model = "auto"
  messages = @(@{ role = "user"; content = "Say hi in one sentence." })
} | ConvertTo-Json -Depth 5

$res = Invoke-RestMethod http://127.0.0.1:8788/v1/chat/completions `
  -Method Post -ContentType "application/json" -Body $body

$res.choices[0].message.content
```

**Stop:** in the TUI press `s` (stop server) then `q` (quit).

---

### macOS (Terminal / zsh)

**1. Install Rust** (if needed)

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustc --version
```

**2. Install + log in to Kiro CLI** (one-time):

```bash
curl -fsSL https://cli.kiro.dev/install | bash
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.zshrc if needed
kiro-cli login --use-device-flow
kiro-cli whoami
```

**3. Build and start Kiro-API (TUI + API)**

```bash
git clone https://github.com/pve-homelab/Kiro-API.git
cd Kiro-API
cargo run --release
```

That opens the **TUI** and **starts the HTTP API automatically**. Leave this terminal open.

You should see a ready line like:

```text
Kiro-API ready · http://127.0.0.1:8788/v1 · /health → 200 · model=auto · mode=ask · …
```

Use `model=auto` (or whatever the banner shows) in your client.

**TUI keys:** `1–7` tabs · `s` stop/start API · `t` smoke test · `q` quit · `Ctrl+Q` always quits

*(Headless only, no TUI: `./target/release/kiro-api serve` — not recommended for first-time use.)*

**4. Health check + test prompt** (new terminal tab)

```bash
curl -s http://127.0.0.1:8788/health | python3 -m json.tool
curl -s http://127.0.0.1:8788/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Say hi in one sentence."}]}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

**Stop:** in the TUI press `s` (stop server) then `q` (quit).

---

### Linux (bash)

**1. Install Rust** (if needed)

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustc --version
```

Also install a C toolchain if cargo complains (Debian/Ubuntu example):

```bash
sudo apt update && sudo apt install -y build-essential pkg-config
```

**2. Install + log in to Kiro CLI** (one-time):

```bash
curl -fsSL https://cli.kiro.dev/install | bash
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.bashrc if needed
kiro-cli login --use-device-flow
kiro-cli whoami
```

**3. Build and start Kiro-API (TUI + API)**

```bash
git clone https://github.com/pve-homelab/Kiro-API.git
cd Kiro-API
cargo run --release
```

That opens the **TUI** and **starts the HTTP API automatically**. Leave this terminal open.

You should see a ready line like:

```text
Kiro-API ready · http://127.0.0.1:8788/v1 · /health → 200 · model=auto · mode=ask · …
```

Use `model=auto` (or whatever the banner shows) in your client.

**TUI keys:** `1–7` tabs · `s` stop/start API · `t` smoke test · `q` quit · `Ctrl+Q` always quits

*(Headless only, no TUI: `./target/release/kiro-api serve` — not recommended for first-time use.)*

**4. Health check + test prompt** (new terminal)

```bash
curl -s http://127.0.0.1:8788/health | python3 -m json.tool
curl -s http://127.0.0.1:8788/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Say hi in one sentence."}]}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

**Stop:** in the TUI press `s` (stop server) then `q` (quit).

---

### If something fails

| Symptom | Fix |
|---------|-----|
| Stuck in `kiro-cli` / login UI | That is **not** the bridge. Finish or Ctrl+C, then run `kiro-api` (the TUI) |
| `kiro-cli` not found | Install from https://kiro.dev/docs/cli/ , ensure it’s on `PATH`, reopen the terminal |
| Auth / Not logged in | Run `kiro-cli login --use-device-flow` (or set `KIRO_API_KEY`) |
| Can’t connect to `8788` | Make sure the TUI is still open and shows RUNNING (press `s` if stopped) |
| `failed to spawn kiro-cli` | Run `kiro-api doctor` |
| Port conflict with Cursor-API | Keep Kiro-API on **8788**, Cursor-API on **8787** |
| Slow first reply | Normal — first Kiro CLI run can take a bit |
| TUI without auto-start | `kiro-api tui --no-autostart` then press `s` |

---

## Commands

| Command | Description |
|---------|-------------|
| `kiro-api` | **TUI + start HTTP API** (default — use this) |
| `kiro-api tui --no-autostart` | TUI only; press `s` to start API |
| `kiro-api serve` | Headless HTTP only (no TUI) |
| `kiro-api doctor` | Locate/probe the `kiro-cli` binary |
| `kiro-api doctor --full` | Probe + live smoke completion |
| `kiro-api config-path` | Print `config.toml` location |

Install onto your cargo bin directory (optional):

```bash
cargo install --path .
kiro-api
```

## TUI

| Tab | Keys |
|-----|------|
| Dashboard | `s` start/stop · `r` health-check · `t` smoke test |
| Config | `↑/↓` select · `Enter` edit · `p` cycle profile · `j` json mode · `w` force · `k` trust |
| Logs | `↑/↓` scroll · `c` clear |
| Usage | token estimates + recent requests · `x` reset |
| Help | quick client notes + compatibility |
| Agent | interactive `kiro-cli` chat (separate from `/v1`) · `Shift+R` restart |
| CLI | generic shell PTY · `Shift+R` restart |

`Tab` / `1–7` switch tabs · on Agent/CLI use `F1–F7` or `Ctrl+←/→` · `q` / `Esc` quit · `Ctrl+Q` always works

## More API examples

Default base URL: `http://127.0.0.1:8788/v1`  
Default model: whatever `/health` reports as `default_model` (usually `auto`)

```bash
# Streaming SSE
curl http://127.0.0.1:8788/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","stream":true,"messages":[{"role":"user","content":"Say hi."}]}'

# List models
curl http://127.0.0.1:8788/v1/models
```

### OpenAI SDK example

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8788/v1",
    api_key="local",  # set Bridge API key in TUI if you enabled auth
)

print(client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello from my app"}],
).choices[0].message.content)
```

## Configuration

Created on first run. Find it with:

```bash
kiro-api config-path
```

Typical locations:

| Platform | Config file |
|----------|-------------|
| Linux | `~/.config/kiro-api/config.toml` |
| macOS | `~/Library/Application Support/kiro-api/config.toml` |
| Windows | `%APPDATA%\kiro-api\config.toml` |

Important fields:

| Field | Default | Notes |
|-------|---------|-------|
| `server.host` / `port` | `127.0.0.1` / `8788` | Bind locally |
| `server.request_timeout_secs` | `600` | Per-request CLI timeout |
| `server.max_concurrency` | `2` | Max parallel `kiro-cli` processes |
| `server.reject_when_busy` | `false` | When `true`, return HTTP 429 when slots are full (default queues) |
| `server.queue_wait_secs` | `0` | Max wait for a slot when `reject_when_busy=true` (`0` = try once). When `reject_when_busy=false`, wait indefinitely |
| `cursor.profile` | `chat` | Preset: `chat`, `json_api`, `long_running` (`long_running` → timeout 900s, concurrency 1, **queue** not 429) |
| `cursor.mode` | `ask` | Chat-safe default |
| `cursor.trust` | `true` | Maps to `--trust-tools=` (empty) unless `force` |
| `cursor.force` | `false` | When true, pass `--trust-all-tools` |
| `cursor.json_mode` | `false` | Append JSON instruction + normalize responses |
| `cursor.message_flatten_mode` | `flat` | `flat`, `fold_system`, or `system_last` |
| `cursor.default_model` | `auto` | Passed to `--model` — also shown on ready banner + `/health` |
| `cursor.workspace` | empty | Working directory for CLI runs |
| `cursor.binary` | empty | Auto-detect `kiro-cli` on `PATH` |
| `cursor.max_context_tokens` | `128000` | Soft budget; warn (or truncate if enabled) |
| `auth.api_key` | auto-generated | If set, require `Authorization: Bearer …` on `/v1` |

### Environment overrides

Product-specific names only. Shared `BRIDGE_*` vars are **ignored** so a leftover `BRIDGE_PORT` from Cursor-API (or vice versa) cannot silently steal the bind address.

| Variable | Effect |
|----------|--------|
| `KIRO_API_HOST` | Override bind host |
| `KIRO_API_PORT` | Override port (default **8788**) |
| `KIRO_API_AUTH_KEY` | Set bridge HTTP auth key (`Authorization: Bearer …`) |
| `KIRO_API_TIMEOUT_SECS` | Override request timeout |
| `KIRO_API_JSON_MODE` | `true`/`1` enables JSON mode |
| `KIRO_API_DEFAULT_MODEL` | Override default model |
| `KIRO_API_MAX_CONTEXT_TOKENS` | Override context budget |
| `KIRO_API_TRUNCATE_OVER_CONTEXT` | `true`/`1` truncates oversized prompts |
| `KIRO_API_MAX_CONCURRENCY` | Override max parallel CLI jobs |
| `KIRO_API_REJECT_WHEN_BUSY` | `true`/`1` → HTTP 429 when full; default queues |
| `KIRO_API_QUEUE_WAIT_SECS` | Wait budget when rejecting when busy |
| `KIRO_API_KEY` | Forwarded to Kiro CLI |
| `KIRO_API_WORKSPACE` | Override workspace path |
| `CURSOR_WORKSPACE` | Legacy workspace override (still accepted) |

On startup the server logs the effective bind address and whether host/port came from `config` or `env:KIRO_API_*`. `/health` also reports `available_concurrency`, `bind_host_source`, and `bind_port_source`.

## How it works

- Headless jobs: `kiro-cli chat --no-interactive …`
- Large prompts are written to **stdin** (avoids Windows argv length limits)
- Responses are mapped to OpenAI `chat.completion` / SSE chunks
- `response_format: { "type": "json_object" }` triggers JSON extraction (strips markdown fences)
- Streaming sends a final SSE chunk with `usage` before `[DONE]`
- Send `X-Request-ID` to correlate requests in logs and response headers
- HTTP **429** only when `reject_when_busy=true` and slots are full (default **queues** instead)
- Client disconnect / timeout kills the CLI process **tree** (Windows: `taskkill /T`) so semaphore permits cannot stick
- Token totals are **estimates** (`chars/4`) unless Kiro exposes billing usage later
- Built-in ingest adapters normalize common request shapes into one runner

### OpenAI compatibility matrix

| Feature | Supported |
|---------|-----------|
| `/v1/chat/completions` sync | Yes |
| `/v1/chat/completions` stream (SSE) | Yes |
| `/v1/models` | Yes |
| `response_format` JSON | Yes |
| `X-Request-ID` | Yes |
| Final stream `usage` chunk | Yes |
| HTTP 429 when busy | Optional (`reject_when_busy=true`; default queues) |
| `temperature` / `max_tokens` | Ignored (CLI has no equivalent) |
| Tool / function calling | No |

## Cross-platform notes

- The HTTP server and TUI use portable crates (`tokio`, `axum`, `crossterm`, `ratatui`).
- `kiro-cli` is resolved from `PATH`, then common install locations (Windows: `%LOCALAPPDATA%\Kiro-Cli`, `C:\Program Files\Kiro-Cli`).
- Prefer a UTF-8 terminal. On Windows, Windows Terminal or a modern PowerShell host works best for the TUI.
- Default command is always **TUI + autostart API** on Windows, macOS, and Linux. Use `serve` only for systemd/launchd/NSSM supervisors.

## Safety

- Keep `force=false` unless you intentionally want `--trust-all-tools` on headless runs
- Prefer binding `127.0.0.1` and setting a bridge API key for anything beyond solo local use

## Architecture

See [docs/architecture.md](docs/architecture.md) for the ingest hub and TUI control-plane overview.

## License

MIT
