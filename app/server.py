"""Entrypoint: run the API and the dashboard on their own ports.

The same FastAPI app serves both the OpenAI /v1 endpoints and the dashboard,
but we bind it on two ports so clients hit the API on 8787 and humans open the
dashboard on 8788. Two uvicorn servers share one event loop in one process,
which keeps the container single-process and the pool/stats state shared.

Clean shutdown is a first-class concern (this process is PID 1 in the
container):

  * We install ONE signal handler for SIGTERM/SIGINT instead of letting each
    uvicorn server install its own competing handlers. That handler asks BOTH
    servers to exit, so `docker stop` / compose down brings everything down
    together and a subsequent `up` starts cleanly.
  * On the way out we kill every in-flight kiro-cli job (via the reaper) so no
    orphaned/ghost process survives the container.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn

from .config import config
from .main import app
from .reaper import reaper

log = logging.getLogger("kiro-api")


def _server(port: int) -> uvicorn.Server:
    cfg = uvicorn.Config(
        app,
        host=config.host,
        port=port,
        log_level="info",
        access_log=False,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        # We manage signals ourselves so the two servers shut down in lockstep.
        timeout_graceful_shutdown=15,
    )
    server = uvicorn.Server(cfg)
    # Disable uvicorn's own signal handlers; the parent coordinates shutdown.
    server.install_signal_handlers = lambda: None
    return server


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    api = _server(config.port)
    dash = _server(config.dashboard_port)

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def _request_stop(signame: str) -> None:
        if not stopping.is_set():
            log.info("received %s — shutting down both servers", signame)
            stopping.set()
        # Tell both uvicorn servers to exit their serve() loops.
        api.should_exit = True
        dash.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
        except (NotImplementedError, RuntimeError):
            # add_signal_handler isn't available on some platforms; fall back.
            signal.signal(sig, lambda s, f: _request_stop(signal.Signals(s).name))

    log.info("Kiro-API V2 starting")
    log.info("  API  → %s", config.v1_base_url)
    log.info("  Dash → %s", config.dashboard_url)
    log.info("  model=%s  max_concurrency=%s  auth=%s",
             config.default_model, config.max_concurrency, config.auth_required)

    serve_task = asyncio.gather(api.serve(), dash.serve())
    try:
        await serve_task
    finally:
        # Belt and braces: whatever caused us to exit (signal, crash, both
        # servers done), make sure no kiro-cli child survives us.
        log.info("draining in-flight kiro-cli jobs")
        killed = await reaper.kill_all_jobs()
        if killed:
            log.info("killed %s in-flight job(s) on shutdown", killed)
        log.info("Kiro-API V2 stopped cleanly")


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
