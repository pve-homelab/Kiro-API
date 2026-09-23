"""Error taxonomy → native OpenAI/Anthropic error shapes.

Every failure the service surfaces is classified into one category, which maps
to the correct HTTP status and each API's native error envelope so that harness
retry/back-off logic behaves correctly (a 429 must look like a rate limit, not a
generic 502).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- categories ---
RATE_LIMIT = "rate_limit"
OVERLOADED = "overloaded"
TIMEOUT = "timeout"
AUTH = "auth"
UPSTREAM = "upstream"


@dataclass
class ApiError(Exception):
    category: str
    message: str
    retry_after: int | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    @property
    def http_status(self) -> int:
        return {
            RATE_LIMIT: 429,
            OVERLOADED: 503,
            TIMEOUT: 504,
            AUTH: 401,
            UPSTREAM: 502,
        }.get(self.category, 502)

    def openai_body(self) -> dict:
        otype = {
            RATE_LIMIT: "rate_limit_error",
            OVERLOADED: "server_error",
            TIMEOUT: "server_error",
            AUTH: "authentication_error",
            UPSTREAM: "server_error",
        }.get(self.category, "server_error")
        return {"error": {"message": self.message, "type": otype, "code": self.category}}

    def anthropic_body(self) -> dict:
        atype = {
            RATE_LIMIT: "rate_limit_error",
            OVERLOADED: "overloaded_error",
            TIMEOUT: "api_error",
            AUTH: "authentication_error",
            UPSTREAM: "api_error",
        }.get(self.category, "api_error")
        return {"type": "error", "error": {"type": atype, "message": self.message}}

    def headers(self) -> dict:
        return {"Retry-After": str(self.retry_after)} if self.retry_after else {}


_PATTERNS = [
    (re.compile(r"rate.?limit|throttl|quota|429|too many requests", re.I), RATE_LIMIT),
    (re.compile(r"overload|unavailable|capacity|503", re.I), OVERLOADED),
    (re.compile(r"time.?out|deadline|timed out", re.I), TIMEOUT),
    (re.compile(r"not logged in|unauthor|forbidden|401|403|expired|no valid.*token", re.I), AUTH),
]


def classify(message: str) -> str:
    """Classify a free-text upstream error message into a category."""
    for pattern, category in _PATTERNS:
        if pattern.search(message or ""):
            return category
    return UPSTREAM


def from_message(message: str) -> ApiError:
    cat = classify(message)
    retry = None
    m = re.search(r"retry.{0,10}?(\d+)", message or "", re.I)
    if m and cat == RATE_LIMIT:
        retry = int(m.group(1))
    return ApiError(cat, message or "upstream error", retry_after=retry)
