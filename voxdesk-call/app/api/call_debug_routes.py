"""Per-call debug capture API (PART 13, P13-11).

Two endpoints and a delivery route:

* `POST /api/calls/{call_id}/pcap` asks the gateway for a capture. It costs the
  gateway real CPU, so it is gated on `calls.debug`, not on ordinary call read.
* `GET /api/calls/{call_id}/pcap` lists what exists for the call and mints a
  short-lived URL for the newest artefact. The mint is what gets audited: the
  evidence we need is who looked at the packets.
* `GET /api/calls/debug-artifacts/{key}` serves the file if the signature and
  expiry check out. The token is verified, not trusted.

Retention purge is tenant-scoped (`POST .../purge`) and is safe to run
repeatedly. A cross-tenant scheduler must invoke it once for each tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.config import settings
from app.db.session import get_session
from app.db.models import Call
from app.db.telephony_models import CallDebugArtifact, TelephonyCallSession
from app.telephony import pcap as capture_logic
from app.telephony.sip.sip_gateway_client import build_gateway_client
from app.telephony.provider_errors import TelephonyError

router = APIRouter(tags=["call-debug"])

#: Where captures live when the deployment has not configured a bucket. The
#: path is intentionally under the app's data directory so a container restart
#: does not silently orphan artefacts the database still references.
DEFAULT_CAPTURE_DIR = "./var/pcap"


def _store() -> capture_logic.CaptureStore:
    root = getattr(settings, "pcap_local_path", "") or DEFAULT_CAPTURE_DIR
    secret = getattr(settings, "pcap_signing_secret", "") or getattr(
        settings, "telephony_webhook_secret", ""
    )
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="capture signing is not configured",
        )
    if str(getattr(settings, "knowledge_storage_backend", "local")).lower() == "s3":
        try:
            return capture_logic.S3CaptureStore(secret=secret)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="S3 packet-capture storage is not configured",
            ) from exc
    try:
        return capture_logic.LocalCaptureStore(root, secret=secret)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="local packet-capture storage is not configured",
        ) from exc


def _gateway():
    return build_gateway_client()


async def _authorized_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> str:
    """Require a tenant-owned row and return the gateway's live-call key."""
    legacy_call_sid = await session.scalar(
        select(Call.call_sid).where(Call.id == call_id, Call.tenant_id == tenant_id)
    )
    if legacy_call_sid is not None:
        return str(legacy_call_sid)
    provider_call_id = await session.scalar(
        select(TelephonyCallSession.provider_call_id).where(
            TelephonyCallSession.id == call_id,
            TelephonyCallSession.tenant_id == tenant_id,
        )
    )
    if provider_call_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")
    return str(provider_call_id)


async def _artifact(
    session: AsyncSession, tenant_id: uuid.UUID, artifact_id: uuid.UUID
) -> CallDebugArtifact:
    row = (
        await session.execute(
            select(CallDebugArtifact).where(
                CallDebugArtifact.id == artifact_id,
                CallDebugArtifact.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
    return row


@router.post("/api/calls/{call_id}/pcap", status_code=status.HTTP_201_CREATED)
async def capture_call_packets(
    call_id: uuid.UUID,
    seconds: int = Query(default=30, ge=1, le=300),
    retention_hours: int | None = Query(default=None, ge=1, le=720),
    ctx: TenantContext = Depends(require_permission(Permission.CALLS_DEBUG)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    gateway_call_id = await _authorized_call(session, ctx.tenant_id, call_id)
    request = capture_logic.CaptureRequest(
        tenant_id=str(ctx.tenant_id),
        call_id=str(call_id),
        seconds=seconds,
        gateway_call_id=str(gateway_call_id or call_id),
    )
    try:
        outcome = await capture_logic.capture_call(
            session,
            tenant_id=ctx.tenant_id,
            call_id=call_id,
            gateway=_gateway(),
            store=_store(),
            request=request,
            retention_hours=retention_hours,
        )
    except capture_logic.CaptureError as exc:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            event_type="call.debug.capture",
            actor_user_id=getattr(ctx, "user_id", None),
            resource_type="call",
            resource_id=str(call_id),
            result="failure",
            detail={"seconds": seconds, "error_code": exc.code},
        )
        await session.commit()
        unavailable = {"gateway_unsupported", "storage_write_failed"}
        invalid_upstream = {"invalid_capture", "capture_too_large"}
        if exc.code in unavailable:
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif exc.code in invalid_upstream:
            code = status.HTTP_502_BAD_GATEWAY
        else:
            code = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)}) from exc
    except TelephonyError as exc:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            event_type="call.debug.capture",
            actor_user_id=getattr(ctx, "user_id", None),
            resource_type="call",
            resource_id=str(call_id),
            result="failure",
            detail={"seconds": seconds, "error_code": exc.code},
        )
        await session.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from exc
    except HTTPException:
        # Store construction happens inside the try block above. If signing or
        # storage configuration is unavailable, keep the authorized failure in
        # the same audit trail as gateway and capture-validation failures.
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            event_type="call.debug.capture",
            actor_user_id=getattr(ctx, "user_id", None),
            resource_type="call",
            resource_id=str(call_id),
            result="failure",
            detail={"seconds": seconds, "error_code": "capture_storage_unavailable"},
        )
        await session.commit()
        raise

    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        event_type="call.debug.capture",
        actor_user_id=getattr(ctx, "user_id", None),
        resource_type="call",
        resource_id=str(call_id),
        detail={
            "artifact_id": outcome.artifact_id,
            "seconds": seconds,
            "packet_count": outcome.packet_count,
            "truncated": outcome.truncated,
        },
    )
    await session.commit()
    return outcome.as_dict()


@router.get("/api/calls/{call_id}/pcap")
async def list_call_captures(
    call_id: uuid.UUID,
    ttl_seconds: int = Query(default=capture_logic.DEFAULT_URL_TTL_SECONDS, ge=30, le=3600),
    ctx: TenantContext = Depends(require_permission(Permission.CALLS_DEBUG)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        await session.execute(
            select(CallDebugArtifact)
            .where(
                CallDebugArtifact.tenant_id == ctx.tenant_id,
                CallDebugArtifact.call_id == call_id,
            )
            .order_by(CallDebugArtifact.captured_at.desc())
        )
    ).scalars().all()
    artifacts = [
        {
            "id": str(row.id),
            "kind": row.kind,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "packet_count": row.packet_count,
            "truncated": row.truncated,
            "captured_at": row.captured_at.isoformat() if row.captured_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "purged_at": row.purged_at.isoformat() if row.purged_at else None,
            "access_count": row.access_count,
        }
        for row in rows
    ]
    if not rows:
        return {"artifacts": [], "download": None}

    live = [row for row in rows if row.purged_at is None]
    download = None
    if live:
        download_result = "success"
        download_error_code = None
        try:
            download = await capture_logic.sign_download(
                session,
                live[0],
                store=_store(),
                user_id=str(getattr(ctx, "user_id", "") or ""),
                ttl_seconds=ttl_seconds,
            )
        except capture_logic.CaptureError as exc:
            download = {"error": {"code": exc.code, "message": str(exc)}}
            download_result = "failure"
            download_error_code = exc.code
        except HTTPException:
            await record_event(
                session,
                tenant_id=ctx.tenant_id,
                event_type="call.debug.download",
                actor_user_id=getattr(ctx, "user_id", None),
                resource_type="call",
                resource_id=str(call_id),
                result="failure",
                detail={
                    "artifact_id": str(live[0].id),
                    "error_code": "capture_storage_unavailable",
                },
            )
            await session.commit()
            raise
        audit_detail = {"artifact_id": str(live[0].id)}
        if download_error_code:
            audit_detail["error_code"] = download_error_code
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            event_type="call.debug.download",
            actor_user_id=getattr(ctx, "user_id", None),
            resource_type="call",
            resource_id=str(call_id),
            result=download_result,
            detail=audit_detail,
        )
        await session.commit()
    return {"artifacts": artifacts, "download": download}


@router.get("/api/calls/debug-artifacts/{storage_key:path}")
async def download_capture(
    storage_key: str,
    expires: int = Query(...),
    sig: str = Query(...),
) -> Response:
    """Deliver a capture file. Signature and expiry are both required."""
    store = _store()
    if not store.verify(storage_key, expires=expires, signature=sig):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="the capture link is invalid or expired"
        )
    try:
        data = await store.get(storage_key)
    except capture_logic.CaptureError as exc:
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if exc.code == "storage_read_failed"
            else status.HTTP_404_NOT_FOUND
        )
        detail = (
            "capture storage is temporarily unavailable"
            if status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            else "the capture has been purged or is unavailable"
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    filename = storage_key.rsplit("/", 1)[-1].replace('"', "")
    return Response(
        content=data,
        media_type="application/vnd.tcpdump.pcap",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/calls/debug-artifacts/purge")
async def purge_captures(
    ctx: TenantContext = Depends(require_permission(Permission.CALLS_DEBUG)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        result = await capture_logic.purge_expired(
            session,
            tenant_id=ctx.tenant_id,
            store=_store(),
            now=datetime.now(timezone.utc),
        )
    except HTTPException:
        await record_event(
            session,
            tenant_id=ctx.tenant_id,
            event_type="call.debug.purge",
            actor_user_id=ctx.user_id,
            actor_email=getattr(ctx.user, "email", None),
            resource_type="call_debug_artifacts",
            result="failure",
            detail={
                "purged": 0,
                "examined": 0,
                "failed": 0,
                "error_code": "capture_storage_unavailable",
            },
        )
        await session.commit()
        raise
    failed = result["examined"] - result["purged"]
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        event_type="call.debug.purge",
        actor_user_id=ctx.user_id,
        actor_email=getattr(ctx.user, "email", None),
        resource_type="call_debug_artifacts",
        result="failure" if failed else "success",
        detail={"purged": result["purged"], "examined": result["examined"], "failed": failed},
    )
    await session.commit()
    return result
