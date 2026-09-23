"""Kiro-API V3 command-line interface.

    kiro-api serve            # run the HTTP service (default; used by systemd)
    kiro-api login            # device-flow login (prints URL+code; optional xdg-open)
    kiro-api status           # human-readable auth + pool + endpoints
    kiro-api stats [--json]   # metrics for scripting
    kiro-api config           # show effective config (precedence-aware)
    kiro-api models           # list models from kiro-cli
    kiro-api acp              # run as an ACP stdio agent (for ACP-native editors)
    kiro-api install-service  # write/enable the systemd unit
    kiro-api version          # versions
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .config import build_config
from .logging_setup import setup_logging


def _default_config_file() -> Path:
    return Path.home() / ".config" / "kiro-api" / "config.env"


def _build_cfg(args) -> object:
    overrides = {}
    for attr in ("host", "port", "max_workers", "min_workers", "auth_key", "default_model",
                 "log_format", "log_level"):
        val = getattr(args, attr, None)
        if val is not None:
            overrides[attr] = val
    return build_config(config_file=getattr(args, "config_file", None) or _default_config_file(),
                        **overrides)


def _cmd_serve(args) -> int:
    cfg = _build_cfg(args)
    setup_logging(cfg.log_level, cfg.log_format)
    from .server import serve
    return serve(cfg)


def _cmd_login(args) -> int:
    cfg = _build_cfg(args)
    setup_logging(cfg.log_level, "text")
    from .auth import AuthManager

    async def _run() -> int:
        auth = AuthManager(cfg.kiro_cli_bin)
        await auth.start_device_login()
        print("Starting device-flow login. Follow the URL/code below:\n")
        # Poll login output until it finishes.
        last = ""
        while True:
            st = auth.login_status()
            out = st.get("output", "")
            if out != last:
                sys.stdout.write(out[len(last):])
                sys.stdout.flush()
                last = out
            if not st.get("running") and st.get("returncode") is not None:
                break
            await asyncio.sleep(0.5)
        rc = auth.login_status().get("returncode")
        print(f"\nlogin finished (exit {rc})")
        return 0 if rc == 0 else 1

    return asyncio.run(_run())


def _fetch_snapshot(cfg) -> dict | None:
    try:
        import urllib.request
        url = f"http://{cfg.base_host()}:{cfg.port}/stats"
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _cmd_status(args) -> int:
    cfg = _build_cfg(args)
    snap = _fetch_snapshot(cfg)
    if snap is None:
        print("● RED   service not reachable at "
              f"http://{cfg.base_host()}:{cfg.port} (is it running?)")
        return 1
    auth = snap["auth"]
    pool = snap["pool"]
    if not auth["logged_in"]:
        dot = "● YELLOW not logged in"
    elif pool["workers_busy"] >= pool["max_workers"]:
        dot = "● ORANGE at capacity"
    else:
        dot = "● GREEN  online · healthy"
    print(dot)
    print(f"  auth      : {auth['state']} ({auth['detail']})")
    sec = auth.get("seconds_to_expiry")
    if sec is not None:
        print(f"  expires in: {int(sec)}s")
    print(f"  workers   : {pool['workers_busy']}/{pool['workers_total']} busy "
          f"(max {pool['max_workers']}, queue {pool['queue_waiters']}/{pool['max_queue']})")
    print(f"  uptime    : {snap['uptime_secs']}s")
    print("  endpoints :")
    for name, url in snap["urls"].items():
        print(f"    {name:9} {url}")
    return 0


def _cmd_stats(args) -> int:
    cfg = _build_cfg(args)
    snap = _fetch_snapshot(cfg)
    if snap is None:
        print(json.dumps({"error": "service not reachable"}))
        return 1
    print(json.dumps(snap, indent=2 if not args.json else None))
    return 0


def _cmd_config(args) -> int:
    cfg = _build_cfg(args)
    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def _cmd_models(args) -> int:
    cfg = _build_cfg(args)
    setup_logging("WARNING", "text")
    from .acp.client import ACPWorker

    async def _run() -> int:
        w = ACPWorker("cli", command=cfg.kiro_cli_bin, engine=cfg.acp_engine)
        try:
            await w.start()
            await w.initialize()
            sid = await w.new_session()
            _ = sid
            for m in w.available_models or [{"id": cfg.default_model}]:
                print(m["id"])
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"error: {exc}", file=sys.stderr)
            return 1
        finally:
            await w.stop()

    return asyncio.run(_run())


def _cmd_acp(args) -> int:
    from .acp_stdio import run_acp_stdio
    return run_acp_stdio()


def _cmd_install_service(args) -> int:
    from .install import install_service, uninstall_service
    if args.uninstall:
        return uninstall_service(user=args.user)
    return install_service(user=args.user)


def _cmd_version(args) -> int:
    print(f"kiro-api {__version__}")
    import shutil
    import subprocess
    cfg = _build_cfg(args)
    if shutil.which(cfg.kiro_cli_bin) or Path(cfg.kiro_cli_bin).exists():
        try:
            out = subprocess.run([cfg.kiro_cli_bin, "--version"], capture_output=True,
                                 text=True, timeout=10)
            print(f"kiro-cli {out.stdout.strip() or out.stderr.strip()}")
        except Exception:
            print("kiro-cli version unavailable")
    else:
        print(f"kiro-cli not found at '{cfg.kiro_cli_bin}'")
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config-file", type=Path, default=None, help="path to config.env")
    p.add_argument("--log-level", default=None, help="DEBUG|INFO|WARNING|ERROR")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiro-api",
                                     description="CLI-driven Kiro-API service (OpenAI/Anthropic/ACP)")
    parser.add_argument("-v", "--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="start the HTTP service (default)")
    serve_p.add_argument("-H", "--host", default=None, help="bind address (any IP; default 127.0.0.1)")
    serve_p.add_argument("-p", "--port", type=int, default=None, help="bind port (default 8787)")
    serve_p.add_argument("--workers", dest="max_workers", type=int, default=None,
                         help="max elastic workers (default 8)")
    serve_p.add_argument("--min-workers", dest="min_workers", type=int, default=None)
    serve_p.add_argument("--auth-key", dest="auth_key", default=None, help="bridge API key")
    serve_p.add_argument("--model", dest="default_model", default=None, help="default model id")
    serve_p.add_argument("--log-format", dest="log_format", default=None, help="text|json")
    _add_common(serve_p)
    serve_p.set_defaults(func=_cmd_serve)

    login_p = sub.add_parser("login", help="device-flow login")
    _add_common(login_p)
    login_p.set_defaults(func=_cmd_login)

    status_p = sub.add_parser("status", help="human-readable service status")
    status_p.add_argument("-H", "--host", default=None)
    status_p.add_argument("-p", "--port", type=int, default=None)
    _add_common(status_p)
    status_p.set_defaults(func=_cmd_status)

    stats_p = sub.add_parser("stats", help="machine-readable metrics")
    stats_p.add_argument("--json", action="store_true", help="compact JSON")
    stats_p.add_argument("-H", "--host", default=None)
    stats_p.add_argument("-p", "--port", type=int, default=None)
    _add_common(stats_p)
    stats_p.set_defaults(func=_cmd_stats)

    config_p = sub.add_parser("config", help="show effective config")
    _add_common(config_p)
    config_p.set_defaults(func=_cmd_config)

    models_p = sub.add_parser("models", help="list models from kiro-cli")
    _add_common(models_p)
    models_p.set_defaults(func=_cmd_models)

    acp_p = sub.add_parser("acp", help="run as an ACP stdio agent")
    _add_common(acp_p)
    acp_p.set_defaults(func=_cmd_acp)

    inst_p = sub.add_parser("install-service", help="install/enable the systemd unit")
    inst_p.add_argument("--user", action="store_true", help="install as a --user service")
    inst_p.add_argument("--uninstall", action="store_true", help="remove the service")
    inst_p.set_defaults(func=_cmd_install_service)

    ver_p = sub.add_parser("version", help="print kiro-api and kiro-cli versions")
    _add_common(ver_p)
    ver_p.set_defaults(func=_cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        print(f"kiro-api {__version__}")
        return 0
    if args.command is None:
        # Default to serve.
        args = parser.parse_args(["serve", *(argv or [])])
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
