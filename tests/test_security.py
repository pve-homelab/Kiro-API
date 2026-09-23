"""Security hardening: constant-time key compare, body-size limit, rate limiting."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from kiro_api.routes.common import _key_matches
from kiro_api.server import create_app

pytestmark = pytest.mark.integration


def test_key_matches_constant_time():
    assert _key_matches("secret", "secret") is True
    assert _key_matches("wrong", "secret") is False
    assert _key_matches("", "secret") is False
    assert _key_matches(None, "secret") is False


async def _boot(config):
    app = create_app(config)
    await app.state.auth.refresh_state(force=True)
    await app.state.pool.start()
    return app


async def _shutdown(app):
    await app.state.pool.stop()
    await app.state.auth.stop()


async def test_body_limit_413(config):
    config.max_body_bytes = 1024  # 1 KiB
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            big = "x" * 5000
            resp = await c.post("/v1/chat/completions",
                                json={"model": "auto",
                                      "messages": [{"role": "user", "content": big}]})
            assert resp.status_code == 413
    finally:
        await _shutdown(app)


async def test_body_limit_allows_small(config):
    config.max_body_bytes = 1024 * 1024
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/v1/chat/completions",
                                json={"model": "auto",
                                      "messages": [{"role": "user", "content": "hi"}]})
            assert resp.status_code == 200
    finally:
        await _shutdown(app)


async def test_rate_limit_429(config):
    config.rate_limit = 3
    config.rate_limit_window = 60
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            payload = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
            codes = []
            for _ in range(5):
                r = await c.post("/v1/chat/completions", json=payload)
                codes.append(r.status_code)
            # First 3 allowed, then 429s.
            assert codes[:3] == [200, 200, 200]
            assert 429 in codes[3:]
            # A 429 carries Retry-After.
            last = await c.post("/v1/chat/completions", json=payload)
            assert last.status_code == 429
            assert "Retry-After" in last.headers
    finally:
        await _shutdown(app)


async def test_health_exempt_from_rate_limit(config):
    config.rate_limit = 1
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            for _ in range(5):
                r = await c.get("/health")
                assert r.status_code == 200  # never rate-limited
    finally:
        await _shutdown(app)
