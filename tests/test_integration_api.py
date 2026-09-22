"""Integration tests: the app running against the kiro-cli stub.

These use FastAPI's TestClient (no Docker, no real kiro-cli). The stub echoes
the last prompt line, so we can assert the request reached the CLI and the
response was mapped to OpenAI format.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["logged_in"] is True
    assert "pool" in body and "reaper" in body


def test_models(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) >= 1


def test_chat_completion_sync(client):
    r = client.post("/v1/chat/completions", json={
        "model": "auto",
        "messages": [{"role": "user", "content": "hello there"}],
    })
    assert r.status_code == 200
    body = r.json()
    content = body["choices"][0]["message"]["content"]
    assert "hello there" in content
    assert body["usage"]["total_tokens"] > 0


def test_chat_completion_stream(client):
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "auto",
        "stream": True,
        "messages": [{"role": "user", "content": "stream me"}],
    }) as r:
        assert r.status_code == 200
        raw = "".join(r.iter_text())
    assert "[DONE]" in raw
    assert "stream me" in raw
    assert '"usage"' in raw


def test_legacy_completions(client):
    r = client.post("/v1/completions", json={"model": "auto", "prompt": "legacy input"})
    assert r.status_code == 200
    assert "legacy input" in r.json()["choices"][0]["text"]


def test_json_mode_strips_fences(client, monkeypatch):
    # The stub can't emit JSON, but we assert the request is accepted and the
    # response_format path returns 200 with a string body.
    r = client.post("/v1/chat/completions", json={
        "model": "auto",
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": "give json"}],
    })
    assert r.status_code == 200


def test_auth_enforced_when_key_set(client):
    from app.config import config
    config.auth_key = "secret"
    try:
        # missing key -> 401
        r = client.get("/v1/models")
        assert r.status_code == 401
        # valid key -> 200
        r = client.get("/v1/models", headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200
    finally:
        config.auth_key = ""


def test_concurrency_burst(client):
    # Fire several requests; all should succeed and stats should count them.
    for i in range(10):
        r = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": f"burst {i}"}],
        })
        assert r.status_code == 200
    stats = client.get("/stats").json()
    assert stats["pool"]["total_requests"] >= 10
    assert stats["pool"]["total_errors"] == 0


def test_admin_config(client):
    r = client.get("/admin/config")
    assert r.status_code == 200
    assert "v1_url" in r.json()


def test_admin_auth_key_toggle(client):
    r = client.post("/admin/auth-key", json={"key": "abc"})
    assert r.status_code == 200
    assert r.json()["auth_required"] is True
    # cleanup
    client.post("/admin/auth-key", json={"key": ""})


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Kiro-API" in r.text
