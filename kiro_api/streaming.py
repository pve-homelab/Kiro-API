"""SSE stream builders for OpenAI and Anthropic, with keepalive heartbeats.

kiro-cli goes silent while a built-in tool runs; without keepalives a client's
idle watchdog can abort a long turn. Each streaming path emits its protocol's
no-op frame on a timer so the connection stays alive without altering output.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from .config import Config
from .errors import ApiError
from .shims.activity import render_plan, render_tool_call, render_tool_update


async def _with_keepalive(
    events: AsyncIterator[dict], interval: int
) -> AsyncIterator[dict | None]:
    """Yield events; inject a None sentinel (→ keepalive frame) when idle."""
    if interval <= 0:
        async for e in events:
            yield e
        return
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for e in events:
                await queue.put(("event", e))
        except Exception as exc:  # propagate errors through the queue
            await queue.put(("error", exc))
        finally:
            await queue.put(("end", None))

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield None  # keepalive
                continue
            if kind == "event":
                yield payload
            elif kind == "error":
                raise payload
            else:
                break
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _openai_chunk(model: str, rid: str, delta: dict, finish=None) -> dict:
    return {
        "id": rid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


async def openai_sse(
    events: AsyncIterator[dict], model: str, rid: str, config: Config, surface_thinking: bool
) -> AsyncIterator[str]:
    yield f"data: {json.dumps(_openai_chunk(model, rid, {'role': 'assistant'}))}\n\n"
    try:
        async for event in _with_keepalive(events, config.sse_keepalive_interval):
            if event is None:
                yield ": keepalive\n\n"
                continue
            etype = event.get("type")
            if etype == "text":
                yield f"data: {json.dumps(_openai_chunk(model, rid, {'content': event['content']}))}\n\n"
            elif etype == "thinking" and surface_thinking:
                yield f"data: {json.dumps(_openai_chunk(model, rid, {'reasoning_content': event['content']}))}\n\n"
            elif etype == "tool_call" and surface_thinking:
                txt = render_tool_call(event)
                if txt:
                    yield f"data: {json.dumps(_openai_chunk(model, rid, {'reasoning_content': txt}))}\n\n"
            elif etype == "tool_call_update" and surface_thinking:
                txt = render_tool_update(event)
                if txt:
                    yield f"data: {json.dumps(_openai_chunk(model, rid, {'reasoning_content': txt}))}\n\n"
            elif etype == "plan" and surface_thinking:
                txt = render_plan(event)
                if txt:
                    yield f"data: {json.dumps(_openai_chunk(model, rid, {'reasoning_content': txt}))}\n\n"
            elif etype == "done":
                yield f"data: {json.dumps(_openai_chunk(model, rid, {}, event.get('finish_reason', 'stop')))}\n\n"
    except ApiError as exc:
        yield f"data: {json.dumps(exc.openai_body())}\n\n"
    yield "data: [DONE]\n\n"


async def anthropic_sse(
    events: AsyncIterator[dict], model: str, rid: str, config: Config, surface_thinking: bool
) -> AsyncIterator[str]:
    def sse(event_name: str, data: dict) -> str:
        return f"event: {event_name}\ndata: {json.dumps(data)}\n\n"

    yield sse("message_start", {
        "type": "message_start",
        "message": {"id": rid, "type": "message", "role": "assistant",
                    "model": model, "content": [], "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}},
    })
    text_block_open = False
    try:
        async for event in _with_keepalive(events, config.sse_keepalive_interval):
            if event is None:
                yield sse("ping", {"type": "ping"})
                continue
            etype = event.get("type")
            if etype == "text":
                if not text_block_open:
                    yield sse("content_block_start", {
                        "type": "content_block_start", "index": 0,
                        "content_block": {"type": "text", "text": ""}})
                    text_block_open = True
                yield sse("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": event["content"]}})
            elif etype == "done":
                if text_block_open:
                    yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
                yield sse("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": _anthropic_stop(event.get("finish_reason", "stop"))},
                    "usage": {"output_tokens": 0}})
                yield sse("message_stop", {"type": "message_stop"})
    except ApiError as exc:
        yield sse("error", exc.anthropic_body())


def _anthropic_stop(finish: str) -> str:
    return {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}.get(finish, "end_turn")
