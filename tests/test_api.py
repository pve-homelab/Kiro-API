"""End-to-end API tests: boot the app against the stub, hit every protocol."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from kiro_api.auth import LOGGED_IN
from kiro_api.server import create_app

pytestmark = pytest.mark.integration


async def _boot(config):
    app = create_app(config)
    await app.state.auth.refresh_state(force=True)
    await app.state.pool.start()
    return app


async def _shutdown(app):
    await app.state.pool.stop()
    await app.state.auth.stop()


async def test_health_and_ready(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            h = await c.get("/health")
            assert h.status_code == 200
            assert h.json()["status"] == "ok"
            r = await c.get("/ready")
            # logged in (stub) + workers available → ready
            assert r.status_code == 200
            assert r.json()["ready"] is True
    finally:
        await _shutdown(app)


async def test_openai_chat_nonstream(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/v1/chat/completions", json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hello world"}],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["object"] == "chat.completion"
            assert "stub reply" in data["choices"][0]["message"]["content"]
            assert data["usage"]["total_tokens"] >= 0
    finally:
        await _shutdown(app)


async def test_openai_chat_stream(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            async with c.stream("POST", "/v1/chat/completions", json={
                "model": "auto", "stream": True,
                "messages": [{"role": "user", "content": "stream me"}],
            }) as resp:
                assert resp.status_code == 200
                chunks = [line async for line in resp.aiter_lines()]
            body = "\n".join(chunks)
            assert "chat.completion.chunk" in body
            assert "[DONE]" in body
            assert "stub reply" in body
    finally:
        await _shutdown(app)


async def test_anthropic_messages(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/v1/messages", json={
                "model": "auto",
                "system": "be terse",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["type"] == "message"
            assert data["content"][0]["type"] == "text"
            assert "stub reply" in data["content"][0]["text"]
    finally:
        await _shutdown(app)


async def test_anthropic_count_tokens(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/v1/messages/count_tokens", json={
                "messages": [{"role": "user", "content": "a" * 400}],
            })
            assert resp.status_code == 200
            assert resp.json()["input_tokens"] >= 100
    finally:
        await _shutdown(app)


async def test_acp_chat(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/acp/chat", json={
                "model": "auto",
                "messages": [{"role": "user", "content": "native acp"}],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "stub reply" in data["content"]
            assert any(e["type"] == "done" for e in data["events"])
    finally:
        await _shutdown(app)


async def test_models_endpoint(config):
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        # Warm a worker so the live catalogue is populated.
        async with app.state.pool.lease() as w:
            await w.new_session()
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.get("/v1/models")
            assert resp.status_code == 200
            ids = [m["id"] for m in resp.json()["data"]]
            assert "stub-model" in ids or "auto" in ids
    finally:
        await _shutdown(app)


async def test_auth_gate_returns_401(config, monkeypatch):
    monkeypatch.setenv("KIRO_STUB_LOGGED_OUT", "1")
    app = await _boot(config)
    assert app.state.auth.state.state != LOGGED_IN
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/v1/chat/completions", json={
                "model": "auto", "messages": [{"role": "user", "content": "x"}],
            })
            assert resp.status_code == 401
            assert resp.json()["error"]["type"] == "authentication_error"
    finally:
        await _shutdown(app)


async def test_upstream_error_classified(config, monkeypatch):
    monkeypatch.setenv("KIRO_STUB_ERROR", "Rate limit exceeded, retry after 30")
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/v1/chat/completions", json={
                "model": "auto", "messages": [{"role": "user", "content": "x"}],
            })
            assert resp.status_code == 429
            assert resp.json()["error"]["type"] == "rate_limit_error"
            assert resp.headers.get("Retry-After") == "30"
    finally:
        await _shutdown(app)


async def test_bridge_auth_key(config):
    config.auth_key = "secret123"
    app = await _boot(config)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            no_key = await c.post("/v1/chat/completions", json={
                "model": "auto", "messages": [{"role": "user", "content": "x"}]})
            assert no_key.status_code == 401
            ok = await c.post("/v1/chat/completions",
                              headers={"Authorization": "Bearer secret123"},
                              json={"model": "auto",
                                    "messages": [{"role": "user", "content": "x"}]})
            assert ok.status_code == 200
    finally:
        await _shutdown(app)
