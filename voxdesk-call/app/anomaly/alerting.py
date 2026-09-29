"""Transactional anomaly alerts backed by the existing outbox dispatcher."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.context import GovernanceScope
from app.outbox.publisher import publish
from app.outbox.models import OutboxEventStatus
from app.db.models import WebhookSubscription
from .persistence import AnomalyAlertRecord, AnomalyResultRecord


async def publish_alerts(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, results: list[dict]) -> list[AnomalyAlertRecord]:
    """Create one tenant/environment-deduplicated alert per anomalous result.

    The initial state is ``pending``.  It becomes ``delivered`` only when the
    existing outbox dispatcher records a successful event delivery; this
    function never claims that an email, webhook, or external alert provider
    has sent anything.
    """
    alerts: list[AnomalyAlertRecord] = []
    for index, result in enumerate(results):
        if not result.get("anomalous") or str(result.get("severity", "")).lower() not in {"high", "critical"}:
            continue
        dedupe = f"{execution_id}:{index}:{result.get('observation_time', '')}:{result.get('metric', '')}"
        existing = await session.scalar(select(AnomalyAlertRecord).where(AnomalyAlertRecord.tenant_id == scope.tenant_id, AnomalyAlertRecord.organization_id == scope.organization_id, AnomalyAlertRecord.environment_id == scope.environment_id, AnomalyAlertRecord.dedupe_key == dedupe))
        if existing is not None:
            alerts.append(existing)
            continue
        result_row = await session.scalar(select(AnomalyResultRecord).where(AnomalyResultRecord.execution_id == execution_id, AnomalyResultRecord.result_index == index, AnomalyResultRecord.tenant_id == scope.tenant_id, AnomalyResultRecord.organization_id == scope.organization_id, AnomalyResultRecord.environment_id == scope.environment_id))
        alert = AnomalyAlertRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, result_id=result_row.id if result_row else None, dedupe_key=dedupe[:200], severity=str(result.get("severity", "info")), delivery_state="pending")
        session.add(alert)
        await session.flush()
        event, _created = await publish(session, tenant_id=scope.tenant_id, environment_id=scope.environment_id, event_type="anomaly.alert.created", aggregate_type="anomaly_alert", aggregate_id=str(alert.id), idempotency_key=f"anomaly-alert:{alert.id}", payload={"alert_id": str(alert.id), "execution_id": str(execution_id), "severity": str(result.get("severity", "info")), "metric": str(result.get("metric", "")), "delivery_state": "queued"})
        alert.outbox_event_id = event.id
        subscriptions = list((await session.scalars(select(WebhookSubscription).where(
            WebhookSubscription.tenant_id == scope.tenant_id,
            WebhookSubscription.enabled.is_(True),
            (WebhookSubscription.environment_id == scope.environment_id) | (WebhookSubscription.environment_id.is_(None)),
        ))).all())
        configured = any(not row.event_types or "anomaly.alert.created" in row.event_types for row in subscriptions)
        if not configured:
            alert.delivery_state = "suppressed"
            alert.delivery_detail = "not_configured: no enabled tenant/environment webhook subscription"
        else:
            alert.delivery_detail = "queued in transactional outbox; external delivery not yet verified"
        alerts.append(alert)
    return alerts


async def reconcile_alert_delivery(session: AsyncSession, scope: GovernanceScope, *, alert_id: uuid.UUID) -> AnomalyAlertRecord | None:
    alert = await session.scalar(select(AnomalyAlertRecord).where(AnomalyAlertRecord.id == alert_id, AnomalyAlertRecord.tenant_id == scope.tenant_id, AnomalyAlertRecord.organization_id == scope.organization_id, AnomalyAlertRecord.environment_id == scope.environment_id))
    if alert is None or alert.outbox_event_id is None or alert.delivery_state == "suppressed":
        return alert
    event = await session.get(__import__("app.outbox.models", fromlist=["OutboxEvent"]).OutboxEvent, alert.outbox_event_id)
    if event is None:
        return alert
    alert.delivery_state = {OutboxEventStatus.DELIVERED.value: "sent", OutboxEventStatus.DEAD_LETTER.value: "failed", OutboxEventStatus.CANCELLED.value: "suppressed"}.get(event.status, "queued")
    alert.delivery_detail = event.last_error or ("outbox delivered" if alert.delivery_state == "delivered" else "delivery remains pending")
    await session.flush()
    return alert
