"""The stated ceiling: ~100 parallel workers. Verifies real subprocess spawning
and that the pool holds up at the top of the supported range."""
from __future__ import annotations

import asyncio
import os

import pytest

from kiro_api.acp.client import ACPWorker
from kiro_api.pool import WorkerPool

pytestmark = pytest.mark.integration


async def _turn(worker: ACPWorker, text: str) -> str:
    sid = await worker.new_session()
    out = []
    async for ev in worker.prompt_stream(sid, [{"type": "text", "text": text}], idle_timeout=15):
        if ev["type"] == "text":
            out.append(ev["content"])
    return "".join(out)


def _child_count() -> int:
    me = os.getpid()
    n = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                fields = fh.read().split()
            if int(fields[3]) == me:
                n += 1
        except (OSError, ValueError, IndexError):
            continue
    return n


async def test_100_workers_parallel(config):
    config.max_workers = 100
    config.max_queue = 400
    pool = WorkerPool(config)
    await pool.start()
    try:
        # Hold all 100 leases open simultaneously to force 100 live subprocesses.
        barrier = asyncio.Event()
        peak = {"children": 0}

        async def hold(i):
            async with pool.lease(timeout=30) as w:
                sid = await w.new_session()
                _ = sid
                peak["children"] = max(peak["children"], _child_count())
                await barrier.wait()

        tasks = [asyncio.create_task(hold(i)) for i in range(100)]
        await asyncio.sleep(3)  # let them all spawn + open sessions
        stats = pool.stats()
        assert stats["workers_total"] == 100, stats
        # Real subprocesses exist (stub wrappers + python children).
        assert peak["children"] >= 100
        barrier.set()
        await asyncio.gather(*tasks)
    finally:
        await pool.stop()
    # After stop, workers are reaped.
    assert pool.stats()["workers_total"] == 0
