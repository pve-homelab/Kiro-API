"""Elastic worker pool + scheduler.

Runs up to `max_workers` long-lived `kiro-cli acp` subprocesses. Each worker
handles one active turn at a time, so N workers = N-way true parallelism (the
answer to "streaming AND ~100 agents"). Workers are spawned lazily on demand and
retired after idle timeout, so ~10 agents run cheap and a stress test can grow
toward ~100 by raising `max_workers`.

Backpressure: a bounded queue guards the pool. When it is full, callers get a
native-shaped 503/429 rather than the service collapsing.

A lease is acquired with `async with pool.lease() as worker:` — the worker is
guaranteed alive and exclusively held for that turn.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

from .acp.client import ACPWorker
from .config import Config
from .errors import OVERLOADED, ApiError

log = logging.getLogger("kiro-api.pool")


class WorkerPool:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._workers: list[ACPWorker] = []
        self._idle: asyncio.LifoQueue[ACPWorker] = asyncio.LifoQueue()
        self._lock = asyncio.Lock()
        self._total = 0  # count of live workers (idle + busy)
        self._waiters = 0
        self._seq = 0
        self._closed = False
        self._sweeper: asyncio.Task | None = None
        # Semaphore caps concurrent leases at max_workers.
        self._sema = asyncio.Semaphore(config.max_workers)
        # Queue-depth guard.
        self._queue_slots = config.max_queue

    # -- lifecycle --
    async def start(self) -> None:
        self._closed = False
        # Pre-warm min_workers.
        for _ in range(max(0, self.config.min_workers)):
            try:
                w = await self._spawn_worker()
                await self._idle.put(w)
            except Exception as exc:  # never fail startup on a warm worker
                log.warning("prewarm worker failed: %s", exc)
                break
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="pool-sweeper")
        log.info(
            "worker pool started (min=%s max=%s idle_timeout=%ss)",
            self.config.min_workers, self.config.max_workers, self.config.worker_idle_timeout,
        )

    async def stop(self) -> None:
        self._closed = True
        if self._sweeper:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._sweeper
        async with self._lock:
            workers = list(self._workers)
            self._workers.clear()
            self._total = 0
        await asyncio.gather(*(w.stop() for w in workers), return_exceptions=True)
        log.info("worker pool stopped (%d workers)", len(workers))

    async def _spawn_worker(self) -> ACPWorker:
        self._seq += 1
        wid = f"w{self._seq}"
        worker = ACPWorker(
            worker_id=wid,
            command=self.config.kiro_cli_bin,
            engine=self.config.acp_engine,
            trust_tools=self.config.trust_tools,
            workspace_dir=self.config.workspace_dir,
        )
        await worker.start()
        await worker.initialize()
        async with self._lock:
            self._workers.append(worker)
            self._total += 1
        log.info("worker spawned (total=%d)", self._total, extra={"worker": wid})
        return worker

    async def _retire_worker(self, worker: ACPWorker) -> None:
        async with self._lock:
            if worker in self._workers:
                self._workers.remove(worker)
                self._total -= 1
        with contextlib.suppress(Exception):
            await worker.stop()
        log.info("worker retired (total=%d)", self._total, extra={"worker": worker.worker_id})

    # -- leasing --
    @contextlib.asynccontextmanager
    async def lease(self, timeout: float | None = None) -> AsyncIterator[ACPWorker]:
        if self._closed:
            raise ApiError(OVERLOADED, "service is shutting down")

        acquired = False
        # Fast path: a slot is free right now → take it synchronously, no queue.
        # When the semaphore is not locked, acquire() returns without suspending
        # on a waiter, so this cannot block.
        if not self._sema.locked():
            await self._sema.acquire()
            acquired = True

        if not acquired:
            # Pool is saturated. Enforce backpressure: only `max_queue` requests
            # may wait; beyond that, reject fast so the service stays responsive.
            if self._waiters >= self._queue_slots:
                raise ApiError(OVERLOADED, "server busy, queue full", retry_after=5)
            self._waiters += 1
            try:
                await asyncio.wait_for(self._sema.acquire(), timeout=timeout)
                acquired = True
            except TimeoutError as exc:
                raise ApiError(
                    OVERLOADED, "no worker available (timed out)", retry_after=5
                ) from exc
            finally:
                self._waiters -= 1

        worker = await self._get_live_worker()
        worker.busy = True
        try:
            yield worker
        finally:
            worker.busy = False
            worker.last_used = time.time()
            # Return to idle only if still alive; else drop it.
            if worker.alive() and not self._closed:
                await self._idle.put(worker)
            else:
                await self._retire_worker(worker)
            if acquired:
                self._sema.release()

    async def _get_live_worker(self) -> ACPWorker:
        # Try idle workers first; discard any that died while idle.
        while not self._idle.empty():
            worker = await self._idle.get()
            if worker.alive():
                return worker
            await self._retire_worker(worker)
        # None available → spawn a new one (bounded by the semaphore already held).
        return await self._spawn_worker()

    # -- idle sweep / health --
    async def _sweep_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(min(30, max(5, self.config.worker_idle_timeout // 4)))
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("pool sweep error: %s", exc)

    async def _sweep(self) -> None:
        now = time.time()
        keep: list[ACPWorker] = []
        drained: list[ACPWorker] = []
        # Drain idle queue, decide keep vs retire.
        while not self._idle.empty():
            drained.append(await self._idle.get())
        for w in drained:
            dead = not w.alive()
            idle_expired = (
                self._total > self.config.min_workers
                and w.last_used
                and (now - w.last_used) > self.config.worker_idle_timeout
            )
            if dead or idle_expired:
                await self._retire_worker(w)
            else:
                keep.append(w)
        for w in keep:
            await self._idle.put(w)

    # -- stats --
    def stats(self) -> dict:
        busy = sum(1 for w in self._workers if w.busy)
        return {
            "workers_total": self._total,
            "workers_busy": busy,
            "workers_idle": self._total - busy,
            "max_workers": self.config.max_workers,
            "queue_waiters": self._waiters,
            "max_queue": self._queue_slots,
        }

    def any_available(self) -> bool:
        return self._total < self.config.max_workers or not self._idle.empty()
