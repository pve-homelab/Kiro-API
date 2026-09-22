"""Long-run resource hygiene: process reaping, leak detection, and light GC.

Running for weeks under 50+ concurrent agents, the failure modes are:
  * orphan / zombie kiro-cli processes that never get reaped
  * concurrency slots held forever by a wedged job (pool starvation)
  * slow native-memory growth in a long-lived Python process

This module runs a background janitor that:
  1. Reaps defunct (zombie) child processes every cycle.
  2. Kills any tracked job that has exceeded a hard wall-clock ceiling
     (a backstop above the per-request timeout, in case a kill was missed).
  3. Detects child-process count growth beyond a threshold and logs a warning
     so it surfaces on the dashboard as an error (orange dot).
  4. Periodically runs gc.collect() and trims glibc malloc arenas
     (malloc_trim) to return freed heap back to the OS.

Everything here is defensive and cheap; it should never block request paths.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import gc
import logging
import os
import signal
import time
from dataclasses import dataclass

log = logging.getLogger("kiro-api.reaper")


# ---- glibc malloc_trim (return freed heap to the OS) ---------------------
def _load_malloc_trim():
    try:
        libc_name = ctypes.util.find_library("c")
        if not libc_name:
            return None
        libc = ctypes.CDLL(libc_name, use_errno=True)
        if not hasattr(libc, "malloc_trim"):
            return None
        libc.malloc_trim.argtypes = [ctypes.c_size_t]
        libc.malloc_trim.restype = ctypes.c_int
        return libc.malloc_trim
    except Exception:
        return None


_malloc_trim = _load_malloc_trim()


@dataclass
class TrackedJob:
    pid: int
    pgid: int
    started: float
    hard_deadline: float


@dataclass
class ReaperStats:
    zombies_reaped: int = 0
    jobs_force_killed: int = 0
    gc_runs: int = 0
    last_child_count: int = 0
    peak_child_count: int = 0
    last_sweep_ts: float = 0.0
    leak_warning: bool = False
    detail: str = ""

    def snapshot(self) -> dict:
        return {
            "zombies_reaped": self.zombies_reaped,
            "jobs_force_killed": self.jobs_force_killed,
            "gc_runs": self.gc_runs,
            "last_child_count": self.last_child_count,
            "peak_child_count": self.peak_child_count,
            "last_sweep_ts": self.last_sweep_ts,
            "leak_warning": self.leak_warning,
            "detail": self.detail,
        }


class Reaper:
    def __init__(
        self,
        sweep_interval: float = 15.0,
        gc_interval: float = 300.0,
        child_warn_threshold: int = 0,  # set from config (max_concurrency * factor)
    ) -> None:
        self.sweep_interval = sweep_interval
        self.gc_interval = gc_interval
        self.child_warn_threshold = child_warn_threshold
        self.stats = ReaperStats()
        self._jobs: dict[int, TrackedJob] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_gc = 0.0

    # -- job tracking (called by the runner) ------------------------------
    async def track(self, pid: int, hard_timeout: float) -> None:
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            pgid = pid
        async with self._lock:
            now = time.time()
            self._jobs[pid] = TrackedJob(
                pid=pid, pgid=pgid, started=now, hard_deadline=now + hard_timeout
            )

    async def untrack(self, pid: int) -> None:
        async with self._lock:
            self._jobs.pop(pid, None)

    async def kill_all_jobs(self) -> int:
        """Kill every tracked kiro-cli job. Called on shutdown so no child
        process is orphaned when the container stops. Returns count killed."""
        async with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            self._force_kill(job)
        return len(jobs)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="reaper")
            log.info(
                "reaper started (sweep=%ss gc=%ss child_warn=%s malloc_trim=%s)",
                self.sweep_interval, self.gc_interval, self.child_warn_threshold,
                bool(_malloc_trim),
            )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # -- main loop --------------------------------------------------------
    async def _loop(self) -> None:
        self._last_gc = time.time()
        while True:
            try:
                await self._sweep()
                now = time.time()
                if now - self._last_gc >= self.gc_interval:
                    self._collect()
                    self._last_gc = now
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the janitor die
                log.warning("reaper sweep error: %s", exc)
            await asyncio.sleep(self.sweep_interval)

    async def _sweep(self) -> None:
        self._reap_zombies()
        await self._enforce_deadlines()
        self._count_children()
        self.stats.last_sweep_ts = time.time()

    # -- 1. reap zombies --------------------------------------------------
    def _reap_zombies(self) -> None:
        # Non-blocking waitpid loop clears any child we implicitly own that
        # asyncio didn't already reap.
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            except Exception:
                break
            if pid == 0:
                break
            self.stats.zombies_reaped += 1

    # -- 2. hard deadline backstop ---------------------------------------
    async def _enforce_deadlines(self) -> None:
        now = time.time()
        stale: list[TrackedJob] = []
        async with self._lock:
            for job in list(self._jobs.values()):
                if now > job.hard_deadline:
                    stale.append(job)
        for job in stale:
            self._force_kill(job)
            await self.untrack(job.pid)

    def _force_kill(self, job: TrackedJob) -> None:
        killed = False
        for target, _how in ((-job.pgid, "pgid"), (job.pid, "pid")):
            try:
                os.kill(target, signal.SIGKILL)
                killed = True
                break
            except ProcessLookupError:
                break
            except PermissionError:
                continue
        if killed:
            self.stats.jobs_force_killed += 1
            log.warning("reaper force-killed wedged job pid=%s (past hard deadline)", job.pid)

    # -- 3. child-count leak detection -----------------------------------
    def _count_children(self) -> None:
        count = self._current_child_count()
        self.stats.last_child_count = count
        self.stats.peak_child_count = max(self.stats.peak_child_count, count)
        if self.child_warn_threshold and count > self.child_warn_threshold:
            self.stats.leak_warning = True
            self.stats.detail = (
                f"child process count {count} exceeds threshold "
                f"{self.child_warn_threshold}; possible leak"
            )
            log.warning(self.stats.detail)
        else:
            self.stats.leak_warning = False
            self.stats.detail = ""

    @staticmethod
    def _current_child_count() -> int:
        # Count entries under /proc whose parent is us. Cheap and Linux-native.
        me = os.getpid()
        count = 0
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry}/stat", "rb") as fh:
                        fields = fh.read().split()
                    # field 4 (index 3) is ppid; name may contain spaces so use rsplit of ')'
                    ppid = int(fields[3])
                except (OSError, ValueError, IndexError):
                    continue
                if ppid == me:
                    count += 1
        except FileNotFoundError:
            # /proc not available (non-Linux) — skip leak detection gracefully.
            return 0
        return count

    # -- 4. gc + malloc_trim ---------------------------------------------
    def _collect(self) -> None:
        collected = gc.collect()
        self.stats.gc_runs += 1
        trimmed = False
        if _malloc_trim is not None:
            try:
                trimmed = bool(_malloc_trim(0))
            except Exception:
                trimmed = False
        log.info("gc.collect() freed %s objects; malloc_trim=%s", collected, trimmed)


# Singleton — configured in server startup.
reaper = Reaper()
