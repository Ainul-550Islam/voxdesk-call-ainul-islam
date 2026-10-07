"""Process liveness, readiness, and safe dependency status API."""
from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.core import health as health_check
from app.observability.health import dependency_health
from app.core.logging import log

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness():
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness():
    result = await health_check.readiness()
    status_code = 200 if result["ready"] else 503
    if not result["ready"]:
        log.error("readiness.unavailable", checks=result["body"]["checks"])
    return JSONResponse(status_code=status_code, content=result["body"])


@router.get("/health/dependencies")
async def dependencies():
    result = await dependency_health()
    return JSONResponse(status_code=200 if result["status"] == "ok" else 503, content=result)
