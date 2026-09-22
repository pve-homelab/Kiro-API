# syntax=docker/dockerfile:1
# ===========================================================================
# Kiro-API V2 — standard image.
#
# Contains the FastAPI service only. kiro-cli and its credentials are mounted
# from the host at runtime (see docker-compose.yml). Python deps are installed
# from PyPI OR from a pre-staged local wheelhouse for airgapped builds.
#
# Airgapped build:
#   1. On an internet-connected box, stage wheels:
#        pip download -r app/requirements.txt -d wheelhouse
#   2. Copy wheelhouse/ into the build context.
#   3. Build with:  docker build --build-arg OFFLINE=1 -f docker/Dockerfile .
# ===========================================================================
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

# --- OCI labels ------------------------------------------------------------
LABEL org.opencontainers.image.title="Kiro-API V2" \
      org.opencontainers.image.description="OpenAI-compatible /v1 API backed by kiro-cli" \
      org.opencontainers.image.version="2.0.0" \
      org.opencontainers.image.source="https://github.com/pve-homelab/Kiro-API"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root user for the service.
RUN groupadd --gid 10001 kiro \
 && useradd --uid 10001 --gid kiro --create-home --shell /usr/sbin/nologin kiro

WORKDIR /srv

# --- Python dependencies ---------------------------------------------------
ARG OFFLINE=0
COPY app/requirements.txt /srv/app/requirements.txt
# When OFFLINE=1, install strictly from the staged wheelhouse (no network).
COPY wheelhouse* /srv/wheelhouse
RUN if [ "$OFFLINE" = "1" ]; then \
        pip install --no-index --find-links=/srv/wheelhouse -r /srv/app/requirements.txt ; \
    else \
        pip install -r /srv/app/requirements.txt ; \
    fi \
 && rm -rf /srv/wheelhouse

# --- Application -----------------------------------------------------------
COPY app /srv/app

# Workspace for kiro-cli runs (mounted or empty).
RUN mkdir -p /work && chown -R kiro:kiro /work /srv

USER kiro

# API + dashboard ports (documented; actual bind from env).
EXPOSE 8787 8788

# Container-level healthcheck hits the API health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,os,sys; \
url='http://127.0.0.1:'+os.environ.get('KIRO_API_PORT','8787')+'/health'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=3).status==200 else 1)" || exit 1

ENTRYPOINT ["python3", "-m", "app.server"]
