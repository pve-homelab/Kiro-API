# Dependencies (airgapped pull list)

Everything below must be staged manually on the airgapped workspace, because the
box has no internet. Nothing in the build runs `curl | bash` or reaches the
network at container start.

## 1. Base container image

| Item | Value |
|------|-------|
| Image | `python:3.12-slim` |
| Registry | Docker Hub (or your internal mirror) |
| Verified digest | `sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9` |

Pull it on a connected box and transfer, or mirror it internally:

```bash
docker pull python:3.12-slim
# optional: pin by digest
docker pull python@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
```

Override the base with `--build-arg PYTHON_IMAGE=<your-mirror>/python:3.12-slim`
or the `PYTHON_IMAGE` env var used by compose.

## 2. Python packages (container service)

Pinned in `app/requirements.txt`:

| Package | Version |
|---------|---------|
| fastapi | 0.115.6 |
| uvicorn[standard] | 0.34.0 |
| pydantic | 2.10.4 |
| python-multipart | 0.0.20 |

`uvicorn[standard]` pulls in `uvloop`, `httptools`, `websockets`, `watchfiles`,
and `python-dotenv` as transitive deps — `pip download` captures them all.

### Stage a wheelhouse (offline install)

On an internet-connected box with the **same platform** (linux/amd64):

```bash
pip download -r app/requirements.txt -d wheelhouse
```

Copy `wheelhouse/` into the build context, then build offline:

```bash
docker build --build-arg OFFLINE=1 -f docker/Dockerfile.bundle -t kiro-api-bundle:2.0.0 .
```

With `OFFLINE=1` pip installs strictly from `wheelhouse/` with `--no-index`.

## 3. kiro-cli binary

- Stays installed on the **host**; the standard compose mounts it read-only.
- For the **bundle image**, place the real binary at `vendor/kiro-cli` before
  building — it is baked to `/usr/local/bin/kiro-cli`.
- If `vendor/kiro-cli` is absent, the offline **stub** (`scripts/kiro-cli-stub.sh`)
  is baked in so the image runs end-to-end for validation. Replace it with the
  real binary for production.

Verify on the real workspace where credentials live (mount these paths):

```bash
which kiro-cli
ls -la ~/.kiro ~/.aws 2>/dev/null
```

Set `HOST_KIRO_CLI_BIN`, `HOST_KIRO_CONFIG_DIR`, and `HOST_AWS_DIR` in `.env`
to match what you find.

## 4. Host tray agent packages

Pinned in `tray/requirements.txt`:

| Package | Version |
|---------|---------|
| pystray | 0.19.5 |
| Pillow | 11.1.0 |
| requests | 2.32.3 |

System packages needed on the host for the tray + dialogs + clipboard:

```bash
sudo apt-get install -y \
  python3-venv python3-tk \
  gir1.2-appindicator3-0.1 \
  libnotify-bin \
  xclip            # or: wl-clipboard   (on Wayland)
```

Stage these wheels offline the same way:

```bash
pip download -r tray/requirements.txt -d tray/wheelhouse
```

## 4b. Dev/test packages (CI + local testing)

Pinned in `requirements-dev.txt`:

| Package | Version |
|---------|---------|
| pytest | 8.3.4 |
| pytest-asyncio | 0.25.2 |
| httpx | 0.28.1 |
| ruff | 0.9.2 |

Stage offline the same way:

```bash
pip download -r app/requirements.txt -r requirements-dev.txt -d wheelhouse
```

CI (`.gitlab-ci.yml`) installs these from your internal PyPI mirror or, with
`USE_WHEELHOUSE=1`, from the staged `wheelhouse/` with `--no-index`.

## 5. Diagram rendering (optional, docs only)

| Item | Value |
|------|-------|
| Image | `minlag/mermaid-cli:latest` |

Only needed to re-render `docs/diagrams/*.mmd` → PNG. The PNGs are committed, so
this is not required to run the app. To re-render offline, `docker save` the
mermaid-cli image on a connected box and `docker load` it on the host, then run
`scripts/render_diagrams.sh`.

## 6. CI prerequisites (self-hosted airgapped GitLab)

The `.gitlab-ci.yml` pipeline will not pass until these exist in your
environment. None of them reach the public internet — they must be mirrored
internally or staged.

### Images (must be in your internal registry)

Set `INTERNAL_REGISTRY` in the pipeline to the registry prefix that holds these:

| Image | Used by | CI variable |
|-------|---------|-------------|
| `python:3.12-slim` | lint + unit + integration jobs | `PY_IMAGE` |
| `docker:27-dind` | build-image job (docker-in-docker service) | `DIND_IMAGE` |
| `docker:27-cli` | build-image job (docker client) | `DOCKER_CLI_IMAGE` |

> If your runners bind the host Docker socket instead of using dind, the
> `docker:27-dind` service is not needed — remove the `services:` block in the
> `build-image` job (documented inline).

### Python packages (internal index OR wheelhouse)

CI installs `app/requirements.txt` + `requirements-dev.txt`. Provide one of:

- **Internal PyPI mirror** — set `PIP_INDEX_URL` to it (e.g.
  `https://pypi.internal.example.com/simple`), leave `USE_WHEELHOUSE=0`; **or**
- **Committed/staged wheelhouse** — set `USE_WHEELHOUSE=1`; CI installs from
  `./wheelhouse` with `--no-index`. Stage it with:
  ```bash
  pip download -r app/requirements.txt -r requirements-dev.txt -d wheelhouse
  ```

### Runners

At least one GitLab runner with the **`docker` executor** for the lint/test
jobs. The `build-image` job additionally needs docker-in-docker enabled (or a
socket-bound runner). Tag runners and target them via `rules`/`tags` if needed.

### CI variables summary

| Variable | Purpose | Example |
|----------|---------|---------|
| `INTERNAL_REGISTRY` | Registry prefix for all base images | `registry.internal.example.com/library` |
| `PIP_INDEX_URL` | Internal PyPI mirror (blank if using wheelhouse) | `https://pypi.internal.example.com/simple` |
| `USE_WHEELHOUSE` | `1` = install offline from `./wheelhouse` | `0` |

### Not run in CI

Real-kiro-cli end-to-end validation is **not** part of CI — runners have no
interactive login. Do it manually on the workspace after deploy (tray Login →
real chat call → parallel-agent burst → tune `KIRO_API_MAX_CONCURRENCY`); see
[DEPLOYMENT.md](DEPLOYMENT.md).

## Summary: what to carry across the airgap

1. `python:3.12-slim` image (or your mirror).
2. `wheelhouse/` for `app/requirements.txt` (linux/amd64).
3. `tray/wheelhouse/` for `tray/requirements.txt`.
4. The real `kiro-cli` binary → `vendor/kiro-cli`.
5. This repository.
6. (optional) `minlag/mermaid-cli` image to re-render diagrams.

For CI on the airgapped GitLab, additionally mirror internally (see section 6):

7. `python:3.12-slim`, `docker:27-dind`, `docker:27-cli` in your internal registry.
8. `requirements-dev.txt` packages in your internal PyPI mirror, or a staged
   `wheelhouse/` for offline install.

Or simplest of all: build the **bundle image** on a connected box and ship a
single `docker save … | gzip` tarball (see DEPLOYMENT.md).
