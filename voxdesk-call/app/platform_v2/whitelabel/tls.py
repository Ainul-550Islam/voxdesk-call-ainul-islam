"""On-demand TLS: the ``ask`` decision and certificate bookkeeping.

Caddy calls ``GET /api/platform/tls/ask?domain=<host>`` for every hostname it is
asked to issue a certificate for. The answer is a *decision*, not a lookup:

* ``200`` with a body containing ``allow: true`` — the host is a verified,
  ``active`` custom domain of a tenant;
* ``403`` — everything else, including unknown hosts, ``pending_dns``, ``failed``
  and ``disabled`` domains, a request without the shared ask token, and a
  deployment with on-demand TLS switched off.

Refusals are ``403`` and never ``400``: Caddy treats any non-2xx as "do not issue"
and a 403 is also what an operator sees in the Caddy log, so the *reason* string is
carried in the body for humans while the status code stays simple.

Certificate state lives in ``wl_tls_cert_status``. We never claim a certificate is
issued because Caddy probably issued one: `sweep()` connects to the hostname,
reads the certificate that is actually being served, and only then moves the domain
to ``active``. A domain whose certificate cannot be read stays ``issuing_cert``
with the last error recorded, which is the honest state.
"""

from __future__ import annotations

import asyncio
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import CustomDomain, TlsCertStatus
from app.platform_v2.errors import NotFound, ValidationFailed
from app.platform_v2.settings import get_plat2_settings
from app.platform_v2.whitelabel import dns as dns_mod
from app.platform_v2.whitelabel import domains as domains_mod

#: Path the edge calls. Exported so the route, the tests and the Caddy file can
#: be compared instead of remembered.
ASK_PATH = "/api/platform/tls/ask"
STATUS_PATH = "/api/platform/tls/status"
ASK_TOKEN_HEADER = "X-Voxdesk-Ask-Token"

#: Certificate states. `valid` is the only one that makes a domain routable.
CERT_VALID = "valid"
CERT_PENDING = "pending"
CERT_EXPIRED = "expired"
CERT_ERROR = "error"

#: How long before expiry we start complaining. 21 days is comfortably inside
#: Let's Encrypt's 30-day renewal window, so a warning here means renewal is
#: actually late rather than merely scheduled.
RENEWAL_WARNING_DAYS = 21


@dataclass(frozen=True)
class AskDecision:
    """What the edge is told, and why."""

    domain: str
    allow: bool
    status_code: int
    reason: str
    tenant_id: uuid.UUID | None = None
    domain_status: str = ""

    def body(self) -> dict[str, Any]:
        return {
            "allow": self.allow,
            "domain": self.domain,
            "reason": self.reason,
            "status": self.domain_status,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def refuse(domain: str, reason: str) -> AskDecision:
    return AskDecision(domain=domain, allow=False, status_code=403, reason=reason)


def allow(domain: str, tenant_id: uuid.UUID, domain_status: str) -> AskDecision:
    return AskDecision(
        domain=domain,
        allow=True,
        status_code=200,
        reason="verified custom domain",
        tenant_id=tenant_id,
        domain_status=domain_status,
    )


async def decide(
    session: AsyncSession,
    *,
    domain: str,
    provided_token: str | None,
) -> AskDecision:
    """The full ask decision, in the order the checks matter.

    Token first: an unauthenticated caller must not learn whether a hostname
    exists, so a bad token is refused before any query runs.
    """
    cfg = get_plat2_settings()
    if not cfg.tls_ask_enabled:
        return refuse(domain, "on-demand TLS is disabled on this deployment")
    if not dns_mod.tls_ask_authorised(provided_token, cfg.tls_ask_token):
        return refuse(domain, "missing or invalid ask token")

    try:
        normalized = dns_mod.normalize_domain(domain)
    except ValidationFailed as exc:
        return refuse(domain, f"not a valid hostname: {exc}")

    row = await session.execute(select(CustomDomain).where(CustomDomain.domain == normalized))
    found = row.scalar_one_or_none()
    if found is None:
        # Deliberately indistinguishable from "known but not active" in wording:
        # the caller has the token, so this is an operator, not an attacker.
        return refuse(normalized, "unknown hostname")
    if found.status != domains_mod.STATUS_ACTIVE:
        return refuse(normalized, f"domain status is {found.status}")
    return allow(normalized, found.tenant_id, found.status)


@dataclass(frozen=True)
class CertificateInfo:
    """What a TLS handshake actually returned."""

    host: str
    state: str
    issuer: str = ""
    serial: str = ""
    not_before: datetime | None = None
    not_after: datetime | None = None
    error: str = ""

    @property
    def days_to_expiry(self) -> int | None:
        if self.not_after is None:
            return None
        # A timestamp read back from a backend that drops tzinfo (SQLite) must not be
        # silently compared against an aware "now": that raises, and a console page
        # that 500s on a certificate is worse than one showing no expiry.
        expiry = self.not_after if self.not_after.tzinfo else self.not_after.replace(tzinfo=timezone.utc)
        return (expiry - _now()).days

    @property
    def renewal_due(self) -> bool:
        days = self.days_to_expiry
        return days is not None and days <= RENEWAL_WARNING_DAYS

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "state": self.state,
            "issuer": self.issuer,
            "serial": self.serial,
            "not_before": self.not_before.isoformat() if self.not_before else None,
            "not_after": self.not_after.isoformat() if self.not_after else None,
            "days_to_expiry": self.days_to_expiry,
            "renewal_due": self.renewal_due,
            "error": self.error,
        }


async def _read_certificate(host: str, *, port: int, timeout: float) -> tuple[bytes, str]:
    """Fetch the DER certificate the host serves.

    Verification is on: if the certificate does not validate we want to know that
    here, not after a browser tells the tenant. The handshake failure text is what
    ends up in ``last_error``.
    """
    context = ssl.create_default_context()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=context, server_hostname=host),
        timeout=timeout,
    )
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else b""
        cipher = ssl_object.cipher()[0] if ssl_object else ""
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - closing a broken socket is not an error
            pass
    return certificate, cipher


def _parse(certificate: bytes) -> tuple[str, str, datetime, datetime]:
    from cryptography import x509

    parsed = x509.load_der_x509_certificate(certificate)
    issuer = parsed.issuer.rfc4514_string()
    serial = format(parsed.serial_number, "x")
    not_before = getattr(parsed, "not_valid_before_utc", None) or parsed.not_valid_before.replace(
        tzinfo=timezone.utc
    )
    not_after = getattr(parsed, "not_valid_after_utc", None) or parsed.not_valid_after.replace(
        tzinfo=timezone.utc
    )
    return issuer, serial, not_before, not_after


async def inspect(
    host: str,
    *,
    port: int = 443,
    timeout: float = 5.0,
    reader: Any | None = None,
) -> CertificateInfo:
    """Read the certificate ``host`` is serving.

    ``reader`` is injectable so the protocol logic is unit-testable without a
    network: it must be an awaitable returning ``(certificate_der, cipher)``.
    """
    normalized = dns_mod.normalize_domain(host)
    try:
        if reader is not None:
            certificate, _ = await reader(normalized, port, timeout)
        else:
            certificate, _ = await _read_certificate(normalized, port=port, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - any failure is "no usable certificate"
        return CertificateInfo(host=normalized, state=CERT_ERROR, error=f"{type(exc).__name__}: {exc}")
    if not certificate:
        return CertificateInfo(host=normalized, state=CERT_ERROR, error="no certificate presented")
    try:
        issuer, serial, not_before, not_after = _parse(certificate)
    except Exception as exc:  # noqa: BLE001 - an unparseable certificate is an error state
        return CertificateInfo(host=normalized, state=CERT_ERROR, error=f"could not parse certificate: {exc}")

    now = _now()
    if not_after <= now:
        state = CERT_EXPIRED
    elif not_before > now:
        state = CERT_PENDING
    else:
        state = CERT_VALID
    return CertificateInfo(
        host=normalized,
        state=state,
        issuer=issuer,
        serial=serial,
        not_before=not_before,
        not_after=not_after,
    )


async def record(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID, info: CertificateInfo
) -> TlsCertStatus:
    """Upsert the certificate row for a domain. One row per domain."""
    row = await session.execute(select(TlsCertStatus).where(TlsCertStatus.domain_id == domain_id))
    status = row.scalar_one_or_none()
    if status is None:
        status = TlsCertStatus(id=uuid.uuid4(), tenant_id=tenant_id, domain_id=domain_id)
        session.add(status)
    status.status = info.state
    status.issuer = info.issuer[:255]
    status.serial = info.serial[:128]
    status.not_before = info.not_before
    status.not_after = info.not_after
    status.last_error = info.error[:500]
    status.updated_at = _now()
    await session.flush()
    return status


async def status_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, domain_id: uuid.UUID
) -> dict[str, Any]:
    """Certificate state for the tenant UI, including renewal pressure."""
    row = await session.execute(
        select(TlsCertStatus).where(TlsCertStatus.domain_id == domain_id, TlsCertStatus.tenant_id == tenant_id)
    )
    status = row.scalar_one_or_none()
    if status is not None:
        info = CertificateInfo(
            host="",
            state=status.status,
            issuer=status.issuer,
            serial=status.serial,
            not_before=status.not_before,
            not_after=status.not_after,
            error=status.last_error,
        )
        payload = info.as_dict()
        payload.pop("host", None)
        payload["checked_at"] = status.updated_at.isoformat() if status.updated_at else None
        return payload

    # No row yet: report the honest unknown rather than a hopeful "pending".
    domain = await session.execute(
        select(CustomDomain).where(CustomDomain.id == domain_id, CustomDomain.tenant_id == tenant_id)
    )
    found = domain.scalar_one_or_none()
    if found is None:
        raise NotFound("domain not found")
    return {
        "state": CERT_PENDING if found.status == domains_mod.STATUS_ISSUING else found.status,
        "issuer": "",
        "serial": "",
        "not_before": None,
        "not_after": None,
        "days_to_expiry": None,
        "renewal_due": False,
        "error": "",
        "checked_at": None,
    }


async def sweep(
    session: AsyncSession,
    *,
    limit: int = 25,
    reader: Any | None = None,
    timeout: float | None = None,
    commit: bool = False,
) -> list[dict[str, Any]]:
    """Check every domain waiting for a certificate.

    A domain becomes ``active`` only when a valid certificate is actually being
    served. This is the function that makes the ``issuing_cert`` state finite
    instead of a place domains go to be forgotten.
    """
    cfg = get_plat2_settings()
    wait = float(timeout if timeout is not None else cfg.dns_check_timeout_seconds)
    rows = await session.execute(
        select(CustomDomain)
        .where(CustomDomain.status == domains_mod.STATUS_ISSUING)
        .order_by(CustomDomain.last_checked_at.asc().nulls_first())
        .limit(limit)
    )
    results: list[dict[str, Any]] = []
    for domain in rows.scalars().all():
        info = await inspect(domain.domain, timeout=wait, reader=reader)
        await record(session, tenant_id=domain.tenant_id, domain_id=domain.id, info=info)
        activated = False
        if info.state == CERT_VALID:
            await domains_mod.activate(session, tenant_id=domain.tenant_id, domain_id=domain.id)
            activated = True
        elif info.state == CERT_EXPIRED:
            domain.failure_reason = "certificate expired before activation"
        results.append(
            {
                "domain": domain.domain,
                "domain_id": str(domain.id),
                "activated": activated,
                **info.as_dict(),
            }
        )
    if commit:
        await session.commit()
    return results


async def refresh_certificate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID,
    reader: Any | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """On-demand re-read of one domain's certificate, for the tenant UI."""
    domain = await domains_mod.get(session, tenant_id=tenant_id, domain_id=domain_id)
    info = await inspect(domain.domain, reader=reader)
    await record(session, tenant_id=tenant_id, domain_id=domain_id, info=info)
    activated = False
    if info.state == CERT_VALID and domain.status == domains_mod.STATUS_ISSUING:
        await domains_mod.activate(session, tenant_id=tenant_id, domain_id=domain_id)
        activated = True
    if commit:
        await session.commit()
    payload = info.as_dict()
    payload["activated"] = activated
    payload["domain_status"] = domain.status
    return payload


__all__ = [
    "ASK_PATH",
    "ASK_TOKEN_HEADER",
    "CERT_ERROR",
    "CERT_EXPIRED",
    "CERT_PENDING",
    "CERT_VALID",
    "RENEWAL_WARNING_DAYS",
    "STATUS_PATH",
    "AskDecision",
    "CertificateInfo",
    "allow",
    "decide",
    "inspect",
    "record",
    "refuse",
    "refresh_certificate",
    "status_for",
    "sweep",
]
