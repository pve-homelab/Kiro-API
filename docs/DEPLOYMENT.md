# Deployment guide

Two ways to run Kiro-API V2:

- **Standard** (`docker-compose.yml`) — builds the slim image, mounts `kiro-cli`
  and credentials from the host. Use where kiro-cli is already installed.
- **Bundle** (`docker-compose.bundle.yml`) — one self-contained image; ideal for
  airgapped hosts where you want a single artifact to move.

---

## Prerequisites

- Docker + Docker Compose v2 on the host.
- `kiro-cli` installed and logged in on the host (or use the tray Login button).
- Confirm where credentials live and set them in `.env`:

```bash
which kiro-cli
ls -la ~/.kiro ~/.aws 2>/dev/null
```

---

## Standard configuration

```bash
cp .env.example .env
# Edit .env: set HOST_KIRO_CLI_BIN, HOST_KIRO_CONFIG_DIR, HOST_AWS_DIR to match.
docker compose up -d --build
```

Verify:

```bash
curl -s http://127.0.0.1:8787/health
# open the dashboard
xdg-open http://127.0.0.1:8788
```

---

## Bundle configuration (airgapped)

### On an internet-connected staging box

```bash
# 1. Stage Python wheels (linux/amd64)
pip download -r app/requirements.txt -d wheelhouse

# 2. Place the real kiro-cli binary
cp /path/to/kiro-cli vendor/kiro-cli

# 3. Build the self-contained image (offline install from wheelhouse)
docker build --build-arg OFFLINE=1 -f docker/Dockerfile.bundle -t kiro-api-bundle:2.0.0 .

# 4. Export a single artifact
docker save kiro-api-bundle:2.0.0 | gzip > kiro-api-bundle-2.0.0.tar.gz
```

### On the airgapped workspace

```bash
docker load < kiro-api-bundle-2.0.0.tar.gz
cp .env.example .env      # set HOST_KIRO_CONFIG_DIR / HOST_AWS_DIR
docker compose -f docker-compose.bundle.yml up -d
```

> The bundle mounts only credentials (the binary is baked in). If those paths
> don't exist on your box, remove or adjust the credential volume lines in
> `docker-compose.bundle.yml`.

---

## The tray agent (host)

```bash
sudo apt-get install -y python3-venv python3-tk gir1.2-appindicator3-0.1 \
     libnotify-bin xclip     # wl-clipboard on Wayland
python3 -m venv tray/.venv
tray/.venv/bin/pip install -r tray/requirements.txt
tray/run-tray.sh
```

Autostart at login:

```bash
cp tray/kiro-api-tray.desktop ~/.config/autostart/
# edit Exec= to the absolute path of tray/run-tray.sh
```

### Tray menu

| Item | Action |
|------|--------|
| ● status | current health (also shown by the dot color) |
| `http://…/v1` | click to copy the base URL |
| **API Endpoints ▸** | copy Chat Completions / Completions / Models URL to clipboard |
| **Open Dashboard** | open `:8788` in a browser |
| **Login to Kiro CLI** | device-flow login (approve in browser) |
| **Change Port…** | prompt → rewrite `.env` → restart |
| **Change Address…** | prompt → rewrite `.env` → restart |
| **Auth ▸** | set / clear the bridge API key |
| **Restart / Stop / Quit** | stack control |

---

## Client usage

Base URL: `http://localhost:8787/v1` (default). Model: `auto`.

### curl

```bash
curl -s http://localhost:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Say hi."}]}'
```

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8787/v1", api_key="local")
print(client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello"}],
).choices[0].message.content)
```

### Long-running requests

Add `X-Kiro-Long: 1` for tool calls that need the long timeout tier:

```bash
curl http://localhost:8787/v1/chat/completions \
  -H "Content-Type: application/json" -H "X-Kiro-Long: 1" \
  -d '{"model":"auto","messages":[{"role":"user","content":"…"}]}'
```

### Auth (optional)

Set a key from the tray (Auth ▸ Set API key), then:

```bash
curl http://localhost:8787/v1/models -H "Authorization: Bearer <key>"
```

---

## Verifying an install

Run the smoke tests against a running instance:

```bash
python3 scripts/smoke_test.py   http://127.0.0.1:8787
python3 scripts/check_reaper.py http://127.0.0.1:8787
python3 scripts/check_admin.py  http://127.0.0.1:8787
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dot stays red | `docker compose ps`; check `docker compose logs kiro-api` |
| Dot yellow | Not logged in — click **Login to Kiro CLI** |
| Dot orange | Open dashboard; check leak warning / errors, review logs |
| `kiro-cli not found` | Verify `HOST_KIRO_CLI_BIN` and the read-only mount path |
| 429 responses | Raise `KIRO_API_MAX_CONCURRENCY` or set `REJECT_WHEN_BUSY=false` |
| Slow first reply | Normal — the first CLI run warms up |
