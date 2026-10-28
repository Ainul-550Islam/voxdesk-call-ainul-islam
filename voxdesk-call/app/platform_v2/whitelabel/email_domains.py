"""Sender domains: SPF, DKIM and DMARC checks a tenant can act on.

White-labelled transactional mail (rota changes, time-off decisions, magic links)
has to come from the tenant's own domain or it lands in spam — and if the records
are wrong it lands in spam *and* damages the tenant's domain reputation, which is
much worse than a failed send.

Three records are checked, all three must pass:

* **SPF** — a ``TXT`` record on the domain beginning ``v=spf1`` that authorises the
  platform's sending hosts and does not end in ``+all``;
* **DKIM** — a ``TXT`` record at ``<selector>._domainkey.<domain>`` carrying
  ``v=DKIM1; k=rsa; p=<public key>``. When the deployment knows its own public key
  (``EMAIL_DKIM_PUBLIC_KEY``), the ``p=`` value must match it: a stale key from a
  previous provider looks valid and silently fails every signature check;
* **DMARC** — a ``TXT`` record at ``_dmarc.<domain>`` beginning ``v=DMARC1`` with a
  policy stricter than ``p=none``. ``p=none`` is a monitoring mode, not a
  deliverability configuration, and telling a tenant their mail is "verified" while
  it is not enforced would be a lie.

Every check is recorded per record (``wl_dns_record_checks``), because "your DNS is
wrong" is not an actionable message and "the DKIM key does not match ours" is.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import DnsRecordCheck, EmailSenderDomain
from app.platform_v2.errors import Conflict, NotFound
from app.platform_v2.settings import get_plat2_settings
from app.platform_v2.whitelabel import dns as dns_mod

STATUS_PENDING = "pending"
STATUS_VERIFIED = "verified"

#: `verified` is the only status the mailer will send from.
VERIFIED = STATUS_VERIFIED

DMARC_STRICT_POLICIES = ("quarantine", "reject")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_name_dkim(selector: str, domain: str) -> str:
    return f"{selector}._domainkey.{domain}"


def record_name_dmarc(domain: str) -> str:
    return f"_dmarc.{domain}"


def normalize_public_key(value: str) -> str:
    """DKIM public keys are base64 in DNS and whitespace-free; compare that form."""
    return "".join(value.split())


def expectations(domain: str, *, selector: str, platform_spf_include: str, public_key: str) -> dict[str, Any]:
    """The three records the tenant has to create, as the UI shows them."""
    spf_value = f"v=spf1 include:{platform_spf_include} -all" if platform_spf_include else "v=spf1 -all"
    return {
        "spf": {
            "record_type": "TXT",
            "name": domain,
            "value": spf_value,
            "purpose": "authorises the platform to send as this domain",
        },
        "dkim": {
            "record_type": "TXT",
            "name": record_name_dkim(selector, domain),
            "value": f"v=DKIM1; k=rsa; p={public_key}" if public_key else "v=DKIM1; k=rsa; p=<published by the platform>",
            "purpose": "signs each message so receivers can prove it came from us",
        },
        "dmarc": {
            "record_type": "TXT",
            "name": record_name_dmarc(domain),
            "value": "v=DMARC1; p=quarantine; rua=mailto:dmarc@<your domain>; pct=100",
            "purpose": "tells receivers what to do when SPF and DKIM both fail",
        },
    }


def parse_spf(values: list[str]) -> dict[str, Any]:
    """Find the SPF record and judge it. Multiple SPF records is itself a failure."""
    candidates = [value for value in values if value.strip().lower().startswith("v=spf1")]
    if not candidates:
        return {"present": False, "value": "", "valid": False, "reason": "no v=spf1 record found"}
    if len(candidates) > 1:
        # Two SPF records make the policy undefined per RFC 7208; receivers treat
        # it as a permanent error, so passing it would be worse than failing it.
        return {
            "present": True,
            "value": "; ".join(candidates),
            "valid": False,
            "reason": "more than one v=spf1 record makes the policy undefined",
        }
    value = candidates[0].strip()
    lowered = value.lower()
    if "+all" in lowered.replace(" ", ""):
        return {"present": True, "value": value, "valid": False, "reason": "SPF ends in +all, which authorises the whole internet"}
    if not lowered.rstrip().endswith(("-all", "~all")):
        return {"present": True, "value": value, "valid": False, "reason": "SPF must end in -all or ~all"}
    return {"present": True, "value": value, "valid": True, "reason": ""}


def parse_dkim(values: list[str], *, expected_key: str) -> dict[str, Any]:
    record = next((value for value in values if "v=dkim1" in value.lower()), "")
    if not record:
        return {"present": False, "value": "", "valid": False, "reason": "no v=DKIM1 record found"}
    lowered = record.lower()
    if "k=" not in lowered:
        return {"present": True, "value": record, "valid": False, "reason": "record does not declare a key type"}
    # Tag-value pairs: `v=DKIM1; k=rsa; p=<base64>`. Keys appear with leading
    # whitespace after the semicolons, so both halves are stripped before lookup.
    tags: dict[str, str] = {}
    for part in record.split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        tags[key.strip().lower()] = value.strip()
    published = normalize_public_key(tags.get("p", ""))
    if not published:
        # `p=` empty is a revocation, not a key.
        return {"present": True, "value": record, "valid": False, "reason": "record has an empty p= value (the key is revoked)"}
    if expected_key and published != normalize_public_key(expected_key):
        return {
            "present": True,
            "value": record,
            "valid": False,
            "reason": "published key does not match the platform signing key",
        }
    return {"present": True, "value": record, "valid": True, "reason": ""}


def parse_dmarc(values: list[str]) -> dict[str, Any]:
    record = next((value for value in values if value.strip().lower().startswith("v=dmarc1")), "")
    if not record:
        return {"present": False, "value": "", "valid": False, "reason": "no v=DMARC1 record found"}
    lowered = record.lower().replace(" ", "")
    policy = ""
    for part in lowered.split(";"):
        if part.startswith("p="):
            policy = part[2:]
    if not policy:
        return {"present": True, "value": record, "valid": False, "reason": "DMARC record has no p= policy"}
    if policy not in DMARC_STRICT_POLICIES:
        return {
            "present": True,
            "value": record,
            "valid": False,
            "reason": f"DMARC policy is p={policy}; quarantine or reject is required for this to protect the domain",
        }
    return {"present": True, "value": record, "valid": True, "reason": ""}


async def create(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    domain: str,
    from_name: str = "",
    dkim_selector: str = "",
    created_by_user_id: uuid.UUID | None = None,
    commit: bool = False,
) -> tuple[EmailSenderDomain, dict[str, Any]]:
    """Register a sender domain and return the records the tenant must publish."""
    cfg = get_plat2_settings()
    normalized = dns_mod.normalize_domain(domain)
    selector = (dkim_selector or cfg.email_dkim_selector or "voxdesk").strip().lower()
    if not selector or not selector.replace("-", "").isalnum():
        raise Conflict("dkim_selector must be alphanumeric", detail={"selector": selector})

    existing = await session.execute(
        select(EmailSenderDomain).where(
            EmailSenderDomain.tenant_id == tenant_id, EmailSenderDomain.domain == normalized
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise Conflict("sender domain is already registered", detail={"domain": normalized})

    row = EmailSenderDomain(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        domain=normalized,
        from_name=from_name.strip()[:80],
        dkim_selector=selector,
        status=STATUS_PENDING,
        records={},
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()
    instructions = expectations(
        normalized,
        selector=selector,
        platform_spf_include=cfg.email_spf_include,
        public_key=cfg.email_dkim_public_key,
    )
    row.records = instructions
    if commit:
        await session.commit()
    return row, instructions


async def get(session: AsyncSession, *, tenant_id: uuid.UUID, sender_id: uuid.UUID) -> EmailSenderDomain:
    row = await session.execute(
        select(EmailSenderDomain).where(
            EmailSenderDomain.id == sender_id, EmailSenderDomain.tenant_id == tenant_id
        )
    )
    found = row.scalar_one_or_none()
    if found is None:
        raise NotFound("sender domain not found")
    return found


async def list_for_tenant(session: AsyncSession, *, tenant_id: uuid.UUID, limit: int = 100) -> list[EmailSenderDomain]:
    rows = await session.execute(
        select(EmailSenderDomain)
        .where(EmailSenderDomain.tenant_id == tenant_id)
        .order_by(EmailSenderDomain.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def _record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    sender: EmailSenderDomain,
    record_type: str,
    name: str,
    expected: str,
    observed: list[str],
    passed: bool,
    detail: dict[str, Any],
) -> DnsRecordCheck:
    row = DnsRecordCheck(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        sender_domain_id=sender.id,
        domain_id=None,
        record_type=record_type,
        name=name[:255],
        expected=expected[:600],
        # ``observed`` is a JSON list column: keep the raw answers, not a joined string.
        observed=[str(value)[:200] for value in list(observed)[:20]],
        passed=passed,
        detail=detail,
    )
    session.add(row)
    return row


async def verify(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    sender_id: uuid.UUID,
    resolver: object | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Check SPF, DKIM and DMARC; the domain is ``verified`` only if all three pass."""
    cfg = get_plat2_settings()
    row = await get(session, tenant_id=tenant_id, sender_id=sender_id)
    timeout = float(cfg.dns_check_timeout_seconds)
    results: dict[str, Any] = {}

    spf_answer = await dns_mod.resolve_txt(row.domain, timeout=timeout, resolver=resolver)
    spf = parse_spf(list(spf_answer.values))
    if not spf_answer.answered:
        spf = {**spf, "valid": False, "reason": f"could not read TXT records: {spf_answer.error}"}
    results["spf"] = spf
    await _record(
        session,
        tenant_id=tenant_id,
        sender=row,
        record_type="TXT",
        name=row.domain,
        expected=(row.records or {}).get("spf", {}).get("value", ""),
        observed=list(spf_answer.values),
        passed=spf["valid"],
        detail={"check": "spf", **spf, "error": spf_answer.error},
    )

    dkim_name = record_name_dkim(row.dkim_selector, row.domain)
    dkim_answer = await dns_mod.resolve_txt(dkim_name, timeout=timeout, resolver=resolver)
    dkim = parse_dkim(list(dkim_answer.values), expected_key=cfg.email_dkim_public_key)
    if not dkim_answer.answered:
        dkim = {**dkim, "valid": False, "reason": f"could not read TXT records: {dkim_answer.error}"}
    results["dkim"] = dkim
    await _record(
        session,
        tenant_id=tenant_id,
        sender=row,
        record_type="TXT",
        name=dkim_name,
        expected=(row.records or {}).get("dkim", {}).get("value", ""),
        observed=list(dkim_answer.values),
        passed=dkim["valid"],
        detail={"check": "dkim", "selector": row.dkim_selector, **dkim, "error": dkim_answer.error},
    )

    dmarc_name = record_name_dmarc(row.domain)
    dmarc_answer = await dns_mod.resolve_txt(dmarc_name, timeout=timeout, resolver=resolver)
    dmarc = parse_dmarc(list(dmarc_answer.values))
    if not dmarc_answer.answered:
        dmarc = {**dmarc, "valid": False, "reason": f"could not read TXT records: {dmarc_answer.error}"}
    results["dmarc"] = dmarc
    await _record(
        session,
        tenant_id=tenant_id,
        sender=row,
        record_type="TXT",
        name=dmarc_name,
        expected=(row.records or {}).get("dmarc", {}).get("value", ""),
        observed=list(dmarc_answer.values),
        passed=dmarc["valid"],
        detail={"check": "dmarc", **dmarc, "error": dmarc_answer.error},
    )

    row.spf_verified = bool(spf["valid"])
    row.dkim_verified = bool(dkim["valid"])
    row.dmarc_verified = bool(dmarc["valid"])
    row.last_checked_at = _now()
    all_passed = row.spf_verified and row.dkim_verified and row.dmarc_verified
    row.status = STATUS_VERIFIED if all_passed else STATUS_PENDING
    if all_passed:
        row.verified_at = _now()
    if commit:
        await session.commit()
    return {
        "sender_domain_id": str(row.id),
        "domain": row.domain,
        "status": row.status,
        "verified": all_passed,
        "checks": results,
        "reasons": [value["reason"] for value in results.values() if value.get("reason")],
    }


async def records_for(session: AsyncSession, *, tenant_id: uuid.UUID, sender_id: uuid.UUID, limit: int = 50) -> list[DnsRecordCheck]:
    await get(session, tenant_id=tenant_id, sender_id=sender_id)
    rows = await session.execute(
        select(DnsRecordCheck)
        .where(DnsRecordCheck.sender_domain_id == sender_id, DnsRecordCheck.tenant_id == tenant_id)
        .order_by(DnsRecordCheck.checked_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def delete(session: AsyncSession, *, tenant_id: uuid.UUID, sender_id: uuid.UUID, commit: bool = False) -> None:
    row = await get(session, tenant_id=tenant_id, sender_id=sender_id)
    checks = await session.execute(select(DnsRecordCheck).where(DnsRecordCheck.sender_domain_id == row.id))
    for check in checks.scalars().all():
        await session.delete(check)
    await session.delete(row)
    if commit:
        await session.commit()


def verified_from_address(sender: EmailSenderDomain) -> str:
    """``from`` address for a verified sender domain, or empty when unverified.

    The mailer calls this and refuses to send when it is empty — there is no path
    that sends from an unverified domain "just for testing".
    """
    if sender.status != STATUS_VERIFIED:
        return ""
    return f"no-reply@{sender.domain}"


def describe(row: EmailSenderDomain) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "domain": row.domain,
        "from_name": row.from_name,
        "dkim_selector": row.dkim_selector,
        "status": row.status,
        "spf_verified": row.spf_verified,
        "dkim_verified": row.dkim_verified,
        "dmarc_verified": row.dmarc_verified,
        "records": row.records,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "verified_at": row.verified_at.isoformat() if row.verified_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


__all__ = [
    "DMARC_STRICT_POLICIES",
    "STATUS_PENDING",
    "STATUS_VERIFIED",
    "VERIFIED",
    "create",
    "delete",
    "describe",
    "expectations",
    "get",
    "list_for_tenant",
    "normalize_public_key",
    "parse_dkim",
    "parse_dmarc",
    "parse_spf",
    "record_name_dkim",
    "record_name_dmarc",
    "records_for",
    "verified_from_address",
    "verify",
]
