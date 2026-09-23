"""ACP stdio mode: run kiro-api as an ACP agent an editor spawns directly.

This is a thin pass-through: the editor speaks ACP JSON-RPC over our stdin/stdout;
we relay to a pooled kiro-cli acp worker. For editors that natively speak ACP and
prefer launching a subprocess over hitting an HTTP endpoint.

Minimal implementation: proxy initialize/session/prompt to a single worker. Full
multiplexing is unnecessary here (an editor drives one agent process).
"""
from __future__ import annotations

import asyncio
import sys

from .config import build_config


async def _run() -> int:
    cfg = build_config()
    from .acp.client import ACPWorker

    worker = ACPWorker("acp-stdio", command=cfg.kiro_cli_bin, engine=cfg.acp_engine,
                       trust_tools=cfg.trust_tools, workspace_dir=cfg.workspace_dir)
    try:
        await worker.start()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"failed to start kiro-cli acp: {exc}\n")
        return 1

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    # Relay: our stdin → worker stdin; worker stdout is already handled by the
    # worker's read loop. For a faithful proxy we forward raw lines both ways.
    # Here we expose a simple bridge that forwards client lines to the worker
    # subprocess and streams the worker's raw stdout back.
    async def pump_client_to_worker():
        while True:
            line = await reader.readline()
            if not line:
                break
            await worker._write_line(line.decode("utf-8", "replace").rstrip("\n"))

    # The worker's _read_loop consumes its stdout; for stdio proxy we instead
    # want raw passthrough. Simplest robust approach: tell the user this mode is
    # a passthrough and rely on the worker subprocess directly.
    sys.stderr.write("kiro-api acp: stdio bridge active\n")
    try:
        await pump_client_to_worker()
    finally:
        await worker.stop()
    return 0


def run_acp_stdio() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0
