"""Security middleware: request body size limit + per-client rate limiting.

Both are opt-in-friendly: the body limit has a sane default; rate limiting is
off unless configured. Applied as ASGI middleware so every route (including
streaming) is covered before the handler runs.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Endpoints that must never be rate-limited or size-checked (liveness probes).
_EXEMPT_PATHS = {"/health", "/ready", "/stats", "/metrics"}


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured max (413)."""

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        if self.max_bytes > 0 and request.url.path not in _EXEMPT_PATHS:
            cl = request.headers.get("content-length")
            if cl is not None:
                try:
                    if int(cl) > self.max_bytes:
                        return JSONResponse(
                            {"error": {"message": "request body too large",
                                       "type": "invalid_request_error"}},
                            status_code=413,
                        )
                except ValueError:
                    pass
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-client rate limit. Client = X-Forwarded-For (first hop)
    or the peer IP. Returns 429 with Retry-After when exceeded."""

    def __init__(self, app, limit: int, window: int) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = max(1, window)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if self.limit <= 0 or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        now = time.time()
        client = self._client(request)
        bucket = self._hits[client]
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            retry = int(self.window - (now - bucket[0])) + 1
            return JSONResponse(
                {"error": {"message": "rate limit exceeded", "type": "rate_limit_error"}},
                status_code=429,
                headers={"Retry-After": str(max(1, retry))},
            )
        bucket.append(now)
        # Opportunistic cleanup so idle clients don't accumulate forever.
        if len(self._hits) > 4096:
            for k in [k for k, v in self._hits.items() if not v]:
                self._hits.pop(k, None)
        return await call_next(request)
