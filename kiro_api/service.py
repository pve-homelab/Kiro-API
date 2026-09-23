"""ShimService — orchestrates one turn across auth + pool + worker.

Flow for every request:
  1. auth gate: if not logged in, fail fast with a native auth error.
  2. lease a worker from the elastic pool (backpressure → 503/429).
  3. open a fresh ACP session, optionally set the model, register MCP servers.
  4. stream normalized events (or aggregate them for non-streaming callers).

The service is protocol-agnostic; the route shims turn its normalized output
into OpenAI/Anthropic/ACP shapes.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from .auth import AuthManager
from .config import Config
from .errors import AUTH, ApiError, from_message
from .pool import WorkerPool
from .shims.prompt import build_prompt_blocks, estimate_tokens

log = logging.getLogger("kiro-api.service")


class TurnResult:
    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.finish_reason = "stop"
        self.usage: dict = {}
        self.prompt_tokens = 0
        self.completion_tokens = 0


class Metrics:
    """Lightweight in-process counters exposed on /metrics (Prometheus text)."""

    def __init__(self) -> None:
        self.requests_total = 0
        self.errors_total = 0
        self.errors_by_category: dict[str, int] = {}
        self.completion_tokens_total = 0
        self.prompt_tokens_total = 0

    def record_request(self) -> None:
        self.requests_total += 1

    def record_error(self, category: str) -> None:
        self.errors_total += 1
        self.errors_by_category[category] = self.errors_by_category.get(category, 0) + 1

    def record_tokens(self, prompt: int, completion: int) -> None:
        self.prompt_tokens_total += prompt
        self.completion_tokens_total += completion

    def snapshot(self) -> dict:
        return {
            "requests_total": self.requests_total,
            "errors_total": self.errors_total,
            "errors_by_category": dict(self.errors_by_category),
            "prompt_tokens_total": self.prompt_tokens_total,
            "completion_tokens_total": self.completion_tokens_total,
        }


# reasoning_effort synonyms accepted from clients → kiro-cli levels.
_EFFORT_ALIASES = {
    "minimal": "low", "low": "low", "medium": "medium", "high": "high",
    "xhigh": "xhigh", "very_high": "xhigh", "max": "max", "maximum": "max",
}


def normalize_effort(value) -> str | None:
    """Map an OpenAI-style reasoning_effort (or plain level) to a kiro-cli level."""
    if value is None:
        return None
    return _EFFORT_ALIASES.get(str(value).strip().lower())


class ShimService:
    def __init__(self, config: Config, pool: WorkerPool, auth: AuthManager) -> None:
        self.config = config
        self.pool = pool
        self.auth = auth
        self.metrics = Metrics()

    def _auth_gate(self) -> None:
        if not self.auth.state.logged_in:
            raise ApiError(AUTH, f"not authenticated: {self.auth.state.detail}")

    def _timeout(self, long_running: bool) -> float:
        return self.config.timeout_long if long_running else self.config.timeout_short

    async def run_stream(
        self,
        messages: list[dict],
        model: str,
        *,
        system=None,
        mcp_servers: list[dict] | None = None,
        long_running: bool = False,
        cwd: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[dict]:
        """Yield normalized events for a turn."""
        self._auth_gate()
        self.metrics.record_request()
        model = model or self.config.default_model
        effort = normalize_effort(effort) or (self.config.default_effort or None)
        blocks = build_prompt_blocks(messages, system=system)
        turn_timeout = float(self._timeout(long_running))
        # Idle (no-output) ceiling: generous for long turns with tool calls, but
        # never unbounded. Bounded below the hard turn timeout.
        idle_timeout = min(turn_timeout, float(self.config.timeout_short))
        try:
            # Lease wait is capped separately so a saturated pool fails fast
            # rather than blocking a client for the whole turn budget.
            async with self.pool.lease(timeout=min(turn_timeout, 60.0)) as worker:
                session_id = await worker.new_session(cwd=cwd, mcp_servers=mcp_servers)
                if model and model != "auto" and model != worker._current_model:
                    await worker.set_model(session_id, model)
                if effort:
                    await worker.set_effort(session_id, effort)
                async for event in worker.prompt_stream(
                    session_id, blocks,
                    turn_timeout=turn_timeout, idle_timeout=idle_timeout,
                ):
                    if event.get("type") == "error":
                        # Reclassify upstream text into a proper category.
                        raise from_message(event.get("message", "upstream error"))
                    yield event
        except ApiError as exc:
            self.metrics.record_error(exc.category)
            raise
        except Exception as exc:  # noqa: BLE001
            err = from_message(str(exc))
            self.metrics.record_error(err.category)
            raise err from exc

    async def run(
        self,
        messages: list[dict],
        model: str,
        *,
        system=None,
        mcp_servers: list[dict] | None = None,
        long_running: bool = False,
        cwd: str | None = None,
        effort: str | None = None,
    ) -> TurnResult:
        """Aggregate a turn into a single result (non-streaming callers)."""
        result = TurnResult()
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        prompt_text = "\n".join(
            (m.get("content") if isinstance(m.get("content"), str) else "")
            for m in messages
            if isinstance(m, dict)
        )
        result.prompt_tokens = estimate_tokens(prompt_text)
        async for event in self.run_stream(
            messages, model, system=system, mcp_servers=mcp_servers,
            long_running=long_running, cwd=cwd, effort=effort,
        ):
            etype = event.get("type")
            if etype == "text":
                content_parts.append(event.get("content", ""))
            elif etype == "thinking":
                reasoning_parts.append(event.get("content", ""))
            elif etype == "done":
                result.finish_reason = event.get("finish_reason", "stop")
                result.usage = event.get("usage", {}) or {}
        result.content = "".join(content_parts)
        result.reasoning = "".join(reasoning_parts)
        result.completion_tokens = estimate_tokens(result.content)
        self.metrics.record_tokens(result.prompt_tokens, result.completion_tokens)
        return result

    def models(self) -> list[dict]:
        """Live model catalogue from any warm worker, else the configured default."""
        for w in self.pool._workers:
            if w.available_models:
                return w.available_models
        return [{"id": self.config.default_model, "name": self.config.default_model, "description": ""},
                {"id": "auto", "name": "auto", "description": ""}]
