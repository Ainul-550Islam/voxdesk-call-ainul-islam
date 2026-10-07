"""Read-only enterprise security posture, backed by current application state.

This endpoint reports configuration and persisted inventory. It does not claim
external certification, SSO/SCIM enforcement, a vault, or a compliance result.
Sensitive configuration values and credential material are never returned.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.models import APIKey, ServiceAccountCredential, UserSession
from app.auth.identity.policies import load_policy
from app.auth.permissions import Permission
from app.auth.rbac import describe_roles
from app.core.config import settings
from app.db.models import AuditLog, Environment
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/enterprise-security", tags=["enterprise-security"])


def _policy_payload(policy) -> dict:
    return {
        "mfa_required": policy.mfa_required,
        "mfa_required_for_admins": policy.mfa_required_for_admins,
        "privileged_reauth_required": policy.privileged_reauth_required,
        "sso_required": policy.sso_required,
        "password_login_allowed": policy.password_login_allowed,
        "api_keys_allowed": policy.api_keys_allowed,
        "service_accounts_allowed": policy.service_accounts_allowed,
        "scim_enabled": policy.scim_enabled,
        "jit_provisioning_allowed": policy.jit_provisioning_allowed,
        "session_idle_minutes": policy.session_idle_minutes,
        "session_max_active": policy.session_max_active,
        "refresh_token_days": policy.refresh_token_days,
        "privileged_reauth_minutes": policy.privileged_reauth_minutes,
        "allowed_email_domains": list(policy.allowed_email_domains or []),
    }


@router.get("/posture")
async def read_security_posture(
    ctx: TenantContext = Depends(require_permission(Permission.IDENTITY_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Report tenant identity policy and live credential/session inventory."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    policy = await load_policy(session, ctx.tenant_id)

    active_sessions = int(
        (
            await session.scalar(
                select(func.count(UserSession.id)).where(
                    UserSession.tenant_id == ctx.tenant_id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now,
                    UserSession.idle_expires_at > now,
                )
            )
        )
        or 0
    )
    active_keys = int(
        (
            await session.scalar(
                select(func.count(APIKey.id)).where(
                    APIKey.tenant_id == ctx.tenant_id,
                    APIKey.revoked_at.is_(None),
                    or_(APIKey.expires_at.is_(None), APIKey.expires_at > now),
                )
            )
        )
        or 0
    )
    revoked_keys = int(
        (
            await session.scalar(
                select(func.count(APIKey.id)).where(
                    APIKey.tenant_id == ctx.tenant_id,
                    APIKey.revoked_at.is_not(None),
                )
            )
        )
        or 0
    )
    active_service_credentials = int(
        (
            await session.scalar(
                select(func.count(ServiceAccountCredential.id)).where(
                    ServiceAccountCredential.tenant_id == ctx.tenant_id,
                    ServiceAccountCredential.revoked_at.is_(None),
                    or_(
                        ServiceAccountCredential.expires_at.is_(None),
                        ServiceAccountCredential.expires_at > now,
                    ),
                )
            )
        )
        or 0
    )
    environment_count = int(
        (
            await session.scalar(
                select(func.count(Environment.id)).where(
                    Environment.tenant_id == ctx.tenant_id
                )
            )
        )
        or 0
    )
    latest_audit_at = await session.scalar(
        select(func.max(AuditLog.created_at)).where(AuditLog.tenant_id == ctx.tenant_id)
    )
    from app.security.secret_store import get_secret_store

    secret_store_backend = type(get_secret_store()).__name__

    return {
        "policy": _policy_payload(policy),
        "roles": describe_roles(),
        "inventory": {
            "active_sessions": active_sessions,
            "active_api_keys": active_keys,
            "revoked_api_keys": revoked_keys,
            "active_service_account_credentials": active_service_credentials,
            "environments": environment_count,
        },
        "controls": {
            "audit_store": "audit_logs",
            "audit_tenant_scope": True,
            "audit_environment_filtering": True,
            "api_key_environment_binding_supported": True,
            "mfa_feature_configured": bool(settings.mfa_enabled),
            "sso_feature_configured": bool(settings.sso_enabled),
            "distributed_rate_limit_configured": bool(settings.redis_url),
            "rate_limit_enabled": bool(settings.rate_limit_enabled),
            "trusted_host_allowlist_configured": bool(settings.trusted_host_list),
            "forwarded_proxy_allowlist_configured": bool(settings.forwarded_allow_ip_list),
            "secret_redaction": "structured-fields-and-credential-patterns",
            "secret_store_backend": secret_store_backend,
        },
        "audit": {
            "latest_event_at": latest_audit_at.isoformat() if latest_audit_at else None,
        },
        "reported_at": now.replace(tzinfo=timezone.utc).isoformat(),
    }
