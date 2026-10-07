"""
app/api/v1/telephony_webhook_routes.py
Provider webhook endpoints (`/api/v1/telephony/webhooks/{provider}/...`) with
signature verification, replay protection, and idempotent event processing.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401 - ensure ORM registry is initialized before auth/telephony imports
from app.db.session import get_session
from app.telephony.exceptions import ProviderWebhookVerificationError
from app.telephony.schemas import WebhookProcessResult
from app.telephony.webhooks import TelephonyWebhookProcessor

router = APIRouter(prefix="/api/v1/telephony/webhooks", tags=["Telephony Webhooks"])


async def _extract_webhook_payload(
    request: Request,
    *,
    default_event_hint: str,
) -> tuple[bytes, dict[str, Any]]:
    raw_body = await request.body()
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/x-www-form-urlencoded" in content_type:
        pairs = parse_qsl(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)
        parsed: dict[str, Any] = {k: v for k, v in pairs}
    else:
        if not raw_body:
            parsed = {}
        else:
            try:
                decoded = json.loads(raw_body.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ProviderWebhookVerificationError("Webhook JSON body must be an object.")
                parsed = dict(decoded)
            except ProviderWebhookVerificationError:
                raise
            except Exception as exc:
                raise ProviderWebhookVerificationError(
                    f"Malformed webhook JSON body: {exc}"
                ) from exc

    if default_event_hint and "event_type" not in parsed and "CallStatus" not in parsed and "status" not in parsed:
        if default_event_hint == "inbound":
            parsed["event_type"] = "call.ringing"
            parsed.setdefault("direction", "inbound")
        elif default_event_hint == "dtmf":
            parsed["event_type"] = "dtmf.received"
        elif default_event_hint == "recording":
            parsed["event_type"] = "recording.available"

    return raw_body, parsed


@router.post("/{provider}/inbound", response_model=WebhookProcessResult)
async def handle_provider_inbound_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WebhookProcessResult:
    raw_body, payload = await _extract_webhook_payload(
        request, default_event_hint="inbound"
    )
    payload.setdefault("direction", "inbound")
    processor = TelephonyWebhookProcessor(session)
    try:
        result = await processor.process_webhook(
            provider=provider,
            headers=dict(request.headers),
            raw_body=raw_body,
            payload=payload,
            url=str(request.url),
        )
        await session.commit()
        return result
    except Exception:
        await session.commit()
        raise


@router.post("/{provider}/status", response_model=WebhookProcessResult)
async def handle_provider_status_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WebhookProcessResult:
    raw_body, payload = await _extract_webhook_payload(
        request, default_event_hint="status"
    )
    processor = TelephonyWebhookProcessor(session)
    try:
        result = await processor.process_webhook(
            provider=provider,
            headers=dict(request.headers),
            raw_body=raw_body,
            payload=payload,
            url=str(request.url),
        )
        await session.commit()
        return result
    except Exception:
        await session.commit()
        raise


@router.post("/{provider}/dtmf", response_model=WebhookProcessResult)
async def handle_provider_dtmf_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WebhookProcessResult:
    raw_body, payload = await _extract_webhook_payload(
        request, default_event_hint="dtmf"
    )
    payload.setdefault("event_type", "dtmf.received")
    processor = TelephonyWebhookProcessor(session)
    try:
        result = await processor.process_webhook(
            provider=provider,
            headers=dict(request.headers),
            raw_body=raw_body,
            payload=payload,
            url=str(request.url),
        )
        await session.commit()
        return result
    except Exception:
        await session.commit()
        raise


@router.post("/{provider}/recording", response_model=WebhookProcessResult)
async def handle_provider_recording_webhook(
    provider: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> WebhookProcessResult:
    raw_body, payload = await _extract_webhook_payload(
        request, default_event_hint="recording"
    )
    payload.setdefault("event_type", "recording.available")
    processor = TelephonyWebhookProcessor(session)
    try:
        result = await processor.process_webhook(
            provider=provider,
            headers=dict(request.headers),
            raw_body=raw_body,
            payload=payload,
            url=str(request.url),
        )
        await session.commit()
        return result
    except Exception:
        await session.commit()
        raise
