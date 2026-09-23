"""Worker + pool: spawn, lease, parallelism, backpressure, dead-worker recovery."""
from __future__ import annotations

import asyncio

import pytest

from kiro_api.acp.client import ACPWorker
from kiro_api.errors import ApiError
from kiro_api.pool import WorkerPool

pytestmark = pytest.mark.integration


async def _run_turn(worker: ACPWorker, text: str) -> str:
    sid = await worker.new_session()
    out = []
    async for ev in worker.prompt_stream(sid, [{"type": "text", "text": text}]):
        if ev["type"] == "text":
            out.append(ev["content"])
    return "".join(out)


async def test_single_worker_turn(stub_bin):
    w = ACPWorker("t", command=stub_bin)
    await w.start()
    await w.initialize()
    try:
        reply = await _run_turn(w, "ping")
        assert "stub reply" in reply
        assert "ping" in reply
        assert w.available_models  # captured from session/new
        assert w.protocol_version == 1
        assert w.agent_name == "kiro-cli-stub"
    finally:
        await w.stop()


async def test_incompatible_protocol_fails_loudly(stub_bin, monkeypatch):
    from kiro_api.acp.client import ACPError
    monkeypatch.setenv("KIRO_STUB_PROTOCOL", "999")
    w = ACPWorker("bad", command=stub_bin)
    await w.start()
    try:
        with pytest.raises(ACPError) as exc:
            await w.initialize()
        assert "incompatible ACP protocol" in str(exc.value)
    finally:
        await w.stop()


async def test_pool_lease_and_stats(config):
    pool = WorkerPool(config)
    await pool.start()
    try:
        async with pool.lease() as worker:
            assert worker.alive()
            reply = await _run_turn(worker, "hi")
            assert "stub reply" in reply
        stats = pool.stats()
        assert stats["workers_total"] >= 1
        assert stats["workers_busy"] == 0
    finally:
        await pool.stop()


async def test_pool_parallelism(config):
    config.max_workers = 5
    pool = WorkerPool(config)
    await pool.start()
    try:
        async def one(i):
            async with pool.lease() as w:
                return await _run_turn(w, f"req{i}")
        results = await asyncio.gather(*(one(i) for i in range(5)))
        assert all("stub reply" in r for r in results)
        assert pool.stats()["workers_total"] <= 5
    finally:
        await pool.stop()


async def test_pool_backpressure(config):
    config.max_workers = 1
    config.max_queue = 0  # no queue → immediate reject when busy
    pool = WorkerPool(config)
    await pool.start()
    try:
        async with pool.lease():
            # pool is now saturated; a second lease with 0 queue must reject
            with pytest.raises(ApiError):
                async with pool.lease(timeout=0.5):
                    pass
    finally:
        await pool.stop()


async def test_dead_worker_replaced(config):
    pool = WorkerPool(config)
    await pool.start()
    try:
        async with pool.lease() as w:
            await _run_turn(w, "x")
            w._kill_tree()  # simulate a crash mid-life
        # Next lease must yield a fresh, alive worker.
        async with pool.lease() as w2:
            assert w2.alive()
            reply = await _run_turn(w2, "again")
            assert "stub reply" in reply
    finally:
        await pool.stop()
