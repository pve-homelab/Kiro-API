"""Resilience: the anti-'stuck' guarantees — a wedged/silent worker must never
hang a request forever; it times out cleanly and the worker is marked dead."""
from __future__ import annotations

import time

import pytest

from kiro_api.acp.client import ACPWorker

pytestmark = pytest.mark.integration


async def _drain(worker: ACPWorker, idle_timeout: float):
    sid = await worker.new_session()
    seen = []
    async for ev in worker.prompt_stream(
        sid, [{"type": "text", "text": "hi"}], idle_timeout=idle_timeout
    ):
        seen.append(ev)
    return seen


async def test_silent_worker_times_out(stub_bin, monkeypatch):
    """A worker that accepts the prompt but never responds must time out."""
    monkeypatch.setenv("KIRO_STUB_HANG", "1")
    w = ACPWorker("hang", command=stub_bin)
    await w.start()
    await w.initialize()
    try:
        start = time.time()
        events = await _drain(w, idle_timeout=2.0)
        elapsed = time.time() - start
        assert elapsed < 10  # bounded, not forever
        assert events[-1]["type"] == "error"
        assert events[-1]["category"] == "timeout"
        assert not w.alive()  # marked dead so the pool will replace it
    finally:
        await w.stop()


async def test_hard_turn_timeout(stub_bin, monkeypatch):
    """The hard turn ceiling fires even if the worker keeps trickling output."""
    monkeypatch.setenv("KIRO_STUB_HANG", "1")
    w = ACPWorker("hard", command=stub_bin)
    await w.start()
    await w.initialize()
    try:
        sid = await w.new_session()
        events = []
        async for ev in w.prompt_stream(
            sid, [{"type": "text", "text": "hi"}], turn_timeout=1.0, idle_timeout=30.0
        ):
            events.append(ev)
        assert events[-1]["type"] == "error"
        assert events[-1]["category"] == "timeout"
    finally:
        await w.stop()
