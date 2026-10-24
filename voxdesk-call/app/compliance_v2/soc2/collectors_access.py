"""Access-control evidence: who exists, who can administer, who can log in how.

Everything here is counted from the real tables — ``users``,
``auth.identity.models`` (factors, sessions, SSO connections, SCIM credentials,
API keys, service accounts) and ``identity_policies``. Nothing is inferred, and
an account the deployment cannot classify is counted as a finding rather than
excluded.

The findings are deliberately mechanical. "Three active administrators have no
second factor" is evidence; "access is well controlled" is a claim, and this
module does not make claims.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.soc2.collectors_base import CollectorResult, EvidenceCollector, item
from app.db.models import User, UserRole

#: An active account with no session activity for this long is reported dormant.
DORMANT_DAYS = 90

#: Controls this collector owns.
CONTROL_AUTHENTICATION = "CC6.1"
CONTROL_PROVISIONING = "CC6.2"
CONTROL_RBAC = "CC6.3"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


class AccessCollector(EvidenceCollector):
    """Users, roles, factors, sessions, provisioning and machine credentials."""

    name = "access"
    control_ids = (CONTROL_AUTHENTICATION, CONTROL_PROVISIONING, CONTROL_RBAC)

    async def collect(self, session: AsyncSession) -> "CollectorResult":
        from app.auth.identity.models import (
            APIKey,
            IdentityPolicy,
            MFAFactor,
            SCIMCredential,
            SSOConnection,
            ServiceAccount,
            UserSession,
        )

        rows = (
            await session.execute(
                select(User.id, User.role, User.is_active, User.tenant_id, User.email)
            )
        ).all()
        total_users = len(rows)
        active_users = sum(1 for row in rows if row.is_active)
        role_counts: dict[str, int] = {}
        for row in rows:
            role_counts[str(getattr(row.role, "value", row.role))] = (
                role_counts.get(str(getattr(row.role, "value", row.role)), 0) + 1
            )

        tenant_ids = sorted({row.tenant_id for row in rows})
        admin_ids = {
            row.id
            for row in rows
            if row.is_active
            and str(getattr(row.role, "value", row.role)) in {UserRole.ADMIN.value, UserRole.OWNER.value}
        }

        factors = (
            await session.execute(
                select(MFAFactor.user_id, MFAFactor.status).where(MFAFactor.status == "active")
            )
        ).all()
        users_with_factor = {row.user_id for row in factors}
        admins_without_factor = sorted(str(uid) for uid in admin_ids if uid not in users_with_factor)

        policies = list((await session.execute(select(IdentityPolicy))).scalars())
        policy_by_tenant = {row.tenant_id: row for row in policies}
        tenants_requiring_mfa = [
            str(tenant_id)
            for tenant_id in tenant_ids
            if tenant_id in policy_by_tenant and policy_by_tenant[tenant_id].mfa_required
        ]

        live_sessions = (
            await session.execute(
                select(func.count(UserSession.id)).where(
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > _utcnow(),
                    UserSession.idle_expires_at > _utcnow(),
                )
            )
        ).scalar_one()

        cutoff = _utcnow() - dt.timedelta(days=DORMANT_DAYS)
        last_seen = (
            await session.execute(
                select(UserSession.user_id, func.max(UserSession.last_seen_at)).group_by(
                    UserSession.user_id
                )
            )
        ).all()
        seen = {row[0]: _aware(row[1]) for row in last_seen}
        dormant = [
            str(row.id)
            for row in rows
            if row.is_active and (seen.get(row.id) is None or (seen.get(row.id) or cutoff) < cutoff)
        ]

        sso_connections = (
            await session.execute(select(SSOConnection.status))
        ).scalars().all()
        scim_credentials = (
            await session.execute(
                select(func.count(SCIMCredential.id)).where(SCIMCredential.revoked_at.is_(None))
            )
        ).scalar_one()
        active_api_keys = (
            await session.execute(
                select(func.count(APIKey.id)).where(APIKey.revoked_at.is_(None))
            )
        ).scalar_one()
        # Service accounts carry two independent switches: ``enabled`` is the
        # customer's toggle, ``emergency_disabled`` the platform kill switch.
        # A count of "live" accounts must respect both, and there is no
        # ``revoked_at`` on this table to lean on.
        service_accounts = (
            await session.execute(
                select(func.count(ServiceAccount.id)).where(
                    ServiceAccount.enabled.is_(True),
                    ServiceAccount.emergency_disabled.is_(False),
                )
            )
        ).scalar_one()

        mfa_coverage = (
            round(len(users_with_factor) / active_users * 100, 2) if active_users else 0.0
        )

        items = [
            item(
                self.name,
                CONTROL_AUTHENTICATION,
                "users+identity_factors",
                {
                    "active_users": active_users,
                    "total_users": total_users,
                    "users_with_active_factor": len(users_with_factor),
                    "mfa_coverage_percent": mfa_coverage,
                    "tenants_requiring_mfa": tenants_requiring_mfa,
                    "live_sessions": int(live_sessions),
                },
            ),
            item(
                self.name,
                CONTROL_PROVISIONING,
                "sso+scim",
                {
                    "sso_connections": len(sso_connections),
                    "sso_status_counts": {
                        status: list(sso_connections).count(status) for status in set(sso_connections)
                    },
                    "active_scim_credentials": int(scim_credentials),
                    "active_api_keys": int(active_api_keys),
                    "service_accounts": int(service_accounts),
                },
            ),
            item(
                self.name,
                CONTROL_RBAC,
                "users.roles",
                {
                    "role_counts": dict(sorted(role_counts.items())),
                    "administrators": len(admin_ids),
                    "dormant_accounts": len(dormant),
                    "dormant_window_days": DORMANT_DAYS,
                },
            ),
        ]

        findings: list[str] = []
        if admins_without_factor:
            findings.append(
                f"{len(admins_without_factor)} active administrator(s) have no active second factor"
            )
        if dormant:
            findings.append(
                f"{len(dormant)} active account(s) have no session activity in {DORMANT_DAYS} days"
            )

        return self._result(
            configured=True,
            items=items,
            findings=findings,
        )


__all__ = ["DORMANT_DAYS", "AccessCollector"]
