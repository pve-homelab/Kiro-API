"""ACPWorker — manages a single `kiro-cli acp` subprocess over JSON-RPC/stdio.

One worker = one long-lived subprocess. It runs `initialize` once, then a fresh
`session/new` per turn, `session/prompt` to run it, streaming normalized events,
and `session/cancel` on abandonment. A worker handles ONE active turn at a time
(the pool provides parallelism by running many workers), so a heavy turn can
only block itself.

Liveness: `alive()` reflects the subprocess state; the pool health-checks and
replaces dead/wedged workers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from collections.abc import AsyncIterator
from typing import Any

from . import events

log = logging.getLogger("kiro-api.acp")

_STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "max_turn_requests": "length",
    "tool_use": "tool_calls",
    "refusal": "stop",
    "cancelled": "stop",
}


class ACPError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class ACPWorker:
    def __init__(
        self,
        worker_id: str,
        command: str = "kiro-cli",
        engine: str = "v2",
        trust_tools: bool = True,
        workspace_dir: str = "",
        stdio_limit: int = 16 * 1024 * 1024,
    ) -> None:
        self.worker_id = worker_id
        self._command = command
        self._engine = engine or "v2"
        self._trust_tools = trust_tools
        self._workspace_dir = workspace_dir
        self._stdio_limit = stdio_limit

        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._prompt_sessions: dict[str, str] = {}
        self._write_lock = asyncio.Lock()
        self._initialized = False
        self.available_models: list[dict] = []
        self._current_model: str | None = None
        self.busy = False
        self.last_used = 0.0
        self._dead = False  # set on kill/EOF so liveness is instant, not race-y
        self.protocol_version: int | None = None
        self.agent_name = ""
        self.agent_version = ""

    # -- lifecycle --
    def alive(self) -> bool:
        return (
            not self._dead
            and self._proc is not None
            and self._proc.returncode is None
        )

    def _argv(self) -> list[str]:
        argv = [self._command, "acp", "--agent-engine", self._engine]
        return argv

    async def start(self) -> None:
        argv = self._argv()
        log.info("spawning ACP subprocess: %s", " ".join(argv), extra={"worker": self.worker_id})
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self._stdio_limit,
            start_new_session=True,  # own process group → killable tree
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_reader = asyncio.create_task(self._stderr_loop())

    # ACP protocol versions this client understands. The kiro-cli acp surface is
    # not part of AWS's public docs, so we validate the negotiated version at
    # startup and fail loudly (rather than subtly) if a kiro-cli update changes
    # the contract beyond what we support.
    SUPPORTED_PROTOCOL_VERSIONS = frozenset({1})

    async def initialize(self) -> None:
        params = {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
        }
        result = await self._call("initialize", params, timeout=30)
        proto = 1
        if isinstance(result, dict):
            raw = result.get("protocolVersion", 1)
            try:
                proto = int(raw)
            except (TypeError, ValueError):
                proto = raw
            agent = result.get("agentInfo") or result.get("agent") or {}
            if isinstance(agent, dict):
                self.agent_name = str(agent.get("name") or "")
                self.agent_version = str(agent.get("version") or "")
        self.protocol_version = proto
        if proto not in self.SUPPORTED_PROTOCOL_VERSIONS:
            raise ACPError(
                -32001,
                f"incompatible ACP protocol version {proto!r} from kiro-cli "
                f"(supported: {sorted(self.SUPPORTED_PROTOCOL_VERSIONS)}). "
                "The kiro-cli acp contract may have changed; upgrade kiro-api or "
                "pin a compatible kiro-cli.",
            )
        self._initialized = True
        log.info(
            "ACP initialized: agent=%s v%s protocol=%s",
            getattr(self, "agent_name", "?") or "?",
            getattr(self, "agent_version", "?") or "?",
            proto,
            extra={"worker": self.worker_id},
        )

    async def stop(self) -> None:
        for task in (self._reader, self._stderr_reader):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self._proc and self._proc.returncode is None:
            try:
                if self._proc.stdin and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError, OSError):
                self._kill_tree()

    def _kill_tree(self) -> None:
        self._dead = True  # mark dead immediately; reaping is async
        if not self._proc:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self._proc.kill()
            except (ProcessLookupError, OSError):
                pass

    # -- session --
    async def new_session(self, cwd: str | None = None, mcp_servers: list[dict] | None = None) -> str:
        workdir = cwd or self._workspace_dir or os.getcwd()
        params = {"cwd": workdir, "mcpServers": mcp_servers or []}
        result = await self._call("session/new", params, timeout=30)
        if not isinstance(result, dict) or "sessionId" not in result:
            raise ACPError(-32603, f"session/new returned no sessionId: {result!r}")
        self._capture_models(result)
        return str(result["sessionId"])

    def _capture_models(self, result: dict) -> None:
        info = result.get("models")
        if not isinstance(info, dict):
            return
        current = info.get("currentModelId")
        if isinstance(current, str) and current:
            self._current_model = current
        avail = info.get("availableModels")
        if isinstance(avail, list):
            normalized = []
            for e in avail:
                if isinstance(e, dict) and e.get("modelId"):
                    normalized.append({
                        "id": str(e["modelId"]),
                        "name": str(e.get("name") or e["modelId"]),
                        "description": str(e.get("description") or ""),
                    })
            if normalized:
                self.available_models = normalized

    async def set_model(self, session_id: str, model_id: str) -> None:
        try:
            await self._call("session/set_model", {"sessionId": session_id, "modelId": model_id})
        except ACPError as exc:
            log.warning("set_model %s failed: %s", model_id, exc, extra={"worker": self.worker_id})

    # -- prompting --
    async def prompt_stream(
        self,
        session_id: str,
        prompt_blocks: list[dict],
        meta: dict | None = None,
        turn_timeout: float | None = None,
        idle_timeout: float = 300.0,
    ) -> AsyncIterator[dict]:
        """Stream normalized events for one turn.

        Bounded two ways so a wedged/silent worker can never hang a request
        forever (the core anti-"stuck" guarantee):
          * ``idle_timeout`` — max seconds to wait for the NEXT event; reset on
            every event. Catches a subprocess that goes silent mid-turn.
          * ``turn_timeout`` — optional hard ceiling on the whole turn.
        On either timeout we emit a terminal error event and mark the worker
        dead so the pool replaces it.
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[session_id] = queue
        req_id = str(uuid.uuid4())
        self._prompt_sessions[req_id] = session_id
        params: dict[str, Any] = {"sessionId": session_id, "prompt": prompt_blocks}
        if meta:
            params["_meta"] = meta
        prompt_sent = False
        completed = False
        loop = asyncio.get_running_loop()
        hard_deadline = (loop.time() + turn_timeout) if turn_timeout else None
        try:
            await self._send({"jsonrpc": "2.0", "id": req_id, "method": "session/prompt", "params": params})
            prompt_sent = True
            while True:
                # Compute the wait budget: min(idle, remaining hard budget).
                wait = idle_timeout
                if hard_deadline is not None:
                    remaining = hard_deadline - loop.time()
                    if remaining <= 0:
                        self._dead = True
                        yield events.error(
                            f"turn exceeded {turn_timeout}s hard timeout", "timeout"
                        )
                        completed = True
                        break
                    wait = min(wait, remaining)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait)
                except TimeoutError:
                    # No event for `wait` seconds → the worker is silent/wedged.
                    self._dead = True
                    yield events.error(
                        f"worker produced no output for {int(wait)}s", "timeout"
                    )
                    completed = True
                    break
                etype = event.get("type")
                yield event
                if etype in ("done", "error"):
                    completed = True
                    break
        finally:
            if prompt_sent and not completed:
                # Client abandoned the turn → cancel so the worker frees.
                await self._cancel(session_id)
            self._queues.pop(session_id, None)
            self._prompt_sessions.pop(req_id, None)

    async def _cancel(self, session_id: str) -> None:
        try:
            await self._send_notification("session/cancel", {"sessionId": session_id})
        except (OSError, RuntimeError):
            pass

    # -- JSON-RPC plumbing --
    async def _call(self, method: str, params: dict, timeout: float = 120.0) -> Any:
        req_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise ACPError(-32000, f"ACP {method} timed out after {timeout}s") from exc
        if resp.get("error"):
            err = resp["error"]
            raise ACPError(err.get("code", -32000), err.get("message", "error"), err.get("data"))
        return resp.get("result")

    async def _send(self, obj: dict) -> None:
        await self._write_line(json.dumps(obj))

    async def _send_notification(self, method: str, params: dict) -> None:
        await self._write_line(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))

    async def _write_line(self, line: str) -> None:
        async with self._write_lock:
            if self._proc and self._proc.stdin and not self._proc.stdin.is_closing():
                self._proc.stdin.write((line + "\n").encode())
                await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            try:
                raw = await self._proc.stdout.readline()
                if not raw:
                    self._fail_all("kiro-cli subprocess exited")
                    break
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._dispatch(line)
            except asyncio.CancelledError:
                break
            except (json.JSONDecodeError, ValueError) as exc:
                log.error("ACP read parse error: %s", exc, extra={"worker": self.worker_id})
            except Exception as exc:
                log.warning("ACP read loop error: %s", exc, extra={"worker": self.worker_id})

    async def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            try:
                raw = await self._proc.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    log.debug("kiro-cli stderr: %s", line, extra={"worker": self.worker_id})
            except asyncio.CancelledError:
                break
            except Exception:
                break

    def _fail_all(self, message: str) -> None:
        self._dead = True  # stdout EOF → subprocess is gone
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(ACPError(-32000, message))
        self._pending.clear()
        for q in list(self._queues.values()):
            q.put_nowait(events.error(message, "upstream"))

    def _dispatch(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        method = msg.get("method")
        has_id = msg.get("id") is not None
        if method and has_id:
            asyncio.create_task(self._handle_agent_request(msg))
            return
        if method:
            self._handle_notification(msg)
            return
        msg_id = str(msg.get("id", ""))
        fut = self._pending.pop(msg_id, None)
        if fut and not fut.done():
            fut.set_result(msg)
            return
        self._finish_prompt(msg_id, msg)

    def _finish_prompt(self, req_id: str, msg: dict) -> None:
        session_id = self._prompt_sessions.get(req_id)
        if not session_id:
            return
        q = self._queues.get(session_id)
        if not q:
            return
        if msg.get("error"):
            err = msg["error"]
            q.put_nowait(events.error(err.get("message", "prompt error"), "upstream"))
            return
        result = msg.get("result") or {}
        stop = result.get("stopReason", "end_turn") if isinstance(result, dict) else "end_turn"
        usage = result.get("usage") if isinstance(result, dict) else {}
        q.put_nowait(events.done(_STOP_REASON_MAP.get(stop, "stop"), usage or {}))

    def _handle_notification(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        params = msg.get("params", {})
        session_id = params.get("sessionId", "")
        update = params.get("update", {})
        kind = update.get("sessionUpdate")
        q = self._queues.get(session_id)
        if not q:
            return
        if kind == "agent_message_chunk":
            t = _content_text(update.get("content"))
            if t:
                q.put_nowait(events.text(t))
        elif kind == "agent_thought_chunk":
            t = _content_text(update.get("content"))
            if t:
                q.put_nowait(events.thinking(t))
        elif kind == "tool_call":
            if _is_plan(update):
                entries, desc = _plan_entries(update)
                if entries:
                    q.put_nowait(events.plan(entries, desc))
            else:
                q.put_nowait(events.tool_call(
                    update.get("toolCallId", str(uuid.uuid4())),
                    update.get("title") or update.get("kind") or "tool",
                    update.get("kind") or "",
                    update.get("rawInput") or {},
                ))
        elif kind == "tool_call_update":
            if not _is_plan(update):
                q.put_nowait(events.tool_update(
                    update.get("toolCallId", str(uuid.uuid4())),
                    update.get("title") or update.get("kind") or "tool",
                    update.get("kind") or "",
                    _tool_output(update.get("rawOutput")),
                ))

    async def _handle_agent_request(self, msg: dict) -> None:
        method = msg.get("method", "")
        req_id = msg.get("id")
        params = msg.get("params", {})
        if method == "session/request_permission":
            option_id = _select_permission(params.get("options", []), self._trust_tools)
            await self._respond(req_id, {"outcome": {"outcome": "selected", "optionId": option_id}})
            return
        await self._respond_error(req_id, -32601, f"{method} not supported")

    async def _respond(self, req_id: Any, result: Any) -> None:
        await self._write_line(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}))

    async def _respond_error(self, req_id: Any, code: int, message: str) -> None:
        await self._write_line(json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        ))


# -- helpers --
def _content_text(content: Any) -> str:
    if isinstance(content, dict):
        return content.get("text", "")
    if isinstance(content, str):
        return content
    return ""


def _tool_output(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    items = raw.get("items")
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items:
        if isinstance(item, dict) and item.get("Text") is not None:
            parts.append(str(item["Text"]))
    return "\n".join(parts)[:8000]


def _is_plan(update: dict) -> bool:
    raw = update.get("rawInput")
    if isinstance(raw, dict) and any(
        k in raw for k in ("tasks", "task_list_description", "completed_task_ids")
    ):
        return True
    return "task list" in str(update.get("title") or "").lower()


def _plan_entries(update: dict) -> tuple[list[dict], str]:
    raw = update.get("rawInput")
    entries, desc = [], ""
    if isinstance(raw, dict):
        desc = str(raw.get("task_list_description") or "")
        tasks = raw.get("tasks")
        if isinstance(tasks, list):
            for t in tasks:
                if isinstance(t, dict):
                    content = t.get("task_description") or t.get("content") or ""
                    if content:
                        entries.append({
                            "content": str(content),
                            "status": "completed" if t.get("completed") else "pending",
                        })
    return entries, desc


def _select_permission(options: list[dict], approve: bool) -> str:
    """Pick an allow/reject option id from the request's options."""
    want = "allow" if approve else "reject"
    for opt in options or []:
        descriptor = f"{opt.get('kind', '')} {opt.get('optionId', '')}".lower()
        if want in descriptor:
            return opt.get("optionId", want)
    # Fallback conventional ids.
    for opt in options or []:
        oid = str(opt.get("optionId", "")).lower()
        if approve and oid.startswith("allow"):
            return opt["optionId"]
        if not approve and oid.startswith(("reject", "deny")):
            return opt["optionId"]
    return f"{want}_once"
