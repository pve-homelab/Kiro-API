"""Admin endpoints used by the host tray agent.

These are bound to the dashboard/localhost surface and let the tray:
  * trigger a kiro-cli login (device flow) inside the container's CLI context
  * toggle the bridge auth key at runtime (no restart needed)
  * read the effective config it should display

Port/address changes are NOT done here — those rewrite .env and restart the
container (see tray agent + docs), because uvicorn cannot rebind its listen
port in-process cleanly.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from .config import config
from .kiro_status import check_login

router = APIRouter(prefix="/admin", tags=["admin"])

# Tracks an in-flight login so the tray can poll its output.
_login_state: dict = {"running": False, "output": "", "returncode": None}


class AuthKeyBody(BaseModel):
    key: str = ""


@router.get("/config")
async def get_config() -> dict:
    return {
        "v1_url": config.v1_base_url,
        "dashboard_url": config.dashboard_url,
        "host": config.advertised_host,
        "port": config.port,
        "dashboard_port": config.dashboard_port,
        "default_model": config.default_model,
        "auth_required": config.auth_required,
        "max_concurrency": config.max_concurrency,
    }


@router.post("/auth-key")
async def set_auth_key(body: AuthKeyBody) -> dict:
    """Set or clear the bridge auth key at runtime. Empty string disables auth."""
    config.auth_key = body.key.strip()
    return {"auth_required": config.auth_required}


@router.post("/login")
async def trigger_login() -> dict:
    """Kick off `kiro-cli login --use-device-flow` in the background.

    Device-flow login prints a URL/code the user must approve. The tray polls
    /admin/login/status to surface that text. Interactive approval still happens
    out of band (the user approves in a browser), but this removes the manual
    'open a terminal and type the command' step.
    """
    if _login_state["running"]:
        return {"running": True, "output": _login_state["output"]}

    _login_state.update(running=True, output="", returncode=None)
    asyncio.create_task(_run_login())
    return {"running": True}


async def _run_login() -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            config.kiro_cli_bin,
            "login",
            "--use-device-flow",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        chunks: list[str] = []
        assert proc.stdout is not None
        async for line in proc.stdout:
            text = line.decode("utf-8", "replace")
            chunks.append(text)
            _login_state["output"] = "".join(chunks)[-4000:]
        await proc.wait()
        _login_state["returncode"] = proc.returncode
    except FileNotFoundError:
        _login_state["output"] = f"kiro-cli not found at '{config.kiro_cli_bin}'"
        _login_state["returncode"] = 127
    finally:
        _login_state["running"] = False
        # refresh cached login status
        await check_login(force=True)


@router.get("/login/status")
async def login_status() -> dict:
    return dict(_login_state)
