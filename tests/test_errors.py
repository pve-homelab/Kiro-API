"""Error taxonomy → native shapes + classification."""
from __future__ import annotations

from kiro_api.errors import (
    AUTH,
    OVERLOADED,
    RATE_LIMIT,
    TIMEOUT,
    ApiError,
    classify,
    from_message,
)


def test_classify():
    assert classify("Rate limit exceeded, retry after 30") == RATE_LIMIT
    assert classify("service is overloaded") == OVERLOADED
    assert classify("operation timed out") == TIMEOUT
    assert classify("not logged in") == AUTH
    assert classify("some random failure") == "upstream"


def test_http_status_mapping():
    assert ApiError(RATE_LIMIT, "x").http_status == 429
    assert ApiError(OVERLOADED, "x").http_status == 503
    assert ApiError(TIMEOUT, "x").http_status == 504
    assert ApiError(AUTH, "x").http_status == 401
    assert ApiError("upstream", "x").http_status == 502


def test_openai_body_shape():
    body = ApiError(RATE_LIMIT, "slow down").openai_body()
    assert body["error"]["type"] == "rate_limit_error"
    assert body["error"]["message"] == "slow down"


def test_anthropic_body_shape():
    body = ApiError(OVERLOADED, "busy").anthropic_body()
    assert body["type"] == "error"
    assert body["error"]["type"] == "overloaded_error"


def test_retry_after_extracted():
    err = from_message("Rate limit hit, retry after 42 seconds")
    assert err.category == RATE_LIMIT
    assert err.retry_after == 42
    assert err.headers()["Retry-After"] == "42"
