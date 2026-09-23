"""Admin endpoints (localhost surface): device-flow login trigger + status."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/login")
async def trigger_login(request: Request) -> dict:
    auth = request.app.state.auth
    return await auth.start_device_login()


@router.get("/login/status")
async def login_status(request: Request) -> dict:
    return request.app.state.auth.login_status()


@router.get("/config")
async def get_config(request: Request) -> dict:
    cfg = request.app.state.config
    return {
        "urls": cfg.urls(),
        "host": cfg.host,
        "port": cfg.port,
        "default_model": cfg.default_model,
        "auth_required": cfg.auth_required,
        "max_workers": cfg.max_workers,
        "min_workers": cfg.min_workers,
    }
