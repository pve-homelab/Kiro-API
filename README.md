# Kiro-API V2

A Dockerized, OpenAI-compatible `/v1` API backed by the **Kiro CLI** (`kiro-cli`), with a
host-side system-tray agent, a slim live dashboard, and dual Docker Compose configurations
(standard + fully bundled/airgapped).

Your app → `http://localhost:8787/v1/chat/completions` → `kiro-cli chat --no-interactive`

This is the V2 successor to the Rust/Ratatui [Kiro-API](https://github.com/pve-homelab/Kiro-API)
TUI. V2 trades the TUI for a container + host tray agent so it runs the same way every time
with no toolchain fights, and is easy to share with coworkers by handing them one image.

---

## Why two pieces?

| Piece | Runs where | Responsibility |
|-------|-----------|----------------|
| **`kiro-api` container** | Docker | OpenAI-compatible `/v1` endpoints, dashboard, kiro-cli job pool |
| **`kiro-tray` agent** | Host (Ubuntu workspace) | System-tray icon, status dot, endpoint clipboard menu, login button, port/address change, auth toggle |

A container cannot draw a Linux system-tray icon (that needs host D-Bus / AppIndicator access),
so the tray icon is a thin host-side Python app that controls and monitors the container.

---

## Ports

| Service | Port | Notes |
|---------|------|-------|
| API (`/v1`) | `8787` | Default; changeable from the tray |
| Dashboard | `8788` | Live status page |

Both bind to `localhost` only.

---

## Quick links

- **[Quick Start Guide](docs/QUICK-START-GUIDE.md) — start here: pull, configure, deploy, and the dots/tray/dashboard reference**
- [Architecture & workflows](docs/ARCHITECTURE.md) — diagrams for system, request, login, port-change, and deployment flows
- [Deployment guide](docs/DEPLOYMENT.md) — standard + airgapped bundle, tray setup, client usage
- [Configuration & API reference](docs/CONFIGURATION.md) — every env var and endpoint
- [Airgapped dependency list](docs/DEPENDENCIES.md) — exact artifacts to stage

## Quick start (standard)

```bash
cp .env.example .env          # set HOST_KIRO_* paths for your box
docker compose up -d --build
curl -s http://127.0.0.1:8787/health
# dashboard: http://127.0.0.1:8788
```

Airgapped single-artifact path (bundle image): see
[DEPLOYMENT.md](docs/DEPLOYMENT.md#bundle-configuration-airgapped).

### Pulling from Harbor or Artifactory

The CI pipeline publishes both images with immutable commit tags and a `latest`
tag from the default branch:

```bash
docker login harbor.example.com
IMAGE_REPOSITORY=harbor.example.com/kiro/kiro-api IMAGE_TAG=latest \
  docker compose pull
IMAGE_REPOSITORY=harbor.example.com/kiro/kiro-api-bundle IMAGE_TAG=latest \
  docker compose -f docker-compose.bundle.yml pull
```

Set `IMAGE_REPOSITORY` to the full repository path in Harbor or Artifactory.
The pipeline uses the same `IMAGE_REPOSITORY` variable and masked
`REGISTRY_USER`/`REGISTRY_PASSWORD` variables to publish both standard and
bundled images.

## Testing & CI

```bash
scripts/run_tests.sh        # lint (ruff) + unit + integration in a container
```

- **Unit tests** (`pytest -m "not integration"`) — pure logic, no app boot.
- **Integration tests** (`pytest -m integration`) — run the app against the
  kiro-cli stub via FastAPI TestClient (no real auth, no Docker needed).
- **CI** — `.gitlab-ci.yml` runs lint → unit → integration → build image, written
  for an **airgapped self-hosted GitLab** (internal registry + wheelhouse; see the
  variables at the top of the file).
- **Real kiro-cli validation** stays manual on the workspace (CI runners can't
  do interactive login) — see [DEPLOYMENT.md](docs/DEPLOYMENT.md).

## What's in V2 vs V1

| | V1 (Rust TUI) | V2 (this) |
|-|---------------|-----------|
| Runtime | `cargo run` on host | Docker container |
| Control surface | terminal TUI | host tray icon + web dashboard |
| Sharing | build from source | hand over one image |
| Endpoints | `/v1/*` | `/v1/*` (same) + admin API |
| Concurrency | pool | pool tuned for 50+ agents + queue |
| Long-run safety | — | reaper: zombie reap, leak watch, GC |
| Clean shutdown | — | drains in-flight jobs on stop, no ghost processes |
| Configs | one | standard + airgapped bundle |

---

## Status dot (tray icon)

| Color | Meaning |
|-------|---------|
| 🔴 Red | API not running |
| 🟡 Yellow | Running but not logged in to kiro-cli |
| 🟠 Orange | Error — check the dashboard |
| 🟢 Green | Online and healthy |

---

## Layout

```
kiro-api-docker/
├── app/                 # FastAPI service (runs in the container)
├── tray/                # Host system-tray agent
├── docker/              # Dockerfiles
├── scripts/             # kiro-cli stub, helpers
├── docs/                # Architecture, deployment, dependencies, diagrams
├── docker-compose.yml           # Standard config
├── docker-compose.bundle.yml    # Single self-contained image (airgapped)
└── .env.example
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) to get started.
