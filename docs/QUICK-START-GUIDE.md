# Quick Start Guide

Get Kiro-API V2 running and talking OpenAI `/v1` in a few minutes. This covers
pulling/loading the image, setting up the `.env` file, deploying, and reaching
the tray icon and dashboard.

For deeper detail see [DEPLOYMENT.md](DEPLOYMENT.md),
[CONFIGURATION.md](CONFIGURATION.md), and [DEPENDENCIES.md](DEPENDENCIES.md).

---

## TL;DR

```bash
cp .env.example .env          # set the HOST_KIRO_* paths for your machine
docker compose up -d --build  # start API (:8787) + dashboard (:8788)
tray/run-tray.sh              # start the ghost tray icon
```

Point any OpenAI client at `http://localhost:8787/v1` with model `auto`.

---

## Step 1 — Get the image

Pick the path that matches your environment.

### A. Normal (has internet / internal mirror)

Nothing to pre-pull — `docker compose up --build` builds the image and pulls
`python:3.12-slim` for you.

### B. Airgapped (single bundled image)

On a connected staging box, build and export one artifact:

```bash
pip download -r app/requirements.txt -d wheelhouse   # stage Python wheels
cp /path/to/kiro-cli vendor/kiro-cli                 # real CLI binary
docker build --build-arg OFFLINE=1 -f docker/Dockerfile.bundle -t kiro-api-bundle:2.0.0 .
docker save kiro-api-bundle:2.0.0 | gzip > kiro-api-bundle-2.0.0.tar.gz
```

Copy the tarball to the airgapped host and load it:

```bash
docker load < kiro-api-bundle-2.0.0.tar.gz
```

> Sharing with a coworker = hand them this one `.tar.gz`. They `docker load` it
> and run — no build, no toolchain, same image every time.

---

## Step 2 — Set up the `.env` file

```bash
cp .env.example .env
```

**Why the file exists:** you don't need it to run on defaults — compose already
falls back to sane defaults for every setting. `.env` is the place where
your **per-machine paths** and any **changes you make from the tray** (port,
address, API key) are saved so they survive restarts.

Edit these to match your machine:

```bash
HOST_KIRO_CLI_BIN=/usr/local/bin/kiro-cli      # `which kiro-cli`
HOST_KIRO_CONFIG_DIR=${HOME}/.kiro             # verify: ls -la ~/.kiro ~/.aws
HOST_AWS_DIR=${HOME}/.aws
```

Everything else (port 8787, dashboard 8788, no auth) already has a default. To
preset a port or enable auth up front, edit the tray-managed values in `.env`;
otherwise just set them later from the tray.

---

## Step 3 — Deploy

### Standard config

```bash
docker compose up -d --build
```

### Bundle config (airgapped)

```bash
docker compose -f docker-compose.bundle.yml up -d
```

Verify it's up:

```bash
curl -s http://localhost:8787/health      # expect "status":"ok"
```

---

## Step 4 — Start the tray icon (host)

One-time host setup:

```bash
sudo apt-get install -y python3-venv python3-tk gir1.2-appindicator3-0.1 \
     libnotify-bin xclip        # use wl-clipboard instead of xclip on Wayland
python3 -m venv tray/.venv
tray/.venv/bin/pip install -r tray/requirements.txt
```

Run it:

```bash
tray/run-tray.sh
```

Autostart at login (optional):

```bash
cp tray/kiro-api-tray.desktop ~/.config/autostart/
# edit Exec= to the absolute path of tray/run-tray.sh
```

---

## Step 5 — Use it

Point any OpenAI-compatible client at the base URL:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8787/v1", api_key="local")
print(client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello"}],
).choices[0].message.content)
```

Tip: click **API Endpoints** in the tray to copy the exact endpoint URL to your
clipboard.

---

## Stopping & restarting

```bash
docker compose down          # clean stop — kills in-flight jobs, no ghosts left
docker compose up -d         # clean restart
```

The service handles `docker stop` / `down` gracefully: it drains in-flight
`kiro-cli` jobs and exits, so no orphaned processes linger and the next start is
clean. (You can also use the tray **Stop** / **Restart** items.)

---

# Quick Reference

## Status dot (tray icon)

The ghost tray icon shows a colored dot in the corner:

| Dot | Meaning | What to do |
|-----|---------|------------|
| 🔴 **Red** | API not running | `docker compose up -d`, or tray **Restart** |
| 🟡 **Yellow** | Running but not logged in | Tray **Login to Kiro CLI** |
| 🟠 **Orange** | Error (leak warning / pool errors) | Open the dashboard, check logs |
| 🟢 **Green** | Online and healthy | Nothing — you're good |

## The tray menu

**Left- or right-click the ghost icon** in your Ubuntu top bar / system tray to
open the menu:

| Menu item | What it does |
|-----------|--------------|
| ● status line | Current health (mirrors the dot) |
| `http://…/v1` | Click to copy the base URL to clipboard |
| **API Endpoints ▸** | Copy a specific endpoint URL (Chat Completions / Completions / Models) — you'll get a "copied to clipboard" notification |
| **Open Dashboard** | Opens the dashboard in your browser |
| **Login to Kiro CLI** | Runs device-flow login (approve in browser) |
| **Change Port…** | Prompts for a new API port, saves it, restarts |
| **Change Address…** | Prompts for a new advertised address, saves it, restarts |
| **Auth ▸** | Set or clear the bearer API key |
| **Restart / Stop** | Control the container |
| **Quit** | Exit the tray agent (leaves the container running) |

## Addresses

| What | URL | Notes |
|------|-----|-------|
| API base (`/v1`) | `http://localhost:8787/v1` | Point your apps/agents here |
| Chat completions | `http://localhost:8787/v1/chat/completions` | |
| Dashboard | `http://localhost:8788` | Live status; also in tray → Open Dashboard |
| Health JSON | `http://localhost:8787/health` | Quick check from a terminal |

Ports are the defaults; if you changed them from the tray, the tray's status
line and **API Endpoints** menu always show the current values.

## The dashboard at a glance

Open `http://localhost:8788` (or tray → **Open Dashboard**). It shows, refreshing
live: model, endpoint, auth status, uptime, active/queued jobs, request/error
counts, token estimates, and resource-hygiene stats (child processes, GC runs,
and any leak warning). It's read-only — all controls are on the tray.

## Common commands

```bash
# status / logs
docker compose ps
docker compose logs -f kiro-api

# verify an install
python3 scripts/smoke_test.py http://127.0.0.1:8787

# change concurrency for the 50+ agent workload (then restart)
#   edit KIRO_API_MAX_CONCURRENCY in .env, then:
docker compose up -d
```
