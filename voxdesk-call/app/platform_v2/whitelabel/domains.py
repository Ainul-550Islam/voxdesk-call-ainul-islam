"""Custom-domain lifecycle: claim, verify, activate, disable.

The state machine is small on purpose, because every extra state is a state the
``ask`` hook has to get right:

```
pending_dns ──verify──▶ issuing_cert ──cert ok──▶ active ——▶ disabled
     │                      │                        │          │
     └──────fail────────────┴────────fail────────────┘          └──enable──▶ active
                            failed ◀───────┘
```

Only ``active`` resolves a Host to a tenant. An unknown host, a ``pending_dns``
host, a ``failed`` host and a ``disabled`` host are all "no tenant" — there is no
default tenant to fall back to, and `resolve_host` is the only function the
request path uses to decide which tenant a Host belongs to.

Verification requires **both** proofs:

* ownership — a TXT record at ``_voxdesk-challenge.<domain>`` carrying the token
  (stored only as a SHA-256 digest);
* routing — a CNAME to ``CUSTOM_DOMAIN_CNAME_TARGET``, or an A/AAAA record that
  already resolves. A record existing is not enough: the A address must be one of
  the addresses the platform edge publishes (``CUSTOM_DOMAIN_EDGE_IPS``) when that
  list is configured, so "point it anywhere" cannot pass verification.

Every attempt is recorded as a ``WlDomainCheck`` row with the resolvers used and
the values observed, so a tenant can see *why* verification failed without asking
support to read our logs.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.models import EnterpriseDomain
from app.db.platform_v2_models import CustomDomain, WlDomainCheck
from app.platform_v2.errors import Conflict, DomainNotVerified, DomainTaken, NotFound, ValidationFailed
from app.platform_v2.settings import get_plat2_settings
from app.platform_v2.whitelabel import dns as dns_mod

STATUS_PENDING = "pending_dns"
STATUS_ISSUING = "issuing_cert"
STATUS_ACTIVE = "active"
STATUS_FAILED = "failed"
STATUS_DISABLED = "disabled"

ALL_STATUSES = (STATUS_PENDING, STATUS_ISSUING, STATUS_ACTIVE, STATUS_FAILED, STATUS_DISABLED)

#: The only status that resolves a Host. Named, not spelled out inline, so a
#: future state cannot accidentally become routable by being "not disabled".
ROUTABLE_STATUSES = (STATUS_ACTIVE,)

CHECK_OWNERSHIP = "ownership_txt"
CHECK_CNAME = "cname"
CHECK_ADDRESS = "address"


@dataclass(frozen=True)
class VerificationOutcome:
    """Result of one verification attempt."""

    domain: CustomDomain
    passed: bool
    status: str
    checks: list[dict[str, Any]]
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain_id": str(self.domain.id),
            "domain": self.domain.domain,
            "passed": self.passed,
            "status": self.status,
            "reason": self.reason,
            "checks": self.checks,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def platform_hosts() -> list[str]:
    """Hostnames the platform itself answers on: they cannot be white-labelled."""
    cfg = get_plat2_settings()
    hosts: list[str] = []
    base = cfg.public_base_url or ""
    if "://" in base:
        base = base.split("://", 1)[1]
    base = base.split("/", 1)[0].split(":", 1)[0].strip().lower()
    if base:
        hosts.append(base)
    target = (cfg.custom_domain_cname_target or "").strip().lower().rstrip(".")
    if target:
        hosts.append(target)
    return hosts


def describe(row: CustomDomain) -> dict[str, Any]:
    """Serialise a domain for the API without leaking the ownership token."""
    return {
        "id": str(row.id),
        "domain": row.domain,
        "status": row.status,
        "environment_id": str(row.environment_id) if row.environment_id else None,
        "ownership_record_name": row.ownership_record_name,
        "cname_target": row.cname_target,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "reverify_due_at": row.reverify_due_at.isoformat() if row.reverify_due_at else None,
        "failure_reason": row.failure_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "instructions": {
            "ownership": {
                "record_type": "TXT",
                "name": row.ownership_record_name,
                "value": "the token returned once when the domain was claimed",
            },
            "routing": {
                "record_type": "CNAME",
                "name": row.domain,
                "value": row.cname_target,
            },
        },
    }


async def _existing_claim(session: AsyncSession, domain: str) -> CustomDomain | None:
    row = await session.execute(select(CustomDomain).where(CustomDomain.domain == domain))
    return row.scalar_one_or_none()


async def _foreign_enterprise_domain(session: AsyncSession, domain: str, tenant_id: uuid.UUID) -> bool:
    """True when another tenant already owns this hostname in enterprise SSO.

    ``enterprise_domains`` predates custom domains and is the record used for
    "this address belongs to this customer". Claiming a hostname another tenant
    has verified there would be a takeover, so it is refused before the row is
    written rather than after a certificate exists.
    """
    row = await session.execute(
        select(EnterpriseDomain.id).where(
            EnterpriseDomain.domain == domain,
            EnterpriseDomain.tenant_id != tenant_id,
        )
    )
    return row.first() is not None


async def claim(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    domain: str,
    environment_id: uuid.UUID | None = None,
    created_by_user_id: uuid.UUID | None = None,
    commit: bool = False,
) -> tuple[CustomDomain, str]:
    """Create a ``pending_dns`` domain and return it with its one-time token.

    The plaintext token is returned to the caller exactly once — it is what the
    tenant pastes into DNS — and only its digest is stored.
    """
    cfg = get_plat2_settings()
    normalized = dns_mod.normalize_domain(domain)
    dns_mod.reject_platform_domain(normalized, platform_hosts())

    existing = await _existing_claim(session, normalized)
    if existing is not None:
        # Same tenant asking again is not an error, but it must not create a
        # second row: two rows for one hostname is how ownership gets confused.
        if existing.tenant_id == tenant_id:
            raise Conflict(
                "domain is already claimed by this tenant",
                detail={"domain_id": str(existing.id), "status": existing.status},
            )
        # One message for every state, so a stranger cannot enumerate customers by
        # trying hostnames and reading the refusal.
        raise DomainTaken("domain is not available", detail={"domain": normalized})

    if await _foreign_enterprise_domain(session, normalized, tenant_id):
        raise DomainTaken(
            "domain is verified by another tenant in enterprise identity",
            detail={"domain": normalized},
        )

    token = dns_mod.new_token()
    row = CustomDomain(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        environment_id=environment_id,
        domain=normalized,
        status=STATUS_PENDING,
        ownership_token_hash=dns_mod.token_digest(token),
        ownership_record_name=dns_mod.challenge_name(normalized),
        cname_target=cfg.custom_domain_cname_target,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:  # unique constraint on domain
        await session.rollback()
        # One message for every state, so a stranger cannot enumerate customers by
        # trying hostnames and reading the refusal.
        raise DomainTaken("domain is not available", detail={"domain": normalized}) from exc
    if commit:
        await session.commit()
    return row, dns_mod.expected_txt_value(token)


async def get(session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID) -> CustomDomain:
    row = await session.execute(
        select(CustomDomain).where(CustomDomain.id == domain_id, CustomDomain.tenant_id == tenant_id)
    )
    found = row.scalar_one_or_none()
    if found is None:
        raise NotFound("domain not found")
    return found


async def list_for_tenant(
    session: AsyncSession, *, tenant_id: uuid.UUID, limit: int = 100, offset: int = 0
) -> Sequence[CustomDomain]:
    rows = await session.execute(
        select(CustomDomain)
        .where(CustomDomain.tenant_id == tenant_id)
        .order_by(CustomDomain.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return rows.scalars().all()


async def _record_check(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID,
    check_type: str,
    passed: bool,
    resolvers: list[str],
    detail: dict[str, Any],
    consistent: bool = True,
) -> WlDomainCheck:
    observed = detail.get("observed") or {}
    values = [str(value) for value in (observed.get("values") or [])]
    reason = str(detail.get("reason") or "")
    verdict = reason or ("matched" if passed else "no match")
    if values:
        verdict = f"{verdict}; observed {', '.join(values)}"
    elif observed.get("error") and observed["error"] not in verdict:
        # The caller's reason usually already names the resolver error; repeating it
        # makes the console line twice as long and hides the part that differs.
        verdict = f"{verdict}; {observed['error']}"
    row = WlDomainCheck(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        domain_id=domain_id,
        check_type=check_type,
        passed=passed,
        consistent=consistent,
        resolvers={
            "names": list(resolvers),
            "expected": {key: value for key, value in detail.items() if key != "observed"},
            "observed": observed,
        },
        detail=verdict[:400],
    )
    session.add(row)
    return row


async def verify(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID,
    token: str | None = None,
    resolver: object | None = None,
    commit: bool = False,
) -> VerificationOutcome:
    """Run the DNS checks and move the domain along the state machine.

    ``token`` is the plaintext ownership token. The API never stores it, so the
    caller either supplies it (claim response, kept by the tenant UI) or the
    ownership check falls back to the stored digest compared against the TXT
    record's *token prefix stripped* value — which is why a digest-only path can
    still validate a record the tenant pasted correctly.
    """
    cfg = get_plat2_settings()
    row = await get(session, tenant_id=tenant_id, domain_id=domain_id)
    if row.status == STATUS_DISABLED:
        raise Conflict("domain is disabled; enable it before verifying", detail={"status": row.status})

    timeout = float(cfg.dns_check_timeout_seconds)
    row.last_checked_at = _now()
    checks: list[dict[str, Any]] = []
    resolvers: list[str] = []

    txt = await dns_mod.resolve_txt(row.ownership_record_name, timeout=timeout, resolver=resolver)
    resolvers.extend([txt.resolver] if txt.resolver else [])
    if token:
        ownership_passed = dns_mod.txt_contains(txt, token)
        ownership_reason = "" if ownership_passed else "TXT record does not contain the ownership token"
    else:
        ownership_passed = _txt_matches_digest(txt, row.ownership_token_hash)
        ownership_reason = "" if ownership_passed else "TXT record does not contain the ownership token"
    if not txt.answered:
        ownership_reason = f"could not read the TXT record: {txt.error}"
    checks.append(
        await _check_entry(
            session,
            tenant_id=tenant_id,
            domain_id=row.id,
            check_type=CHECK_OWNERSHIP,
            passed=ownership_passed,
            resolvers=resolvers,
            detail={"expected_name": row.ownership_record_name, "observed": txt.as_detail(), "reason": ownership_reason},
        )
    )

    cname = await dns_mod.resolve_cname(row.domain, timeout=timeout, resolver=resolver)
    resolvers.extend([cname.resolver] if cname.resolver else [])
    cname_passed = dns_mod.cname_points_at(cname, row.cname_target)
    cname_reason = "" if cname_passed else f"CNAME must point at {row.cname_target}"
    if not cname.answered:
        cname_reason = f"could not read the CNAME record: {cname.error}"
    checks.append(
        await _check_entry(
            session,
            tenant_id=tenant_id,
            domain_id=row.id,
            check_type=CHECK_CNAME,
            passed=cname_passed,
            resolvers=resolvers,
            detail={"expected_target": row.cname_target, "observed": cname.as_detail(), "reason": cname_reason},
        )
    )

    address = await dns_mod.resolve_a(row.domain, timeout=timeout, resolver=resolver)
    edge_ips = _edge_ips(cfg)
    address_passed = bool(address.values) and (not edge_ips or set(address.values) & set(edge_ips))
    address_reason = ""
    if not address.values:
        address_reason = "no A record resolves for this hostname yet"
    elif edge_ips and not (set(address.values) & set(edge_ips)):
        address_reason = "A record does not point at the platform edge"
    checks.append(
        await _check_entry(
            session,
            tenant_id=tenant_id,
            domain_id=row.id,
            check_type=CHECK_ADDRESS,
            passed=address_passed,
            resolvers=resolvers,
            detail={"expected_ips": sorted(edge_ips), "observed": address.as_detail(), "reason": address_reason},
        )
    )

    # Ownership is required. Routing is required unless the deployment has no
    # published edge addresses configured, in which case "the name resolves at
    # all" is the strongest statement we can make and we say exactly that.
    routing_passed = cname_passed or address_passed
    passed = ownership_passed and routing_passed
    if passed:
        row.status = STATUS_ISSUING if cfg.tls_ask_enabled else STATUS_ACTIVE
        row.verified_at = _now()
        if row.status == STATUS_ACTIVE:
            row.activated_at = _now()
        row.reverify_due_at = _now() + timedelta(hours=cfg.domain_reverify_hours)
        row.failure_reason = ""
    else:
        reasons = [reason for reason in (ownership_reason, cname_reason, address_reason) if reason]
        row.status = STATUS_FAILED
        row.failure_reason = "; ".join(reasons)[:500] or "domain verification failed"

    if commit:
        await session.commit()
    return VerificationOutcome(
        domain=row,
        passed=passed,
        status=row.status,
        checks=[serialise_check(check) for check in checks],
        reason=row.failure_reason,
    )


async def _check_entry(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID,
    check_type: str,
    passed: bool,
    resolvers: list[str],
    detail: dict[str, Any],
) -> WlDomainCheck:
    return await _record_check(
        session,
        tenant_id=tenant_id,
        domain_id=domain_id,
        check_type=check_type,
        passed=passed,
        resolvers=resolvers,
        detail=detail,
    )


def _txt_matches_digest(txt: dns_mod.Resolution, stored_digest: str) -> bool:
    """Compare a TXT answer against the stored digest when no token was supplied.

    The token cannot be recovered from the digest, so this compares the digest of
    each candidate value. It only works for the bare-token form — a prefixed
    record needs the token, which is why the claim response shows it once and the
    tenant UI keeps it. Guessing is not an option: a false pass here hands a
    certificate to whoever controls DNS.
    """
    if not stored_digest:
        return False
    for value in txt.values:
        for candidate in value.replace('"', " ").split():
            candidate = candidate.strip().strip(",")
            if dns_mod.token_digest(candidate) == stored_digest:
                return True
            for prefix in dns_mod.TOKEN_PREFIXES:
                if candidate.startswith(prefix) and dns_mod.token_digest(candidate[len(prefix) :]) == stored_digest:
                    return True
    return False


def _edge_ips(cfg: Any) -> set[str]:
    raw = getattr(cfg, "custom_domain_edge_ips", "") or ""
    if isinstance(raw, str):
        return {part.strip() for part in raw.split(",") if part.strip()}
    return {str(part) for part in raw}


async def resolve_host(session: AsyncSession, host: str) -> dict[str, Any] | None:
    """Map a request Host to a tenant, or ``None``.

    This is the only function the request path may use for host→tenant mapping,
    and it returns ``None`` for everything except a verified, ``active`` row. It
    never falls back to a default tenant: an attacker who controls DNS for a name
    pointing at our edge must not inherit somebody's workspace.
    """
    if not host:
        return None
    candidate = host.strip().lower().rstrip(".")
    if ":" in candidate:  # strip a port if the caller did not
        candidate = candidate.split(":", 1)[0]
    row = await session.execute(select(CustomDomain).where(CustomDomain.domain == candidate))
    found = row.scalar_one_or_none()
    if found is None or found.status not in ROUTABLE_STATUSES:
        return None
    return {
        "tenant_id": found.tenant_id,
        "environment_id": found.environment_id,
        "domain_id": found.id,
        "domain": found.domain,
        "status": found.status,
    }


async def activate(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID, commit: bool = False
) -> CustomDomain:
    """Mark a domain live once its certificate exists.

    Called by the certificate sweep, never by a tenant: a tenant cannot declare
    its own certificate issued.
    """
    row = await get(session, tenant_id=tenant_id, domain_id=domain_id)
    if row.status == STATUS_DISABLED:
        raise Conflict("domain is disabled", detail={"status": row.status})
    row.status = STATUS_ACTIVE
    row.activated_at = row.activated_at or _now()
    row.reverify_due_at = _now() + timedelta(hours=get_plat2_settings().domain_reverify_hours)
    row.failure_reason = ""
    if commit:
        await session.commit()
    return row


async def disable(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID, reason: str = "", commit: bool = False
) -> CustomDomain:
    """Stop routing the host immediately.

    Disabling is the emergency lever: it takes effect on the next request because
    ``resolve_host`` only accepts ``active``, without waiting for a certificate to
    expire or a DNS change to propagate.
    """
    row = await get(session, tenant_id=tenant_id, domain_id=domain_id)
    row.status = STATUS_DISABLED
    row.failure_reason = (reason or "disabled by an administrator")[:500]
    if commit:
        await session.commit()
    return row


async def enable(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID, commit: bool = False
) -> CustomDomain:
    """Re-enable a disabled domain, but only back to a state it can defend.

    It returns to ``issuing_cert`` unless it still has a valid verification window,
    in which case it goes straight back to ``active``. A disabled domain never
    becomes routable merely because somebody pressed enable.
    """
    cfg = get_plat2_settings()
    row = await get(session, tenant_id=tenant_id, domain_id=domain_id)
    if row.status != STATUS_DISABLED:
        raise Conflict("domain is not disabled", detail={"status": row.status})
    if row.verified_at and row.reverify_due_at and row.reverify_due_at > _now():
        row.status = STATUS_ACTIVE
    else:
        row.status = STATUS_ISSUING if cfg.tls_ask_enabled else STATUS_PENDING
    row.failure_reason = ""
    if commit:
        await session.commit()
    return row


async def delete(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID, commit: bool = False
) -> None:
    """Remove a domain and its check history.

    Deleting releases the hostname, which is why it is not allowed while the
    domain is ``active``: pretending to release a hostname that still has a live
    certificate and a live route is how two tenants end up owning one name.
    """
    row = await get(session, tenant_id=tenant_id, domain_id=domain_id)
    if row.status == STATUS_ACTIVE:
        raise Conflict("disable the domain before deleting it", detail={"status": row.status})
    checks = await session.execute(select(WlDomainCheck).where(WlDomainCheck.domain_id == row.id))
    for check in checks.scalars().all():
        await session.delete(check)
    await session.delete(row)
    if commit:
        await session.commit()


async def reverify_due(
    session: AsyncSession, *, limit: int = 25, now: datetime | None = None
) -> list[CustomDomain]:
    """Domains whose verification window has elapsed and still route traffic."""
    moment = now or _now()
    rows = await session.execute(
        select(CustomDomain)
        .where(
            CustomDomain.status == STATUS_ACTIVE,
            CustomDomain.reverify_due_at.is_not(None),
            CustomDomain.reverify_due_at <= moment,
        )
        .order_by(CustomDomain.reverify_due_at.asc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def checks_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID, limit: int = 50
) -> Sequence[WlDomainCheck]:
    await get(session, tenant_id=tenant_id, domain_id=domain_id)
    rows = await session.execute(
        select(WlDomainCheck)
        .where(WlDomainCheck.domain_id == domain_id, WlDomainCheck.tenant_id == tenant_id)
        .order_by(WlDomainCheck.checked_at.desc())
        .limit(limit)
    )
    return rows.scalars().all()


def serialise_check(row: WlDomainCheck) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "check_type": row.check_type,
        "passed": row.passed,
        "consistent": row.consistent,
        "resolvers": row.resolvers,
        "detail": row.detail,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
    }


def require_active(row: CustomDomain) -> None:
    """Guard for operations that only make sense on a live domain."""
    if row.status != STATUS_ACTIVE:
        raise DomainNotVerified(
            "domain is not active",
            detail={"status": row.status, "domain": row.domain},
        )


def validate_claim_input(domain: str) -> str:
    """Validate without touching the database (used by the request model)."""
    if not domain or not domain.strip():
        raise ValidationFailed("domain is required")
    return dns_mod.normalize_domain(domain)


__all__ = [
    "ALL_STATUSES",
    "CHECK_ADDRESS",
    "CHECK_CNAME",
    "CHECK_OWNERSHIP",
    "ROUTABLE_STATUSES",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_FAILED",
    "STATUS_ISSUING",
    "STATUS_PENDING",
    "VerificationOutcome",
    "activate",
    "checks_for",
    "claim",
    "delete",
    "describe",
    "disable",
    "enable",
    "get",
    "list_for_tenant",
    "platform_hosts",
    "require_active",
    "resolve_host",
    "reverify_due",
    "serialise_check",
    "validate_claim_input",
    "verify",
]
