#!/usr/bin/env python3
"""Fake `kiro-cli` for tests/dev — no real auth, no network.

Supports the subcommands the service uses:
  * `whoami`                    → prints "stub-user" and exits 0 (logged in),
                                  or exits 1 if KIRO_STUB_LOGGED_OUT=1.
  * `login --use-device-flow`   → prints a fake verification URL + code, exits 0.
  * `acp [--agent-engine v2]`   → speaks a minimal ACP JSON-RPC dialect over
                                  stdio: initialize, session/new, session/prompt
                                  (emits agent_message_chunk + a done result),
                                  session/cancel. Enough to exercise the worker,
                                  pool, streaming, and shims end-to-end.

Behavior knobs via env:
  KIRO_STUB_DELAY   seconds to sleep mid-turn (simulate a long turn)  [0]
  KIRO_STUB_THINK   emit a thinking chunk before the answer            [1]
  KIRO_STUB_ERROR   make session/prompt return a JSON-RPC error        [unset]
"""
from __future__ import annotations

import json
import os
import sys
import time


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _cmd_whoami() -> int:
    if os.environ.get("KIRO_STUB_LOGGED_OUT") == "1":
        sys.stderr.write("not logged in\n")
        return 1
    print("stub-user (Builder ID)")
    return 0


def _cmd_login() -> int:
    print("To sign in, visit https://device.example.com/activate and enter code STUB-1234")
    print("login successful")
    return 0


def _handle_acp_line(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        proto = int(os.environ.get("KIRO_STUB_PROTOCOL", "1"))
        _emit({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": proto,
            "agentInfo": {"name": "kiro-cli-stub", "version": "0.0.1"},
            "agentCapabilities": {},
        }})
    elif method == "session/new":
        _emit({"jsonrpc": "2.0", "id": mid, "result": {
            "sessionId": f"stub-session-{mid}",
            "models": {
                "currentModelId": "stub-model",
                "availableModels": [
                    {"modelId": "stub-model", "name": "Stub Model", "description": "test"},
                    {"modelId": "auto", "name": "auto", "description": ""},
                ],
            },
            "modes": {"currentModeId": "kiro_default", "availableModes": []},
        }})
    elif method == "session/set_model":
        _emit({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "_kiro.dev/commands/execute":
        # /effort and other slash-command extensions → accept silently.
        _emit({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "session/prompt":
        sid = msg.get("params", {}).get("sessionId", "")
        if os.environ.get("KIRO_STUB_HANG"):
            # Accept the prompt but never emit any update or terminal result,
            # simulating a wedged/silent worker (tests the idle timeout).
            return
        if os.environ.get("KIRO_STUB_ERROR"):
            _emit({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32000, "message": os.environ["KIRO_STUB_ERROR"]}})
            return
        if os.environ.get("KIRO_STUB_THINK", "1") == "1":
            _emit({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": sid,
                "update": {"sessionUpdate": "agent_thought_chunk",
                           "content": {"type": "text", "text": "thinking..."}}}})
        delay = float(os.environ.get("KIRO_STUB_DELAY", "0") or 0)
        if delay:
            time.sleep(delay)
        # Echo the prompt text back as the answer.
        blocks = msg.get("params", {}).get("prompt", [])
        prompt_text = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        answer = f"stub reply: {prompt_text[-200:]}" if prompt_text else "stub reply"
        for piece in (answer[:len(answer) // 2], answer[len(answer) // 2:]):
            if piece:
                _emit({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": sid,
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"type": "text", "text": piece}}}})
        _emit({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
    elif method == "session/cancel":
        pass  # notification, no response
    elif mid is not None:
        _emit({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})


def _cmd_acp() -> int:
    for line in sys.stdin:
        line = line.strip()
        if line:
            _handle_acp_line(line)
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("usage: kiro-cli-stub <whoami|login|acp|--version>\n")
        return 2
    cmd = argv[0]
    if cmd == "whoami":
        return _cmd_whoami()
    if cmd == "login":
        return _cmd_login()
    if cmd == "acp":
        return _cmd_acp()
    if cmd == "--version":
        print("kiro-cli-stub 0.0.1")
        return 0
    sys.stderr.write(f"unknown command: {cmd}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
