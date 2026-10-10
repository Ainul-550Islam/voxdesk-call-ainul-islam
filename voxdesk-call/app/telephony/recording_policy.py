"""Tenant, environment, and agent recording, consent, and retention policy."""

from __future__ import annotations

import html
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.core.config import settings
from app.db.models import AuditAction, Base, Tenant, UserRole, _uuid
from app.tenancy.isolation import Forbidden, NotFound

CONSENT_MODES = {"none", "one_party", "two_party", "explicit"}
DEFAULT_DISCLOSURE_TEXT = "This call may be recorded for quality and training purposes."


def _default_disclosure_text() -> str:
    return (
        getattr(settings, "recording_disclosure_text", None)
        or getattr(settings, "call_recording_disclosure", None)
        or DEFAULT_DISCLOSURE_TEXT
    )


def _agent_scope(agent_id: Any) -> str:
    raw = str(agent_id or "").strip()
    if not raw:
        return "all"
    try:
        return f"agent:{uuid.UUID(raw).hex}"
    except (ValueError, TypeError):
        return f"agent:{raw[:34]}"


class RecordingPolicy(Base):
    __tablename__ = "recording_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "environment_scope", name="uq_recording_policies_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("environments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    environment_scope: Mapped[str] = mapped_column(String(40), default="all", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consent_mode: Mapped[str] = mapped_column(String(24), default="one_party", nullable=False)
    disclosure_text: Mapped[str] = mapped_column(
        Text, default=_default_disclosure_text, nullable=False
    )
    retention_days: Mapped[int] = mapped_column(
        Integer, default=lambda: settings.call_retention_days, nullable=False
    )
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_access_roles: Mapped[list] = mapped_column(
        JSON, default=lambda: ["owner", "admin"], nullable=False
    )
    redact_pii: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    def as_dict(self) -> dict[str, Any]:
        agent_id = None
        if self.environment_scope and self.environment_scope.startswith("agent:"):
            raw = self.environment_scope.split(":", 1)[1]
            try:
                agent_id = str(uuid.UUID(raw))
            except (ValueError, TypeError):
                agent_id = raw
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_id": str(self.environment_id) if self.environment_id else None,
            "environment_scope": self.environment_scope,
            "agent_id": agent_id,
            "enabled": self.enabled,
            "consent_mode": self.consent_mode,
            "disclosure_text": self.disclosure_text,
            "retention_days": self.retention_days,
            "legal_hold": self.legal_hold,
            "raw_access_roles": list(self.raw_access_roles or []),
            "redact_pii": self.redact_pii,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


async def effective(
    session: AsyncSession,
    tenant: Tenant | None,
    *,
    environment_id: uuid.UUID | None = None,
    agent_id: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    """Resolve effective policy for ``(tenant_id, agent_id, environment_id)``.

    Resolution order:
    1. Agent-scoped ``RecordingPolicy`` (``environment_scope == agent:<id>``) or
       agent-scoped ``RetentionPolicy`` metadata override.
    2. Environment-scoped ``RecordingPolicy`` (``environment_scope == <environment_id>``).
    3. Tenant-wide ``RecordingPolicy`` (``environment_scope == "all"``) or
       tenant-wide ``RetentionPolicy`` metadata override.
    4. System defaults.
    """
    if tenant is None:
        return {
            "enabled": False,
            "consent_mode": "one_party",
            "disclosure_text": _default_disclosure_text(),
            "retention_days": settings.call_retention_days,
            "legal_hold": False,
            "raw_access_roles": ["owner", "admin"],
            "redact_pii": True,
        }

    row: RecordingPolicy | None = None
    if agent_id:
        agent_scope = _agent_scope(agent_id)
        row = (
            await session.execute(
                select(RecordingPolicy).where(
                    RecordingPolicy.tenant_id == tenant.id,
                    RecordingPolicy.environment_scope == agent_scope,
                )
            )
        ).scalar_one_or_none()
    if row is None and environment_id is not None:
        row = (
            await session.execute(
                select(RecordingPolicy).where(
                    RecordingPolicy.tenant_id == tenant.id,
                    RecordingPolicy.environment_scope == str(environment_id),
                )
            )
        ).scalar_one_or_none()
    if row is None:
        row = (
            await session.execute(
                select(RecordingPolicy).where(
                    RecordingPolicy.tenant_id == tenant.id,
                    RecordingPolicy.environment_scope == "all",
                )
            )
        ).scalar_one_or_none()

    # Check RetentionPolicy for agent/tenant overrides or legal hold
    from app.db.enterprise_models import RetentionPolicy

    ret_row: RetentionPolicy | None = None
    if agent_id:
        ret_row = (
            await session.execute(
                select(RetentionPolicy)
                .where(
                    RetentionPolicy.tenant_id == tenant.id,
                    RetentionPolicy.agent_id == str(agent_id),
                )
                .order_by(RetentionPolicy.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if ret_row is None:
        ret_row = (
            await session.execute(
                select(RetentionPolicy)
                .where(
                    RetentionPolicy.tenant_id == tenant.id,
                    RetentionPolicy.agent_id == "",
                )
                .order_by(RetentionPolicy.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if row is not None:
        result = row.as_dict()
        if ret_row is not None and ret_row.legal_hold:
            result["legal_hold"] = True
        return result

    meta = dict(ret_row.meta or {}) if ret_row is not None and isinstance(ret_row.meta, dict) else {}
    return {
        "enabled": bool(meta["enabled"]) if "enabled" in meta else bool(getattr(tenant, "record_calls", False)),
        "consent_mode": str(meta.get("consent_mode") or "one_party"),
        "disclosure_text": str(meta.get("disclosure_text") or _default_disclosure_text()),
        "retention_days": int(ret_row.retention_days) if ret_row is not None else settings.call_retention_days,
        "legal_hold": bool(ret_row.legal_hold) if ret_row is not None else False,
        "raw_access_roles": list(meta.get("raw_access_roles") or ["owner", "admin"]),
        "redact_pii": bool(meta["redact_pii"]) if "redact_pii" in meta else True,
    }


def disclosure_twiml(policy: dict[str, Any] | RecordingPolicy) -> str | None:
    """Return a ``<Say>`` TwiML fragment when two-party/explicit consent is active."""
    data = policy.as_dict() if isinstance(policy, RecordingPolicy) else dict(policy or {})
    if not data.get("enabled", True):
        return None
    consent_mode = str(data.get("consent_mode") or "one_party").lower()
    if consent_mode not in {"two_party", "explicit"}:
        return None
    text = str(data.get("disclosure_text") or settings.call_recording_disclosure).strip()
    if not text:
        return None
    return f"<Say>{html.escape(text)}</Say>"


def prepend_disclosure_twiml(response: Any, policy: dict[str, Any] | RecordingPolicy) -> bool:
    """Prepend the disclosure prompt onto a Twilio ``VoiceResponse`` before streaming."""
    data = policy.as_dict() if isinstance(policy, RecordingPolicy) else dict(policy or {})
    if not data.get("enabled", True):
        return False
    consent_mode = str(data.get("consent_mode") or "one_party").lower()
    if consent_mode not in {"two_party", "explicit"}:
        return False
    text = str(data.get("disclosure_text") or settings.call_recording_disclosure).strip()
    if not text:
        return False
    response.say(text)
    return True


async def save(
    session: AsyncSession,
    tenant: Tenant,
    *,
    environment_id: uuid.UUID | None = None,
    agent_id: str | uuid.UUID | None = None,
    enabled: bool = True,
    consent_mode: str = "one_party",
    disclosure_text: str | None = None,
    retention_days: int | None = None,
    legal_hold: bool = False,
    raw_access_roles: list[str] | None = None,
    redact_pii: bool = True,
    actor_user_id: uuid.UUID | None = None,
    actor_role: UserRole | None = None,
) -> RecordingPolicy:
    if consent_mode not in CONSENT_MODES:
        raise ValueError("consent_mode must be none, one_party, two_party, or explicit")
    days = settings.call_retention_days if retention_days is None else int(retention_days)
    if days < 1 or days > 3650:
        raise ValueError("retention_days must be between 1 and 3650")

    if redact_pii is False and actor_role is not None:
        if not has_permission(actor_role, Permission.SECURITY_WRITE):
            raise Forbidden("disabling PII redaction requires security:settings permission")

    if agent_id is not None and str(agent_id).strip():
        scope = _agent_scope(agent_id)
    elif environment_id is not None:
        scope = str(environment_id)
    else:
        scope = "all"

    row = (
        await session.execute(
            select(RecordingPolicy).where(
                RecordingPolicy.tenant_id == tenant.id,
                RecordingPolicy.environment_scope == scope,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = RecordingPolicy(
            tenant_id=tenant.id,
            environment_id=environment_id,
            environment_scope=scope,
        )
        session.add(row)
    row.enabled = bool(enabled)
    row.consent_mode = consent_mode
    default_disc = (
        getattr(settings, "recording_disclosure_text", None)
        or getattr(settings, "call_recording_disclosure", None)
        or DEFAULT_DISCLOSURE_TEXT
    )
    row.disclosure_text = (disclosure_text or default_disc)[:500]
    row.retention_days = days
    row.legal_hold = bool(legal_hold)
    row.raw_access_roles = [r for r in (raw_access_roles or ["owner", "admin"]) if isinstance(r, str)]
    row.redact_pii = bool(redact_pii)
    row.updated_at = datetime.utcnow()
    await session.flush()

    if not row.redact_pii:
        from app.auth.identity.events import emit

        await emit(
            session,
            AuditAction.PII_REDACTION_DISABLED,
            tenant_id=tenant.id,
            actor_user_id=actor_user_id,
            detail={
                "operation": "pii_redaction_disabled",
                "policy_id": str(row.id),
                "environment_scope": scope,
            },
            commit=False,
        )

    return row


async def get_owned(
    session: AsyncSession, tenant_id: uuid.UUID, policy_id: uuid.UUID
) -> RecordingPolicy:
    row = await session.get(RecordingPolicy, policy_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound("recording policy not found")
    return row


def deadline_from(days: int, *, now: datetime | None = None) -> datetime:
    base = now or datetime.utcnow()
    return base + timedelta(days=max(1, int(days)))


deadline = deadline_from
