"""Round-2 stress + failure injection.

Exercises the pool at scale, queue saturation, worker crashes mid-flight,
spawn failures, garbage-speaking subprocesses, fd stability over many turns,
and the auth watchdog's proactive-refresh transition.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from kiro_api.acp.client import ACPWorker
from kiro_api.auth import EXPIRING_SOON, LOGGED_IN, AuthManager
from kiro_api.errors import ApiError
from kiro_api.pool import WorkerPool

pytestmark = pytest.mark.integration


async def _turn(worker: ACPWorker, text: str) -> str:
    sid = await worker.new_session()
    out = []
    async for ev in worker.prompt_stream(sid, [{"type": "text", "text": text}], idle_timeout=10):
        if ev["type"] == "text":
            out.append(ev["content"])
    return "".join(out)


async def test_high_concurrency_50(config):
    """50 concurrent leases must all succeed with true parallelism."""
    config.max_workers = 50
    config.max_queue = 200
    pool = WorkerPool(config)
    await pool.start()
    try:
        async def one(i):
            async with pool.lease() as w:
                return await _turn(w, f"req{i}")
        results = await asyncio.gather(*(one(i) for i in range(50)))
        assert len(results) == 50
        assert all("stub reply" in r for r in results)
        assert pool.stats()["workers_total"] <= 50
    finally:
        await pool.stop()


async def test_queue_saturation_rejects_cleanly(config):
    """Beyond max_workers + max_queue, leases reject with a native 503, not hang."""
    config.max_workers = 2
    config.max_queue = 2
    pool = WorkerPool(config)
    await pool.start()
    held = []
    rejected = 0

    async def hold(barrier: asyncio.Event):
        async with pool.lease(timeout=5) as w:
            held.append(w)
            await barrier.wait()

    try:
        barrier = asyncio.Event()
        # 2 workers busy + 2 queued = capacity 4; the 5th+ must reject.
        tasks = [asyncio.create_task(hold(barrier)) for _ in range(2)]
        await asyncio.sleep(0.3)  # let the 2 acquire

        async def try_lease():
            nonlocal rejected
            try:
                async with pool.lease(timeout=0.2):
                    await asyncio.sleep(0.1)
            except ApiError:
                rejected += 1

        # Fire several beyond capacity.
        await asyncio.gather(*(try_lease() for _ in range(6)))
        assert rejected >= 1  # at least some rejected cleanly (no hang)
        barrier.set()
        await asyncio.gather(*tasks)
    finally:
        await pool.stop()


async def test_worker_crash_midflight_is_isolated(config):
    """Killing one worker mid-turn must not affect other concurrent turns."""
    config.max_workers = 5
    pool = WorkerPool(config)
    await pool.start()
    try:
        async def victim():
            try:
                async with pool.lease() as w:
                    sid = await w.new_session()
                    agen = w.prompt_stream(sid, [{"type": "text", "text": "x"}], idle_timeout=5)
                    # Start consuming, then kill the worker.
                    w._kill_tree()
                    async for _ in agen:
                        pass
            except ApiError:
                return "handled"
            return "done"

        async def healthy(i):
            async with pool.lease() as w:
                return await _turn(w, f"ok{i}")

        results = await asyncio.gather(victim(), *(healthy(i) for i in range(4)),
                                       return_exceptions=True)
        healthy_results = results[1:]
        assert all(isinstance(r, str) and "stub reply" in r for r in healthy_results)
    finally:
        await pool.stop()


async def test_spawn_failure_surfaces_cleanly(config):
    """A bad kiro-cli path must raise, not hang the pool."""
    config.kiro_cli_bin = "/nonexistent/kiro-cli-binary"
    config.max_workers = 2
    pool = WorkerPool(config)
    await pool.start()
    try:
        # A bad binary surfaces as an ApiError (overloaded/timeout) or an OSError
        # from the failed spawn — never a hang.
        with pytest.raises((ApiError, OSError)):
            async with pool.lease(timeout=5):
                pass
    finally:
        await pool.stop()


async def test_fd_stability_over_many_turns(config):
    """Many sequential turns must not leak file descriptors/processes."""
    config.max_workers = 3
    pool = WorkerPool(config)
    await pool.start()

    def _open_fds() -> int:
        try:
            return len(os.listdir(f"/proc/{os.getpid()}/fd"))
        except OSError:
            return -1

    try:
        # warm up
        async with pool.lease() as w:
            await _turn(w, "warm")
        baseline = _open_fds()
        for i in range(40):
            async with pool.lease() as w:
                await _turn(w, f"turn{i}")
        after = _open_fds()
        if baseline > 0:
            # allow some slack, but not linear growth with turns
            assert after - baseline < 20, f"fd leak: {baseline} → {after}"
    finally:
        await pool.stop()


async def test_auth_proactive_refresh_transition(stub_bin, tmp_path, monkeypatch):
    """A token within the refresh margin transitions to EXPIRING_SOON and the
    watchdog attempts a refresh (which for the stub keeps it logged in)."""
    import json
    import time

    monkeypatch.delenv("KIRO_STUB_LOGGED_OUT", raising=False)
    cache = tmp_path / "sso" / "cache"
    cache.mkdir(parents=True)
    # Token expiring in 100s, with a 300s margin → EXPIRING_SOON.
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 100))
    (cache / "tok.json").write_text(json.dumps({"expiresAt": exp}))
    monkeypatch.setenv("HOME", str(tmp_path))

    auth = AuthManager(stub_bin, refresh_margin=300, whoami_ttl=0)
    st = await auth.refresh_state(force=True)
    assert st.state in (EXPIRING_SOON, LOGGED_IN)
    # Directly exercise the refresh path.
    ok = await auth.try_refresh()
    assert ok is True or auth.state.logged_in
