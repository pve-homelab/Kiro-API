"""/metrics endpoint + per-request reasoning-effort control."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from kiro_api.server import create_app
from kiro_api.service import normalize_effort

pytestmark = pytest.mark.integration


def test_normalize_effort():
    assert normalize_effort("high") == "high"
    assert normalize_effort("minimal") == "low"
    assert normalize_effort("MAX") == "max"
    assert normalize_effort("maximum") == "max"
    assert normalize_effort("bogus") is None
    assert normalize_effort(None) is None


async def _boot(config):
    app = create_app(config)
    await app.state.auth.refresh_state(force=True)
    await app.state.pool.start()
    return app


async def _shutdown(app):
    await app.state.pool.stop()
    await app.state.auth.stop()


async def test_metrics_prometheus_format(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            # Generate one request so counters are non-trivial.
            await c.post("/v1/chat/completions",
                         json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]})
            r = await c.get("/metrics")
            assert r.status_code == 200
            assert "text/plain" in r.headers["content-type"]
            body = r.text
            # Prometheus exposition lines present.
            assert "# TYPE kiro_api_requests_total counter" in body
            assert "kiro_api_requests_total " in body
            assert "kiro_api_workers " in body
            assert "kiro_api_logged_in 1" in body
    finally:
        await _shutdown(app)


async def test_metrics_counts_errors(config, monkeypatch):
    monkeypatch.setenv("KIRO_STUB_ERROR", "Rate limit exceeded")
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            await c.post("/v1/chat/completions",
                         json={"model": "auto", "messages": [{"role": "user", "content": "x"}]})
            r = await c.get("/metrics")
            assert "kiro_api_errors_total " in r.text
            assert 'kiro_api_errors_by_category_total{category="rate_limit"}' in r.text
    finally:
        await _shutdown(app)


async def test_effort_accepted_and_forwarded(config):
    """A request carrying reasoning_effort completes (the stub accepts /effort)."""
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/v1/chat/completions", json={
                "model": "auto",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert r.status_code == 200
            assert "stub reply" in r.json()["choices"][0]["message"]["content"]
            # Header form also works.
            r2 = await c.post("/v1/chat/completions",
                              headers={"X-Kiro-Effort": "max"},
                              json={"model": "auto",
                                    "messages": [{"role": "user", "content": "hi"}]})
            assert r2.status_code == 200
    finally:
        await _shutdown(app)


async def test_effort_unknown_level_ignored(config):
    """An unknown effort must not break the turn (best-effort, logged + skipped)."""
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/v1/chat/completions", json={
                "model": "auto",
                "reasoning_effort": "nonsense",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert r.status_code == 200
    finally:
        await _shutdown(app)
