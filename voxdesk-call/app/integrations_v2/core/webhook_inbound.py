"""Vendor-authentic inbound webhook HMAC verification, replay protection, and deduplicated queuing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import InboundWebhookEvent, IntegrationConnection
from app.integrations_v2.core.connection_store import open_secret_bundle
from app.integrations_v2.core.errors import WebhookSignatureError
from app.integrations_v2.settings import get_int2_settings
from app.outbox.publisher import publish as publish_outbox_event

DEFAULT_REPLAY_TOLERANCE_SECONDS = 300
OUTBOX_EVENT_TYPE_INT2_WEBHOOK = "integrations_v2.webhook_inbound"


@dataclass(frozen=True)
class WebhookReceiptResult:
    status: str  # "accepted" | "duplicate" | "challenge"
    event_id: str
    challenge_response: dict[str, Any] | None = None
    challenge_plain_text: str | None = None


def _check_timestamp_fresh(
    ts_seconds: int,
    *,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
    provider: str = "",
) -> None:
    ref = int(time.time()) if now is None else int(now)
    if abs(ref - ts_seconds) > tolerance_seconds:
        raise WebhookSignatureError(
            f"{provider} webhook timestamp outside {tolerance_seconds}s replay window",
            provider=provider,
        )


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp_header: str,
    signature_header: str,
    raw_body: bytes,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
) -> None:
    """Verify Slack ``X-Slack-Signature: v0=<hex>`` over ``v0:{timestamp}:{raw_body}``."""
    if not signing_secret:
        raise WebhookSignatureError("Slack signing secret is not configured", provider="slack")
    if not timestamp_header or not signature_header:
        raise WebhookSignatureError("Missing Slack signature headers", provider="slack")
    try:
        ts_int = int(timestamp_header.strip())
    except ValueError as exc:
        raise WebhookSignatureError("Malformed X-Slack-Request-Timestamp", provider="slack") from exc

    _check_timestamp_fresh(
        ts_int, now=now, tolerance_seconds=tolerance_seconds, provider="slack"
    )
    basestring = f"v0:{ts_int}:".encode("ascii") + raw_body
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise WebhookSignatureError("Slack HMAC-SHA256 signature mismatch", provider="slack")


def verify_zoom_signature(
    *,
    secret_token: str,
    timestamp_header: str,
    signature_header: str,
    raw_body: bytes,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
) -> None:
    """Verify Zoom ``x-zm-signature: v0=<hex>`` over ``v0:{x-zm-request-timestamp}:{raw_body}``."""
    if not secret_token:
        raise WebhookSignatureError("Zoom webhook secret token is not configured", provider="zoom")
    if not timestamp_header or not signature_header:
        raise WebhookSignatureError("Missing Zoom signature headers", provider="zoom")
    try:
        ts_int = int(timestamp_header.strip())
    except ValueError as exc:
        raise WebhookSignatureError("Malformed x-zm-request-timestamp", provider="zoom") from exc

    _check_timestamp_fresh(
        ts_int, now=now, tolerance_seconds=tolerance_seconds, provider="zoom"
    )
    message = f"v0:{ts_int}:".encode("ascii") + raw_body
    expected = "v0=" + hmac.new(
        secret_token.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header.strip()):
        raise WebhookSignatureError("Zoom HMAC-SHA256 signature mismatch", provider="zoom")


def build_zoom_url_validation_response(
    *, secret_token: str, plain_token: str
) -> dict[str, str]:
    """Build Zoom ``endpoint.url_validation`` challenge response ``{plainToken, encryptedToken}``."""
    if not secret_token or not plain_token:
        raise WebhookSignatureError("Missing Zoom challenge parameters", provider="zoom")
    encrypted = hmac.new(
        secret_token.encode("utf-8"),
        plain_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": encrypted}


def verify_zendesk_signature(
    *,
    signing_secret: str,
    timestamp_header: str,
    signature_header: str,
    raw_body: bytes,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
) -> None:
    """Verify Zendesk ``X-Zendesk-Webhook-Signature`` (base64 SHA-256 HMAC over ``timestamp + raw_body``)."""
    if not signing_secret:
        raise WebhookSignatureError("Zendesk webhook secret is not configured", provider="zendesk")
    if not timestamp_header or not signature_header:
        raise WebhookSignatureError("Missing Zendesk webhook signature headers", provider="zendesk")

    ts_raw = timestamp_header.strip()
    try:
        if ts_raw.isdigit():
            ts_int = int(ts_raw)
        else:
            iso = ts_raw[:-1] + "+00:00" if ts_raw.endswith("Z") else ts_raw
            ts_int = int(datetime.fromisoformat(iso).timestamp())
    except Exception as exc:
        raise WebhookSignatureError(
            "Malformed X-Zendesk-Webhook-Signature-Timestamp", provider="zendesk"
        ) from exc

    _check_timestamp_fresh(
        ts_int, now=now, tolerance_seconds=tolerance_seconds, provider="zendesk"
    )
    signed_bytes = ts_raw.encode("utf-8") + raw_body
    digest = hmac.new(signing_secret.encode("utf-8"), signed_bytes, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(digest).decode("ascii")
    if not hmac.compare_digest(expected_b64, signature_header.strip()):
        raise WebhookSignatureError("Zendesk webhook signature mismatch", provider="zendesk")


def verify_teams_client_state(*, expected_secret: str, payload: dict[str, Any]) -> None:
    """Verify Microsoft Graph change-notification ``value[].clientState``."""
    if not expected_secret:
        raise WebhookSignatureError("Teams subscription clientState secret not configured", provider="teams")
    items = payload.get("value")
    if not isinstance(items, list) or not items:
        raise WebhookSignatureError("Teams Graph notification missing value[] items", provider="teams")
    for entry in items:
        state = str((entry or {}).get("clientState") or "")
        if not hmac.compare_digest(expected_secret, state):
            raise WebhookSignatureError("Teams Graph clientState mismatch", provider="teams")


def verify_generic_hmac_sha256(
    *,
    secret: str,
    timestamp_header: str,
    signature_header: str,
    raw_body: bytes,
    provider: str,
    now: int | None = None,
    tolerance_seconds: int = DEFAULT_REPLAY_TOLERANCE_SECONDS,
) -> None:
    """Verify ``X-Webhook-Signature`` or vendor HMAC-SHA256 hex/base64 over ``{ts}.{body}`` or ``{body}``."""
    if not secret:
        raise WebhookSignatureError(f"{provider} webhook secret not configured", provider=provider)
    if not signature_header:
        raise WebhookSignatureError(f"Missing {provider} signature header", provider=provider)

    sig_clean = signature_header.strip()
    if sig_clean.startswith("sha256="):
        sig_clean = sig_clean[len("sha256=") :]

    if timestamp_header:
        try:
            ts_int = int(timestamp_header.strip())
        except ValueError as exc:
            raise WebhookSignatureError("Invalid webhook timestamp header", provider=provider) from exc
        _check_timestamp_fresh(
            ts_int, now=now, tolerance_seconds=tolerance_seconds, provider=provider
        )
        candidate_msg = f"{ts_int}.".encode("ascii") + raw_body
    else:
        candidate_msg = raw_body

    digest = hmac.new(secret.encode("utf-8"), candidate_msg, hashlib.sha256)
    expected_hex = digest.hexdigest()
    expected_b64 = base64.b64encode(digest.digest()).decode("ascii")
    if not (
        hmac.compare_digest(expected_hex, sig_clean)
        or hmac.compare_digest(expected_b64, sig_clean)
    ):
        raise WebhookSignatureError(f"{provider} webhook HMAC mismatch", provider=provider)


def resolve_webhook_signing_secret(connection: IntegrationConnection) -> str:
    """Load per-connection webhook secret, falling back to configured vendor app secret if applicable."""
    if connection.webhook_secret_ref:
        bundle = open_secret_bundle(
            connection.webhook_secret_ref,
            tenant_id=connection.tenant_id,
            provider=f"{connection.provider}:webhook",
        )
        sec = str(bundle.get("webhook_secret") or "").strip()
        if sec:
            return sec

    cfg = get_int2_settings()
    if connection.provider == "slack":
        return cfg.slack_signing_secret.strip()
    if connection.provider == "zoom":
        return cfg.zoom_webhook_secret_token.strip()
    if connection.provider == "zendesk":
        return cfg.zendesk_webhook_secret.strip()
    return ""


async def receive_inbound_webhook(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    raw_body: bytes,
    headers: dict[str, str],
    validation_token: str = "",
    now: int | None = None,
) -> WebhookReceiptResult:
    """Verify signature, handle challenge handshakes, deduplicate ``external_event_id``, and queue outbox event."""
    provider = connection.provider

    # Microsoft Graph change-notification validationToken handshake (sent before body signature)
    if provider == "teams" and validation_token:
        return WebhookReceiptResult(
            status="challenge",
            event_id="teams_validation_token",
            challenge_plain_text=validation_token,
        )

    lower_headers = {k.lower(): v for k, v in headers.items()}
    secret = resolve_webhook_signing_secret(connection)

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise WebhookSignatureError("Webhook body is not valid JSON", provider=provider) from exc

    if provider == "slack":
        verify_slack_signature(
            signing_secret=secret,
            timestamp_header=lower_headers.get("x-slack-request-timestamp", ""),
            signature_header=lower_headers.get("x-slack-signature", ""),
            raw_body=raw_body,
            now=now,
        )
    elif provider == "zoom":
        verify_zoom_signature(
            secret_token=secret,
            timestamp_header=lower_headers.get("x-zm-request-timestamp", ""),
            signature_header=lower_headers.get("x-zm-signature", ""),
            raw_body=raw_body,
            now=now,
        )
    elif provider == "zendesk":
        verify_zendesk_signature(
            signing_secret=secret,
            timestamp_header=lower_headers.get("x-zendesk-webhook-signature-timestamp", ""),
            signature_header=lower_headers.get("x-zendesk-webhook-signature", ""),
            raw_body=raw_body,
            now=now,
        )
    elif provider == "teams":
        if lower_headers.get("x-webhook-signature"):
            verify_generic_hmac_sha256(
                secret=secret,
                timestamp_header=lower_headers.get("x-webhook-timestamp", ""),
                signature_header=lower_headers.get("x-webhook-signature", ""),
                raw_body=raw_body,
                provider=provider,
                now=now,
            )
        else:
            verify_teams_client_state(expected_secret=secret, payload=payload)
    else:
        verify_generic_hmac_sha256(
            secret=secret,
            timestamp_header=lower_headers.get("x-webhook-timestamp", ""),
            signature_header=(
                lower_headers.get("x-webhook-signature")
                or lower_headers.get("x-freshdesk-webhook-signature")
                or lower_headers.get("x-zoho-webhook-signature")
                or ""
            ),
            raw_body=raw_body,
            provider=provider,
            now=now,
        )

    if provider == "slack" and payload.get("type") == "url_verification":
        return WebhookReceiptResult(
            status="challenge",
            event_id="slack_url_verification",
            challenge_response={"challenge": str(payload.get("challenge") or "")},
        )

    if provider == "zoom" and payload.get("event") == "endpoint.url_validation":
        plain = str((payload.get("payload") or {}).get("plainToken") or "")
        return WebhookReceiptResult(
            status="challenge",
            event_id="zoom_url_validation",
            challenge_response=build_zoom_url_validation_response(
                secret_token=secret, plain_token=plain
            ),
        )

    payload_sha = hashlib.sha256(raw_body).hexdigest()
    first_teams_id = ""
    if provider == "teams" and isinstance(payload.get("value"), list) and payload["value"]:
        first_teams_id = str(payload["value"][0].get("subscriptionId") or "") + ":" + payload_sha[:16]

    event_id = str(
        payload.get("event_id")
        or payload.get("id")
        or first_teams_id
        or lower_headers.get("x-slack-retry-num", "")
        or payload_sha[:40]
    )[:180]
    nonce = f"{connection.id}:{event_id}"[:192]
    event_type = str(
        payload.get("event") or payload.get("type") or payload.get("topic") or "webhook"
    )[:120]

    dup_stmt = select(InboundWebhookEvent).where(
        (
            (InboundWebhookEvent.connection_id == connection.id)
            & (InboundWebhookEvent.external_event_id == event_id)
        )
        | (InboundWebhookEvent.nonce == nonce)
    )
    existing = (await session.execute(dup_stmt)).scalar_one_or_none()
    if existing is not None:
        return WebhookReceiptResult(status="duplicate", event_id=event_id)

    event_row = InboundWebhookEvent(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        provider=provider,
        external_event_id=event_id,
        nonce=nonce,
        event_type=event_type,
        signature_verified=True,
        status="queued",
        payload_sha256=payload_sha,
        payload_excerpt={
            "event_type": event_type,
            "keys": sorted(list(payload.keys()))[:20] if isinstance(payload, dict) else [],
        },
        attempts=0,
    )
    session.add(event_row)
    await session.flush()

    await publish_outbox_event(
        session,
        tenant_id=connection.tenant_id,
        event_type=OUTBOX_EVENT_TYPE_INT2_WEBHOOK,
        aggregate_type="webhook_event",
        aggregate_id=str(event_row.id),
        payload={
            "webhook_event_row_id": str(event_row.id),
            "connection_id": str(connection.id),
            "provider": provider,
            "external_event_id": event_id,
            "event_type": event_type,
            "payload_sha256": payload_sha,
        },
        idempotency_key=f"int2wh:{connection.id}:{event_id}"[:120],
    )
    await session.flush()
    return WebhookReceiptResult(status="accepted", event_id=event_id)
