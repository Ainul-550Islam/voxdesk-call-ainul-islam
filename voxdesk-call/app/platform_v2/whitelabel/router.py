"""HTTP surface for white-label: domains, branding, assets, sender domains, widget.

Two routers live here, because two audiences call them:

* ``router`` (prefix ``/api/whitelabel``) — tenant-scoped, permission-checked,
  audited. This is what the console calls.
* ``public_router`` (prefix ``/api/platform/tls``) — the edge calls
  ``/ask`` with the shared ask token and no session. It answers 200/403 and never
  says more than "allow" or "not allowed".

The widget endpoints sit between the two: ``/api/whitelabel/widget/*`` is the
authenticated admin view (what the tenant configured), and
``/api/whitelabel/public/widget/*`` is what a browser fetches — resolved strictly
from the request ``Host``, never from a query parameter, because a host we do not
know must not receive a branded document.

Every mutation claims ``Idempotency-Key`` when present and writes its audit row in
the same transaction as the change.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.platform_v2 import http as api
from app.platform_v2.errors import NotFound, ValidationFailed
from app.platform_v2.whitelabel import assets as assets_mod
from app.platform_v2.whitelabel import branding as branding_mod
from app.platform_v2.whitelabel import domains as domains_mod
from app.platform_v2.whitelabel import email_domains as email_mod
from app.platform_v2.whitelabel import templates as templates_mod
from app.platform_v2.whitelabel import tls as tls_mod
from app.platform_v2.whitelabel import widget as widget_mod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/whitelabel", tags=["white-label"])
public_router = APIRouter(prefix="/api/platform/tls", tags=["white-label-tls"])
widget_public_router = APIRouter(prefix="/api/whitelabel/public", tags=["white-label-widget"])


# ------------------------------------------------------------------ schemas ---
class ClaimDomainIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    environment_id: uuid.UUID | None = None


class VerifyDomainIn(BaseModel):
    token: str | None = Field(default=None, max_length=128)


class ThemeIn(BaseModel):
    payload: dict[str, Any]
    activate: bool = True
    name: str = Field(default="Default", max_length=60)


class ThemeValidateIn(BaseModel):
    payload: dict[str, Any]


class SenderDomainIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    from_name: str = Field(default="", max_length=80)
    dkim_selector: str = Field(default="", max_length=63)


class TemplatePreviewIn(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


class DisableDomainIn(BaseModel):
    reason: str = Field(default="", max_length=500)


# ------------------------------------------------------------------- domains ---
@router.get("/domains")
@api.endpoint_errors
async def list_domains(
    limit: int | None = Query(default=None),
    offset: int | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_READ)),
    session: AsyncSession = Depends(get_session),
):
    size, start = api.page(limit, offset)
    rows = await domains_mod.list_for_tenant(session, tenant_id=ctx.tenant_id, limit=size, offset=start)
    return {"domains": [domains_mod.describe(row) for row in rows], "count": len(rows)}


@router.post("/domains", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def claim_domain(
    body: ClaimDomainIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Claim a hostname. The ownership token is returned **once**, in this response."""
    claim = await api.claim(session, ctx, request, operation="wl.domain.claim", payload=body.model_dump(mode="json"))
    replay = api.replay_response(claim, resource_type="wl_custom_domain")
    if replay is not None:
        return replay
    row, token = await domains_mod.claim(
        session,
        tenant_id=ctx.tenant_id,
        domain=body.domain,
        environment_id=body.environment_id,
        created_by_user_id=ctx.user_id,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.domain.claimed",
        resource_type="wl_custom_domain",
        resource_id=row.id,
        detail={"domain": row.domain},
    )
    await api.complete_claim(session, claim, resource_type="wl_custom_domain", resource_id=row.id)
    await session.commit()
    payload = domains_mod.describe(row)
    payload["ownership_token"] = token
    payload["ownership_token_notice"] = (
        "This token is shown once. Create the TXT record below and keep the value; "
        "verification also works without it while the record stays correct."
    )
    return payload


@router.get("/domains/{domain_id}")
@api.endpoint_errors
async def read_domain(
    domain_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await domains_mod.get(session, tenant_id=ctx.tenant_id, domain_id=domain_id)
    return domains_mod.describe(row)


@router.get("/domains/{domain_id}/checks")
@api.endpoint_errors
async def read_domain_checks(
    domain_id: uuid.UUID,
    limit: int | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_READ)),
    session: AsyncSession = Depends(get_session),
):
    size, _ = api.page(limit, 0)
    rows = await domains_mod.checks_for(session, tenant_id=ctx.tenant_id, domain_id=domain_id, limit=size)
    return {"checks": [domains_mod.serialise_check(row) for row in rows]}


@router.post("/domains/{domain_id}/verify")
@api.endpoint_errors
async def verify_domain(
    domain_id: uuid.UUID,
    request: Request,
    body: VerifyDomainIn | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_VERIFY)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="wl.domain.verify", payload={"domain_id": str(domain_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_custom_domain")
    if replay is not None:
        return replay
    outcome = await domains_mod.verify(
        session,
        tenant_id=ctx.tenant_id,
        domain_id=domain_id,
        token=(body.token if body else None),
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.domain.verified" if outcome.passed else "wl.domain.verification_failed",
        resource_type="wl_custom_domain",
        resource_id=domain_id,
        result="success" if outcome.passed else "failure",
        detail={"status": outcome.status, "reason": outcome.reason},
    )
    await api.complete_claim(session, claim, resource_type="wl_custom_domain", resource_id=domain_id)
    await session.commit()
    return outcome.as_dict()


@router.get("/domains/{domain_id}/certificate")
@api.endpoint_errors
async def read_certificate(
    domain_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_READ)),
    session: AsyncSession = Depends(get_session),
):
    await domains_mod.get(session, tenant_id=ctx.tenant_id, domain_id=domain_id)
    return await tls_mod.status_for(session, tenant_id=ctx.tenant_id, domain_id=domain_id)


@router.post("/domains/{domain_id}/refresh-certificate")
@api.endpoint_errors
async def refresh_certificate(
    domain_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_VERIFY)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="wl.certificate.refresh", payload={"domain_id": str(domain_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_custom_domain")
    if replay is not None:
        return replay
    result = await tls_mod.refresh_certificate(
        session, tenant_id=ctx.tenant_id, domain_id=domain_id, commit=False
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.certificate.refreshed",
        resource_type="wl_custom_domain",
        resource_id=domain_id,
        detail={"state": result.get("state"), "activated": result.get("activated")},
    )
    await api.complete_claim(session, claim, resource_type="wl_custom_domain", resource_id=domain_id)
    await session.commit()
    return result


@router.post("/domains/{domain_id}/disable")
@api.endpoint_errors
async def disable_domain(
    domain_id: uuid.UUID,
    request: Request,
    body: DisableDomainIn | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="wl.domain.disable", payload={"domain_id": str(domain_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_custom_domain")
    if replay is not None:
        return replay
    row = await domains_mod.disable(
        session,
        tenant_id=ctx.tenant_id,
        domain_id=domain_id,
        reason=(body.reason if body else ""),
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.domain.disabled",
        resource_type="wl_custom_domain",
        resource_id=domain_id,
        detail={"reason": row.failure_reason},
    )
    await api.complete_claim(session, claim, resource_type="wl_custom_domain", resource_id=domain_id)
    await session.commit()
    return domains_mod.describe(row)


@router.post("/domains/{domain_id}/enable")
@api.endpoint_errors
async def enable_domain(
    domain_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="wl.domain.enable", payload={"domain_id": str(domain_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_custom_domain")
    if replay is not None:
        return replay
    row = await domains_mod.enable(session, tenant_id=ctx.tenant_id, domain_id=domain_id)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.domain.enabled",
        resource_type="wl_custom_domain",
        resource_id=domain_id,
        detail={"status": row.status},
    )
    await api.complete_claim(session, claim, resource_type="wl_custom_domain", resource_id=domain_id)
    await session.commit()
    return domains_mod.describe(row)


@router.delete("/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
@api.endpoint_errors
async def delete_domain(
    domain_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    await domains_mod.get(session, tenant_id=ctx.tenant_id, domain_id=domain_id)
    claim = await api.claim(
        session, ctx, request, operation="wl.domain.delete", payload={"domain_id": str(domain_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_custom_domain")
    if replay is not None:
        return replay
    await domains_mod.delete(session, tenant_id=ctx.tenant_id, domain_id=domain_id)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.domain.deleted",
        resource_type="wl_custom_domain",
        resource_id=domain_id,
    )
    await api.complete_claim(session, claim, resource_type="wl_custom_domain", resource_id=domain_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -------------------------------------------------------------------- themes ---
@router.get("/theme")
@api.endpoint_errors
async def read_theme(
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    payload, version = await branding_mod.current(session, tenant_id=ctx.tenant_id)
    return {
        "payload": payload,
        "version": branding_mod.describe_version(version) if version else None,
        "defaults": branding_mod.default_theme(),
        "allowed_fonts": list(branding_mod.ALLOWED_FONTS),
    }


@router.post("/theme/validate")
@api.endpoint_errors
async def validate_theme_endpoint(
    body: ThemeValidateIn,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
):
    """Dry-run validation, so the editor can show contrast failures before saving.

    This endpoint *reports*; it never raises. A theme editor that has to parse a 422
    to colour a field red is one that will show the failure in the wrong place, so
    the errors come back in the body keyed by the field they belong to.
    """
    try:
        payload, report = branding_mod.validate_theme(body.payload)
    except ValidationFailed as exc:
        return {
            "valid": False,
            "errors": dict(exc.detail or {}),
            "message": str(exc),
            "payload": body.payload,
            "contrast": None,
        }
    return {"valid": True, "errors": {}, "payload": payload, "contrast": report}


@router.post("/theme")
@api.endpoint_errors
async def publish_theme(
    body: ThemeIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(session, ctx, request, operation="wl.theme.publish", payload=body.model_dump(mode="json"))
    replay = api.replay_response(claim, resource_type="wl_brand_theme_version")
    if replay is not None:
        return replay
    version = await branding_mod.publish(
        session,
        tenant_id=ctx.tenant_id,
        payload=body.payload,
        created_by_user_id=ctx.user_id,
        activate=body.activate,
        name=body.name,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.theme.published" if body.activate else "wl.theme.drafted",
        resource_type="wl_brand_theme_version",
        resource_id=version.id,
        detail={"version_number": version.version_number, "activated": body.activate},
    )
    await api.complete_claim(session, claim, resource_type="wl_brand_theme_version", resource_id=version.id)
    await session.commit()
    return branding_mod.describe_version(version)


@router.get("/theme/versions")
@api.endpoint_errors
async def list_theme_versions(
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await branding_mod.versions(session, tenant_id=ctx.tenant_id)
    return {"versions": [branding_mod.describe_version(row) for row in rows]}


@router.post("/theme/versions/{version_id}/activate")
@api.endpoint_errors
async def activate_theme_version(
    version_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="wl.theme.activate", payload={"version_id": str(version_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_brand_theme_version")
    if replay is not None:
        return replay
    version = await branding_mod.activate_version(session, tenant_id=ctx.tenant_id, version_id=version_id)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.theme.activated",
        resource_type="wl_brand_theme_version",
        resource_id=version.id,
        detail={"version_number": version.version_number},
    )
    await api.complete_claim(session, claim, resource_type="wl_brand_theme_version", resource_id=version.id)
    await session.commit()
    return branding_mod.describe_version(version)


# -------------------------------------------------------------------- assets ---
@router.get("/assets")
@api.endpoint_errors
async def list_assets(
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await assets_mod.list_for_tenant(session, tenant_id=ctx.tenant_id)
    return {"assets": [assets_mod.describe(row) for row in rows]}


@router.post("/assets", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form(...),
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Upload a logo or favicon. The bytes are validated and SVGs are sanitised."""
    raw = await file.read()
    claim = await api.claim(
        session,
        ctx,
        request,
        operation="wl.asset.upload",
        payload={"kind": kind, "filename": file.filename, "size": len(raw)},
    )
    replay = api.replay_response(claim, resource_type="wl_brand_asset")
    if replay is not None:
        return replay
    validated = assets_mod.validate_upload(
        kind=kind, filename=file.filename or "asset", content_type=file.content_type, data=raw
    )
    row, created = await assets_mod.store(
        session, tenant_id=ctx.tenant_id, asset=validated, created_by_user_id=ctx.user_id
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.asset.uploaded",
        resource_type="wl_brand_asset",
        resource_id=row.id,
        detail={
            "kind": row.kind,
            "content_type": row.content_type,
            "sanitized": row.sanitized,
            "removed": validated.removed,
            "created": created,
        },
    )
    await api.complete_claim(session, claim, resource_type="wl_brand_asset", resource_id=row.id)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        if created:
            try:
                await assets_mod.delete_storage_path(row.storage_path, tenant_id=ctx.tenant_id)
            except Exception:
                logger.exception("Failed to clean up uncommitted brand asset %s", row.id)
        raise
    payload = assets_mod.describe(row)
    payload["deduplicated"] = not created
    payload["removed_from_svg"] = validated.removed
    return payload


@router.get("/assets/{asset_id}")
@api.endpoint_errors
async def read_asset(
    asset_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await assets_mod.get(session, tenant_id=ctx.tenant_id, asset_id=asset_id)
    return assets_mod.describe(row)


@router.get("/assets/{asset_id}/content")
@api.endpoint_errors
async def read_asset_content(
    asset_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Serve the stored bytes with the sniffed content type and a tight CSP."""
    row = await assets_mod.get(session, tenant_id=ctx.tenant_id, asset_id=asset_id)
    data = await assets_mod.read_bytes(row)
    headers = {
        "Content-Type": row.content_type,
        "Content-Disposition": f'inline; filename="{row.filename}"',
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
        # An SVG is a document: it must not be able to run script even when a
        # browser opens it directly.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
    }
    return Response(content=data, media_type=row.content_type, headers=headers)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
@api.endpoint_errors
async def delete_asset(
    asset_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    await assets_mod.get(session, tenant_id=ctx.tenant_id, asset_id=asset_id)
    claim = await api.claim(
        session, ctx, request, operation="wl.asset.delete", payload={"asset_id": str(asset_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_brand_asset")
    if replay is not None:
        return replay
    storage_path = await assets_mod.delete(session, tenant_id=ctx.tenant_id, asset_id=asset_id)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.asset.deleted",
        resource_type="wl_brand_asset",
        resource_id=asset_id,
    )
    await api.complete_claim(session, claim, resource_type="wl_brand_asset", resource_id=asset_id)
    await session.commit()
    try:
        await assets_mod.delete_storage_path(storage_path, tenant_id=ctx.tenant_id)
    except Exception:
        # The database delete is already committed. Keep the response successful;
        # the private orphan can be removed by storage lifecycle/operations.
        logger.exception("Failed to remove storage for deleted brand asset %s", asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------- sender domains ---
@router.get("/email-domains")
@api.endpoint_errors
async def list_sender_domains(
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await email_mod.list_for_tenant(session, tenant_id=ctx.tenant_id)
    return {"sender_domains": [email_mod.describe(row) for row in rows]}


@router.post("/email-domains", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def create_sender_domain(
    body: SenderDomainIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="wl.sender_domain.create", payload=body.model_dump()
    )
    replay = api.replay_response(claim, resource_type="wl_email_sender_domain")
    if replay is not None:
        return replay
    row, instructions = await email_mod.create(
        session,
        tenant_id=ctx.tenant_id,
        domain=body.domain,
        from_name=body.from_name,
        dkim_selector=body.dkim_selector,
        created_by_user_id=ctx.user_id,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.sender_domain.created",
        resource_type="wl_email_sender_domain",
        resource_id=row.id,
        detail={"domain": row.domain, "dkim_selector": row.dkim_selector},
    )
    await api.complete_claim(session, claim, resource_type="wl_email_sender_domain", resource_id=row.id)
    await session.commit()
    payload = email_mod.describe(row)
    payload["instructions"] = instructions
    return payload


@router.post("/email-domains/{sender_id}/verify")
@api.endpoint_errors
async def verify_sender_domain(
    sender_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="wl.sender_domain.verify", payload={"sender_id": str(sender_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_email_sender_domain")
    if replay is not None:
        return replay
    result = await email_mod.verify(session, tenant_id=ctx.tenant_id, sender_id=sender_id)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.sender_domain.verified" if result["verified"] else "wl.sender_domain.failed",
        resource_type="wl_email_sender_domain",
        resource_id=sender_id,
        result="success" if result["verified"] else "failure",
        detail={"status": result["status"], "reasons": result["reasons"]},
    )
    await api.complete_claim(session, claim, resource_type="wl_email_sender_domain", resource_id=sender_id)
    await session.commit()
    return result


@router.get("/email-domains/{sender_id}/records")
@api.endpoint_errors
async def read_sender_records(
    sender_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    sender = await email_mod.get(session, tenant_id=ctx.tenant_id, sender_id=sender_id)
    rows = await email_mod.records_for(session, tenant_id=ctx.tenant_id, sender_id=sender_id)
    return {
        "sender_id": str(sender.id),
        "domain": sender.domain,
        "status": sender.status,
        # What the tenant must publish. This is the same shape the create call
        # returned, so a console can render the table before any check has run.
        "records": email_mod.describe(sender)["records"],
        # What the last verification actually observed, one row per attempt.
        "checks": [
            {
                "id": str(row.id),
                "record_type": row.record_type,
                "name": row.name,
                "expected": row.expected,
                "observed": row.observed,
                "passed": row.passed,
                "detail": row.detail,
                "checked_at": row.checked_at.isoformat() if row.checked_at else None,
            }
            for row in rows
        ],
    }


@router.delete("/email-domains/{sender_id}", status_code=status.HTTP_204_NO_CONTENT)
@api.endpoint_errors
async def delete_sender_domain(
    sender_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    await email_mod.get(session, tenant_id=ctx.tenant_id, sender_id=sender_id)
    claim = await api.claim(
        session, ctx, request, operation="wl.sender_domain.delete", payload={"sender_id": str(sender_id)}
    )
    replay = api.replay_response(claim, resource_type="wl_email_sender_domain")
    if replay is not None:
        return replay
    await email_mod.delete(session, tenant_id=ctx.tenant_id, sender_id=sender_id)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wl.sender_domain.deleted",
        resource_type="wl_email_sender_domain",
        resource_id=sender_id,
    )
    await api.complete_claim(session, claim, resource_type="wl_email_sender_domain", resource_id=sender_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------- templates ---
@router.get("/templates")
@api.endpoint_errors
async def list_templates(
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
):
    return {"templates": templates_mod.available()}


@router.post("/templates/{name}/preview")
@api.endpoint_errors
async def preview_template(
    name: str,
    body: TemplatePreviewIn,
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Render a branded email with the active theme, for the console's preview pane."""
    theme, _version = await branding_mod.current(session, tenant_id=ctx.tenant_id)
    rendered = templates_mod.render(name, body.variables, theme)
    return rendered.as_dict()


# -------------------------------------------------------- widget (admin view) ---
@router.get("/widget")
@api.endpoint_errors
async def read_widget_admin(
    ctx: TenantContext = Depends(require_permission(Permission.BRAND_READ)),
    session: AsyncSession = Depends(get_session),
):
    theme, _version = await branding_mod.current(session, tenant_id=ctx.tenant_id)
    return {
        "config": widget_mod.config_for(ctx.tenant_id, theme),
        "loader_script": widget_mod.loader_script(widget_mod.config_for(ctx.tenant_id, theme)),
        "content_security_policy": widget_mod.content_security_policy(theme),
        "frame_ancestors": widget_mod.frame_ancestors(theme),
        "cache_seconds": widget_mod.LOADER_MAX_AGE_SECONDS,
    }


# ------------------------------------------------------ widget (public, by host) ---
@widget_public_router.get("/widget/{public_key}/loader.js")
@api.endpoint_errors
async def public_widget_loader(
    public_key: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """The script a tenant's site loads.

    Resolved from ``Host`` **and** the public key: the host decides the tenant, the
    key is checked against that tenant so a key copied from one tenant's page
    cannot be used to pull another tenant's theme onto a domain that is not theirs.
    A host we do not serve is a 404 — never the default theme.
    """
    host = request.headers.get("host", "")
    theme = await branding_mod.theme_for_host(session, host=host)
    if theme is None:
        raise NotFound("host is not a configured tenant domain", detail={"host": host})
    tenant_id = uuid.UUID(theme["_meta"]["tenant_id"])
    expected = widget_mod.public_key_for(tenant_id)
    if public_key != expected:
        raise NotFound("widget key does not belong to this host")
    script = widget_mod.loader_script(widget_mod.config_for(tenant_id, theme))
    return Response(
        content=script,
        media_type="application/javascript",
        headers={
            "Cache-Control": f"public, max-age={widget_mod.LOADER_MAX_AGE_SECONDS}",
            "X-Content-Type-Options": "nosniff",
            "Access-Control-Allow-Origin": "*",
        },
    )


@widget_public_router.get("/widget/{public_key}/config")
@api.endpoint_errors
async def public_widget_config(
    public_key: str,
    request: Request,
    origin: str | None = Header(default=None, alias="Origin"),
    session: AsyncSession = Depends(get_session),
):
    """Widget configuration for an authorised embedding origin."""
    host = request.headers.get("host", "")
    theme = await branding_mod.theme_for_host(session, host=host)
    if theme is None:
        raise NotFound("host is not a configured tenant domain", detail={"host": host})
    tenant_id = uuid.UUID(theme["_meta"]["tenant_id"])
    if public_key != widget_mod.public_key_for(tenant_id):
        raise NotFound("widget key does not belong to this host")
    widget_mod.validate_embed_origin(origin, theme)
    return Response(
        content=json.dumps(widget_mod.config_for(tenant_id, theme)),
        media_type="application/json",
        headers=widget_mod.headers(theme),
    )


# ------------------------------------------------------------------ edge (ask) ---
@public_router.get("/ask")
@api.endpoint_errors
async def tls_ask(
    request: Request,
    domain: str = Query(..., min_length=3, max_length=253),
    ask_token: str | None = Header(default=None, alias=tls_mod.ASK_TOKEN_HEADER),
    session: AsyncSession = Depends(get_session),
):
    """The on-demand certificate decision Caddy asks for.

    The decision function returns only the compact allow/deny body expected by the
    edge; its token check and every ordinary refusal produce status 403. The shared
    endpoint wrapper only translates unexpected/domain exceptions, not deny results.
    """
    decision = await tls_mod.decide(session, domain=domain, provided_token=ask_token)
    return Response(
        content=json.dumps(decision.body()),
        status_code=decision.status_code,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@public_router.get("/status")
@api.endpoint_errors
async def tls_status(
    domain: str = Query(..., min_length=3, max_length=253),
    ctx: TenantContext = Depends(require_permission(Permission.DOMAIN_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Certificate state for a domain the caller's tenant owns."""
    normalized = domains_mod.validate_claim_input(domain)
    row = None
    for candidate in await domains_mod.list_for_tenant(session, tenant_id=ctx.tenant_id, limit=500):
        if candidate.domain == normalized:
            row = candidate
            break
    if row is None:
        raise NotFound("domain not found")
    payload = await tls_mod.status_for(session, tenant_id=ctx.tenant_id, domain_id=row.id)
    payload["domain_status"] = row.status
    return payload


__all__ = ["public_router", "router", "widget_public_router"]
