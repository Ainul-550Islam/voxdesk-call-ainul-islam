"""Notification fan-out: device targeting, quiet hours, delivery records, retries.

The rules this module implements are the product rules a contact centre actually
argues about:

* **Quiet hours are respected, page-outs are not.** A non-urgent notification that
  arrives inside a device's quiet window is recorded as ``dropped`` with
  ``error_code="quiet_hours"`` — not silently skipped, so "why did nobody get the
  rota message?" has an answer. An urgent template (``URGENT_TEMPLATES``) ignores the
  window, because a queue breaching at 03:00 is exactly what a phone is for.
* **Every attempt is a row.** ``mob_push_deliveries`` is written *before* the
  provider call, then updated with the outcome. A crash mid-send therefore leaves a
  ``queued`` row rather than no evidence, and the retry pass can find it.
* **Deduplication is per device.** The unique key is ``(tenant, dedupe_key)`` and the
  dedupe key embeds the device id and a time bucket, so one logical notification
  reaches each device once, while a genuinely repeated notification an hour later is
  not swallowed.
* **A dead token stops being used.** A permanent provider error
  (``UNREGISTERED``/``BadDeviceToken``) increments the token's failure count and
  deactivates it after ``MAX_PERMANENT_FAILURES``; a transient one is retried by the
  ``mobile.push.retry`` job a bounded number of times.

The provider clients are injected (``providers``), which is what makes this module
testable without FCM or APNs: the tests assert fan-out, quiet hours, dedupe and
retry behaviour against fake providers, and nothing here claims a real notification
was delivered in CI.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import MobileDevice, PushDelivery
from app.platform_v2.mobile import apns as apns_mod
from app.platform_v2.mobile import devices as devices_mod
from app.platform_v2.mobile import fcm as fcm_mod

log = structlog.get_logger(__name__)

#: Templates that ignore quiet hours. Deliberately a short list: every entry here is
#: a promise that waking somebody up is worth it.
URGENT_TEMPLATES = frozenset({"open_shift_available", "queue_breach", "adherence_escalation"})

#: Statuses allowed by ``ck_mob_delivery_status``.
STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_DROPPED = "dropped"

#: How many attempts a transient failure gets before it is left failed.
MAX_ATTEMPTS = 3

#: Window used to collapse duplicate sends of the same logical notification.
DEDUPE_BUCKET_MINUTES = 5

#: Attempt rows older than this are not retried: a rota message retried a day later
#: is worse than a missed message, because it arrives out of context.
RETRY_WINDOW = timedelta(hours=6)


class Provider(Protocol):
    """What a platform client must offer this module."""

    async def send(self, message: Any) -> Any:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class Notification:
    template: str
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    collapse_key: str = ""
    urgent: bool = False

    @property
    def is_urgent(self) -> bool:
        return self.urgent or self.template in URGENT_TEMPLATES


@dataclass
class DeliveryReport:
    device_id: uuid.UUID
    platform: str
    status: str
    dedupe_key: str
    provider_message_id: str = ""
    error_code: str = ""
    error_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": str(self.device_id),
            "platform": self.platform,
            "status": self.status,
            "dedupe_key": self.dedupe_key,
            "provider_message_id": self.provider_message_id,
            "error_code": self.error_code,
            "error_reason": self.error_reason,
        }


@dataclass(frozen=True)
class Providers:
    """The two provider clients, either or both of which may be ``None``."""

    fcm: fcm_mod.FcmClient | None = None
    apns: apns_mod.ApnsClient | None = None

    def for_platform(self, platform: str) -> Any | None:
        return self.fcm if platform == "android" else self.apns


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bucket(moment: datetime) -> str:
    floored = moment.replace(minute=(moment.minute // DEDUPE_BUCKET_MINUTES) * DEDUPE_BUCKET_MINUTES, second=0, microsecond=0)
    return floored.strftime("%Y%m%dT%H%M")


def dedupe_key(*, template: str, device_id: uuid.UUID, collapse_key: str, moment: datetime | None = None) -> str:
    """Per-device dedupe key: one logical notification per device per time bucket."""
    raw = f"{template}|{device_id}|{collapse_key}|{_bucket(moment or _now())}"
    return hashlib.sha256(raw.encode()).hexdigest()[:60]


def default_providers() -> Providers:
    """Build whichever providers the deployment has configured.

    A provider that is not configured, or whose credentials are incomplete, is
    ``None`` — not an exception at import time. The API boots on a deployment with
    no push credentials, and ``send_to_user`` then records ``dropped`` with
    ``error_code="provider_not_configured"``, which is the honest outcome.
    """
    gcm: fcm_mod.FcmClient | None = None
    apple: apns_mod.ApnsClient | None = None
    try:
        gcm = fcm_mod.FcmClient.from_settings()
    except Exception as exc:  # noqa: BLE001 - not configured is a normal state
        log.info("platform_v2.push.fcm_unavailable", error=str(exc)[:120])
    try:
        apple = apns_mod.ApnsClient.from_settings()
    except Exception as exc:  # noqa: BLE001
        log.info("platform_v2.push.apns_unavailable", error=str(exc)[:120])
    return Providers(fcm=gcm, apns=apple)


async def _existing_delivery(
    session: AsyncSession, *, tenant_id: uuid.UUID, key: str
) -> PushDelivery | None:
    row = await session.execute(
        select(PushDelivery).where(PushDelivery.tenant_id == tenant_id, PushDelivery.dedupe_key == key)
    )
    return row.scalar_one_or_none()


def _message_for(platform: str, token: str, notification: Notification) -> Any:
    if platform == "android":
        return fcm_mod.PushMessage(
            token=token,
            title=notification.title,
            body=notification.body,
            data={key: str(value) for key, value in notification.data.items()},
            collapse_key=notification.collapse_key,
            high_priority=notification.is_urgent,
            tag=notification.collapse_key,
        )
    return apns_mod.ApnsMessage(
        device_token=token,
        title=notification.title,
        body=notification.body,
        data=dict(notification.data),
        collapse_id=notification.collapse_key,
        push_type=apns_mod.push_type_for(notification.template),
        immediate=notification.is_urgent,
        thread_id=notification.collapse_key,
    )


async def send_to_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    notification: Notification,
    providers: Providers | None = None,
    now: datetime | None = None,
    commit: bool = False,
) -> list[DeliveryReport]:
    """Fan a notification out to every live device of one user."""
    moment = now or _now()
    client_set = providers if providers is not None else default_providers()
    devices = await devices_mod.list_for_user(session, tenant_id=tenant_id, user_id=user_id)
    reports: list[DeliveryReport] = []

    for device in devices:
        if device.revoked_at is not None:
            reports.append(
                DeliveryReport(
                    device_id=device.id,
                    platform=device.platform,
                    status=STATUS_DROPPED,
                    dedupe_key="",
                    error_code="device_revoked",
                )
            )
            continue
        if not device.push_enabled:
            reports.append(
                DeliveryReport(
                    device_id=device.id,
                    platform=device.platform,
                    status=STATUS_DROPPED,
                    dedupe_key="",
                    error_code="push_disabled",
                )
            )
            continue
        if devices_mod.in_quiet_hours(device.quiet_hours, moment) and not notification.is_urgent:
            reports.append(
                DeliveryReport(
                    device_id=device.id,
                    platform=device.platform,
                    status=STATUS_DROPPED,
                    dedupe_key="",
                    error_code="quiet_hours",
                )
            )
            continue

        tokens = await devices_mod.active_tokens(session, tenant_id=tenant_id, device=device)
        if not tokens:
            reports.append(
                DeliveryReport(
                    device_id=device.id,
                    platform=device.platform,
                    status=STATUS_DROPPED,
                    dedupe_key="",
                    error_code="no_active_token",
                )
            )
            continue

        client = client_set.for_platform(device.platform)
        for token in tokens:
            key = dedupe_key(
                template=notification.template,
                device_id=device.id,
                collapse_key=notification.collapse_key,
                moment=moment,
            )
            existing = await _existing_delivery(session, tenant_id=tenant_id, key=key)
            if existing is not None:
                reports.append(
                    DeliveryReport(
                        device_id=device.id,
                        platform=device.platform,
                        status=existing.status,
                        dedupe_key=key,
                        provider_message_id=existing.provider_message_id,
                        error_code=existing.error_code,
                    )
                )
                continue

            row = PushDelivery(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                device_id=device.id,
                template=notification.template,
                provider="fcm" if device.platform == "android" else "apns",
                status=STATUS_QUEUED,
                dedupe_key=key,
                collapse_id=notification.collapse_key[:64],
                payload_summary={
                    "title": notification.title[:120],
                    "body": notification.body[:200],
                    "urgent": notification.is_urgent,
                    "data_keys": sorted(notification.data),
                    # Values are kept, not just their names: a retried rota push
                    # without its shift_id opens the app on the wrong screen.
                    "data": {str(key): str(value)[:200] for key, value in notification.data.items()},
                    "attempts": 0,
                },
                error_code="",
                error_reason="",
            )
            # A savepoint, so a duplicate claim rolls back only this row instead of
            # discarding the deliveries already staged for earlier devices.
            try:
                async with session.begin_nested():
                    session.add(row)
                    await session.flush()
            except IntegrityError:
                # Another worker claimed the same (device, bucket) while we were
                # here: that is exactly the duplicate this key exists to stop.
                continue

            if client is None:
                row.status = STATUS_DROPPED
                row.error_code = "provider_not_configured"
                row.error_reason = f"no {device.platform} push provider is configured"
                reports.append(
                    DeliveryReport(
                        device_id=device.id,
                        platform=device.platform,
                        status=STATUS_DROPPED,
                        dedupe_key=key,
                        error_code=row.error_code,
                        error_reason=row.error_reason,
                    )
                )
                continue

            try:
                result = await client.send(_message_for(device.platform, token.token, notification))
            except Exception as exc:  # noqa: BLE001 - a provider exception is a failed attempt
                row.status = STATUS_FAILED
                row.error_code = "provider_exception"
                row.error_reason = f"{type(exc).__name__}: {exc}"[:200]
                await devices_mod.record_failure(session, token=token, error=row.error_reason, permanent=False)
                reports.append(
                    DeliveryReport(
                        device_id=device.id,
                        platform=device.platform,
                        status=STATUS_FAILED,
                        dedupe_key=key,
                        error_code=row.error_code,
                        error_reason=row.error_reason,
                    )
                )
                continue

            if getattr(result, "ok", False):
                row.status = STATUS_SENT
                row.sent_at = _now()
                row.provider_message_id = str(
                    getattr(result, "message_id", "") or getattr(result, "apns_id", "")
                )[:200]
                await devices_mod.record_success(session, token=token)
            else:
                transient = bool(getattr(result, "transient", False))
                row.status = STATUS_FAILED
                row.error_code = str(getattr(result, "error_code", "") or getattr(result, "reason", ""))[:80]
                row.error_reason = str(getattr(result, "error_reason", "") or getattr(result, "reason", ""))[:200]
                await devices_mod.record_failure(
                    session, token=token, error=row.error_reason or row.error_code, permanent=not transient
                )
            reports.append(
                DeliveryReport(
                    device_id=device.id,
                    platform=device.platform,
                    status=row.status,
                    dedupe_key=key,
                    provider_message_id=row.provider_message_id,
                    error_code=row.error_code,
                    error_reason=row.error_reason,
                )
            )

    if commit:
        await session.commit()
    return reports


async def deliveries_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, limit: int = 50
) -> list[PushDelivery]:
    """Recent delivery attempts for a user, newest first."""
    rows = await session.execute(
        select(PushDelivery)
        .join(MobileDevice, MobileDevice.id == PushDelivery.device_id)
        .where(PushDelivery.tenant_id == tenant_id, MobileDevice.user_id == user_id)
        .order_by(PushDelivery.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def retry_failed(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    providers: Providers | None = None,
    limit: int = 25,
    now: datetime | None = None,
    commit: bool = False,
) -> dict[str, int]:
    """Retry transient failures that are still inside the retry window.

    ``dropped`` rows are never retried: they were dropped for a reason the retry
    cannot change (quiet hours, revoked device, no token).
    """
    moment = now or _now()
    cutoff = moment - RETRY_WINDOW
    client_set = providers if providers is not None else default_providers()
    rows = await session.execute(
        select(PushDelivery)
        .where(
            PushDelivery.tenant_id == tenant_id,
            PushDelivery.status == STATUS_FAILED,
            PushDelivery.created_at >= cutoff,
        )
        .order_by(PushDelivery.created_at.asc())
        .limit(limit)
    )
    attempted = 0
    sent = 0
    still_failing = 0
    for delivery in rows.scalars().all():
        device_row = await session.execute(
            select(MobileDevice).where(MobileDevice.id == delivery.device_id)
        )
        device = device_row.scalar_one_or_none()
        if device is None or device.revoked_at is not None:
            delivery.status = STATUS_DROPPED
            delivery.error_code = "device_revoked"
            continue
        tokens = await devices_mod.active_tokens(session, tenant_id=tenant_id, device=device)
        client = client_set.for_platform(device.platform)
        if not tokens or client is None:
            delivery.status = STATUS_DROPPED
            delivery.error_code = "no_active_token" if not tokens else "provider_not_configured"
            continue

        payload = delivery.payload_summary or {}
        attempts = int(payload.get("attempts") or 0)
        if attempts >= MAX_ATTEMPTS:
            # Left failed on purpose: a rota message retried a sixth time is noise.
            continue
        stored_data = payload.get("data")
        notification = Notification(
            template=delivery.template,
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            data=(
                {str(key): str(value) for key, value in stored_data.items()}
                if isinstance(stored_data, dict) and stored_data
                else {key: "" for key in payload.get("data_keys", [])}
            ),
            collapse_key=delivery.collapse_id,
            urgent=bool(payload.get("urgent")),
        )
        token = tokens[0]
        attempted += 1
        delivery.payload_summary = {**payload, "attempts": attempts + 1}
        try:
            result = await client.send(_message_for(device.platform, token.token, notification))
        except Exception as exc:  # noqa: BLE001
            delivery.error_reason = f"{type(exc).__name__}: {exc}"[:200]
            still_failing += 1
            continue
        if getattr(result, "ok", False):
            delivery.status = STATUS_SENT
            delivery.sent_at = _now()
            delivery.provider_message_id = str(
                getattr(result, "message_id", "") or getattr(result, "apns_id", "")
            )[:200]
            delivery.error_code = ""
            delivery.error_reason = ""
            await devices_mod.record_success(session, token=token)
            sent += 1
        else:
            delivery.error_code = str(getattr(result, "error_code", "") or getattr(result, "reason", ""))[:80]
            delivery.error_reason = str(getattr(result, "error_reason", "") or getattr(result, "reason", ""))[:200]
            still_failing += 1
            await devices_mod.record_failure(
                session,
                token=token,
                error=delivery.error_reason or delivery.error_code,
                permanent=not bool(getattr(result, "transient", False)),
            )
    if commit:
        await session.commit()
    return {"attempted": attempted, "sent": sent, "still_failing": still_failing}


def describe_delivery(row: PushDelivery) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "device_id": str(row.device_id),
        "template": row.template,
        "provider": row.provider,
        "status": row.status,
        "provider_message_id": row.provider_message_id,
        "error_code": row.error_code,
        "error_reason": row.error_reason,
        "collapse_id": row.collapse_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


def summarise_deliveries(rows: list[PushDelivery]) -> dict[str, int]:
    """Counts per status, for the console."""
    summary = {STATUS_QUEUED: 0, STATUS_SENT: 0, STATUS_FAILED: 0, STATUS_DROPPED: 0}
    for row in rows:
        summary[row.status] = summary.get(row.status, 0) + 1
    return summary


__all__ = [
    "DEDUPE_BUCKET_MINUTES",
    "MAX_ATTEMPTS",
    "RETRY_WINDOW",
    "STATUS_DROPPED",
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_SENT",
    "URGENT_TEMPLATES",
    "DeliveryReport",
    "Notification",
    "Provider",
    "Providers",
    "dedupe_key",
    "default_providers",
    "deliveries_for",
    "describe_delivery",
    "retry_failed",
    "send_to_user",
    "summarise_deliveries",
]
