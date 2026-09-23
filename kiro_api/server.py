"""FastAPI app assembly + the `serve()` entrypoint used by the CLI/systemd.

serve() responsibilities (all reliability-critical):
  * validate the bind BEFORE starting anything (bad host/port → clean exit).
  * start auth watchdog + worker pool via the app lifespan.
  * signal systemd readiness (sd_notify READY=1) and pet the watchdog.
  * install ONE signal handler that drains in-flight work and stops cleanly.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, sdnotify
from .auth import AuthManager
from .config import BindError, Config
from .pool import WorkerPool
from .routes import acp, admin, anthropic, control, openai
from .service import ShimService

log = logging.getLogger("kiro-api")


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="Kiro-API V3", version=__version__)
    # Security middleware first (outermost): rate limit, then body-size limit.
    from .security import BodyLimitMiddleware, RateLimitMiddleware
    if config.rate_limit > 0:
        app.add_middleware(RateLimitMiddleware, limit=config.rate_limit,
                           window=config.rate_limit_window)
    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_body_bytes)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    auth = AuthManager(
        kiro_cli_bin=config.kiro_cli_bin,
        refresh_margin=config.auth_refresh_margin,
        poll_interval=config.auth_poll_interval,
    )
    pool = WorkerPool(config)
    service = ShimService(config, pool, auth)

    app.state.config = config
    app.state.auth = auth
    app.state.pool = pool
    app.state.service = service

    app.include_router(control.router)
    app.include_router(admin.router)
    app.include_router(openai.router)
    app.include_router(anthropic.router)
    app.include_router(acp.router)
    return app


async def _watchdog_loop() -> None:
    """Pet the systemd watchdog at half the configured interval."""
    usec = sdnotify.watchdog_usec()
    if not usec:
        return
    interval = max(1.0, (usec / 1_000_000) / 2)
    while True:
        sdnotify.watchdog()
        await asyncio.sleep(interval)


async def _serve_async(config: Config) -> None:
    auth_started = False
    app = create_app(config)

    # Start background actors.
    app.state.auth.start()
    auth_started = True
    await app.state.auth.refresh_state(force=True)
    await app.state.pool.start()

    uv_config = uvicorn.Config(
        app, host=config.host, port=config.port,
        log_level=config.log_level.lower(), access_log=False,
        timeout_graceful_shutdown=20,
    )
    server = uvicorn.Server(uv_config)
    server.install_signal_handlers = lambda: None  # we coordinate shutdown

    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()

    def _request_stop(signame: str) -> None:
        if not stopping.is_set():
            log.info("received %s — draining and shutting down", signame)
            stopping.set()
            sdnotify.stopping()
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, f: _request_stop(signal.Signals(s).name))

    urls = config.urls()
    log.info("Kiro-API V3 starting")
    for name, url in urls.items():
        log.info("  %-9s → %s", name, url)
    log.info("  workers=%s..%s  auth=%s  model=%s",
             config.min_workers, config.max_workers, config.auth_required, config.default_model)

    watchdog = asyncio.create_task(_watchdog_loop())

    async def _on_ready():
        # uvicorn sets started once the socket is listening.
        while not server.started:
            await asyncio.sleep(0.05)
        sdnotify.ready()
        sdnotify.status(f"serving on {config.host}:{config.port}")
        log.info("ready — systemd notified")

    ready_task = asyncio.create_task(_on_ready())

    try:
        await server.serve()
    finally:
        watchdog.cancel()
        ready_task.cancel()
        for t in (watchdog, ready_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        log.info("draining worker pool")
        await app.state.pool.stop()
        if auth_started:
            await app.state.auth.stop()
        log.info("Kiro-API V3 stopped cleanly")


def serve(config: Config) -> int:
    """Blocking serve entrypoint. Returns a process exit code."""
    # Validate bind first — fail loud and clean, never a half-started zombie.
    try:
        config.validate_bind()
    except BindError as exc:
        log.error("cannot start: %s", exc)
        return 2
    try:
        asyncio.run(_serve_async(config))
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        log.error("fatal: %s", exc, exc_info=True)
        return 1
    return 0
