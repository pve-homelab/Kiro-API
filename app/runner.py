"""kiro-cli subprocess runner + bounded concurrency pool.

Each API request spawns a short-lived `kiro-cli chat --no-interactive` job.
A semaphore bounds how many run at once (tuned for the 50+ agent workload);
requests beyond that wait in an async queue (or get a 429 if configured).

Large prompts are written to the process stdin to avoid argv length limits.
On timeout or client disconnect the whole process tree is killed so a stuck
CLI job can never permanently hold a concurrency slot.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from dataclasses import dataclass

from .config import config
from .reaper import reaper


class BusyError(Exception):
    """Raised when reject_when_busy is on and the queue is full."""


class KiroCliError(Exception):
    """Raised when kiro-cli exits non-zero or cannot be spawned."""


@dataclass
class PoolStats:
    active: int = 0
    queued: int = 0
    max_concurrency: int = 0
    total_requests: int = 0
    total_errors: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    last_model: str = ""
    last_request_ts: float = 0.0

    def snapshot(self) -> dict:
        return {
            "active": self.active,
            "queued": self.queued,
            "max_concurrency": self.max_concurrency,
            "available": max(0, self.max_concurrency - self.active),
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "last_model": self.last_model,
            "last_request_ts": self.last_request_ts,
        }


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — matches V1 behavior until
    kiro-cli exposes real billing usage."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class KiroRunner:
    """Owns the concurrency pool and spawns kiro-cli jobs."""

    def __init__(self) -> None:
        self._sema = asyncio.Semaphore(config.max_concurrency)
        self._queued = 0
        self.stats = PoolStats(max_concurrency=config.max_concurrency)

    # -- slot management --------------------------------------------------
    @contextlib.asynccontextmanager
    async def _slot(self):
        # Backpressure: reject if the queue is full and configured to do so.
        if config.reject_when_busy and config.max_queue > 0 and self._queued >= config.max_queue:
            raise BusyError("queue full")

        self._queued += 1
        self.stats.queued = self._queued
        try:
            await self._sema.acquire()
        finally:
            self._queued -= 1
            self.stats.queued = self._queued

        self.stats.active += 1
        try:
            yield
        finally:
            self.stats.active -= 1
            self._sema.release()

    # -- command construction --------------------------------------------
    def _build_cmd(self, model: str) -> list[str]:
        cmd = [config.kiro_cli_bin, "chat", "--no-interactive"]
        if model and model != "auto":
            cmd += ["--model", model]
        return cmd

    def _timeout_for(self, long_running: bool) -> int:
        return config.timeout_long if long_running else config.timeout_short

    # -- execution --------------------------------------------------------
    async def run(self, prompt: str, model: str, long_running: bool = False) -> tuple[str, int, int]:
        """Run one kiro-cli job to completion. Returns (text, prompt_tokens, completion_tokens)."""
        model = model or config.default_model
        async with self._slot():
            self.stats.total_requests += 1
            self.stats.last_model = model
            self.stats.last_request_ts = time.time()
            prompt_tokens = estimate_tokens(prompt)

            try:
                text = await self._spawn(prompt, model, long_running)
            except Exception:
                self.stats.total_errors += 1
                raise

            completion_tokens = estimate_tokens(text)
            self.stats.total_prompt_tokens += prompt_tokens
            self.stats.total_completion_tokens += completion_tokens
            return text, prompt_tokens, completion_tokens

    async def _spawn(self, prompt: str, model: str, long_running: bool) -> str:
        cmd = self._build_cmd(model)
        timeout = self._timeout_for(long_running)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.kiro_cli_workspace if os.path.isdir(config.kiro_cli_workspace) else None,
                start_new_session=True,  # own process group so we can kill the tree
            )
        except FileNotFoundError as exc:
            raise KiroCliError(f"kiro-cli not found at '{config.kiro_cli_bin}'") from exc

        # Register with the reaper: a hard wall-clock ceiling above the request
        # timeout acts as a backstop so a wedged job can never hold its slot
        # forever even if the kill below is somehow missed.
        await reaper.track(proc.pid, hard_timeout=timeout + 60)

        try:
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode("utf-8")), timeout=timeout
                )
            except TimeoutError as exc:
                self._kill_tree(proc)
                raise KiroCliError(f"kiro-cli timed out after {timeout}s") from exc
            except asyncio.CancelledError:
                # Client disconnected — kill the job so the slot frees immediately.
                self._kill_tree(proc)
                raise

            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", "replace").strip()
                raise KiroCliError(err or f"kiro-cli exited with code {proc.returncode}")

            return (stdout or b"").decode("utf-8", "replace").strip()
        finally:
            # Always reap the process and release its reaper tracking, no matter
            # how we exit, so no zombie or orphaned process-group survives.
            if proc.returncode is None:
                self._kill_tree(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            await reaper.untrack(proc.pid)

    @staticmethod
    def _kill_tree(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


# Singleton runner shared across requests.
runner = KiroRunner()
