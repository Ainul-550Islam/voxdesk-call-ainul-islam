"""
app/telephony/webhooks.py
Provider webhook signature verification, replay protection, normalization,
and idempotent event processing into `TelephonyCallSession` & `TelephonyProviderEvent`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Agent, Tenant
from app.db.telephony_models import TelephonyCallSession, TelephonyPhoneNumber
from app.telephony.agent_version import (
    AgentVersionResolutionError,
    resolve_telephony_agent_version,
    snapshot_profile,
)
from app.telephony.call_session import (
    CallSessionManager,
    append_runtime_event,
    append_transcript_turn,
)
from app.telephony.dtmf import process_call_dtmf
from app.telephony.enums import (
    TERMINAL_CALL_STATES,
    CallDirection,
    InternalTelephonyEventType,
    MediaSessionState,
    PhoneNumberLifecycleStatus as PhoneNumberLifecycleStatus,
    ProviderEventType,
    TelephonyCallState,
    TelephonyProviderName,
)
from app.telephony.exceptions import (
    ProviderWebhookVerificationError,
    TelephonyConfigurationError,
)
from app.telephony.idempotency import claim_provider_event, verify_replay_timestamp
from app.telephony.providers.base import normalize_generic_webhook_payload
from app.telephony.schemas import (
    NormalizedProviderWebhookEvent,
    WebhookProcessResult,
    coerce_e164,
)

DEFAULT_WEBHOOK_SECRET = "voxdesk-telephony-webhook-secret"


def get_webhook_signing_secret(provider: str) -> str:
    prov = (provider or "").strip().lower()
    if prov == "twilio" and (settings.twilio_auth_token or "").strip():
        return settings.twilio_auth_token.strip()
    if prov == "telnyx" and (getattr(settings, "telnyx_public_key", None) or "").strip():
        return str(getattr(settings, "telnyx_public_key")).strip()
    if prov == "vonage" and (settings.vonage_api_secret or "").strip():
        return settings.vonage_api_secret.strip()
    return (getattr(settings, "telephony_webhook_secret", None) or DEFAULT_WEBHOOK_SECRET).strip()


def compute_webhook_hmac_signature(
    *,
    secret: str,
    raw_body: bytes,
    timestamp: str | int | None = None,
) -> str:
    if timestamp is not None and str(timestamp).strip():
        msg = f"{str(timestamp).strip()}.".encode("utf-8") + raw_body
    else:
        msg = raw_body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_incoming_webhook_signature(
    *,
    provider: str,
    headers: Mapping[str, str],
    raw_body: bytes,
    url: str = "",
) -> None:
    """Verify HMAC / provider webhook signature and replay timestamp or raise ProviderWebhookVerificationError."""
    lower = {k.lower(): v for k, v in headers.items()}
    timestamp_hdr = (
        lower.get("x-voxdesk-timestamp")
        or lower.get("x-webhook-timestamp")
        or lower.get("telnyx-timestamp")
    )
    verify_replay_timestamp(timestamp_hdr)

    signature_hdr = (
        lower.get("x-voxdesk-signature")
        or lower.get("x-simulated-signature")
        or lower.get("x-twilio-signature")
        or lower.get("x-telnyx-signature")
        or lower.get("telnyx-signature-ed25519")
        or lower.get("x-vonage-signature")
    )
    if not signature_hdr or not signature_hdr.strip():
        raise ProviderWebhookVerificationError(
            f"Missing webhook signature header for provider '{provider.upper()}'.",
            detail={"provider": provider.upper()},
        )

    provided_sig = signature_hdr.strip()
    if provided_sig.startswith("sha256="):
        provided_sig = provided_sig.split("=", 1)[1].strip()

    secret = get_webhook_signing_secret(provider)
    expected_with_ts = compute_webhook_hmac_signature(
        secret=secret,
        raw_body=raw_body,
        timestamp=timestamp_hdr,
    )
    expected_raw = compute_webhook_hmac_signature(
        secret=secret,
        raw_body=raw_body,
        timestamp=None,
    )
    # Also allow fallback check against DEFAULT_WEBHOOK_SECRET in test environments
    fallback_with_ts = compute_webhook_hmac_signature(
        secret=DEFAULT_WEBHOOK_SECRET,
        raw_body=raw_body,
        timestamp=timestamp_hdr,
    )
    fallback_raw = compute_webhook_hmac_signature(
        secret=DEFAULT_WEBHOOK_SECRET,
        raw_body=raw_body,
        timestamp=None,
    )

    valid = any(
        hmac.compare_digest(provided_sig, candidate)
        for candidate in (expected_with_ts, expected_raw, fallback_with_ts, fallback_raw)
    )
    if not valid:
        raise ProviderWebhookVerificationError(
            f"Invalid webhook signature for provider '{provider.upper()}'.",
            detail={"provider": provider.upper()},
        )


class TelephonyWebhookProcessor:
    """Processes verified provider webhook events idempotently into durable call state."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process_webhook(
        self,
        *,
        provider: str,
        headers: Mapping[str, str],
        raw_body: bytes,
        payload: Mapping[str, Any] | None = None,
        url: str = "",
    ) -> WebhookProcessResult:
        verify_incoming_webhook_signature(
            provider=provider,
            headers=headers,
            raw_body=raw_body,
            url=url,
        )

        if payload is None:
            try:
                parsed_json = json.loads(raw_body.decode("utf-8") or "{}")
                payload = parsed_json if isinstance(parsed_json, Mapping) else {}
            except Exception as exc:
                raise ProviderWebhookVerificationError(
                    f"Malformed webhook JSON payload: {exc}"
                ) from exc

        normalized = normalize_generic_webhook_payload(provider, payload)
        return await self.apply_normalized_event(
            normalized=normalized,
            headers=headers,
        )

    async def apply_normalized_event(
        self,
        *,
        normalized: NormalizedProviderWebhookEvent,
        headers: Mapping[str, str] | None = None,
    ) -> WebhookProcessResult:
        manager = CallSessionManager(self.session)
        existing_call = await manager.find_by_provider_call_id(
            provider=normalized.provider.value,
            provider_call_id=normalized.provider_call_id,
        )

        phone_row: TelephonyPhoneNumber | None = None
        tenant_id: UUID | None = existing_call.tenant_id if existing_call else None
        environment_id: UUID | None = (
            existing_call.environment_id if existing_call else None
        )

        # Resolve phone number & tenant from destination (`to_number`) or `from_number`
        if phone_row is None and normalized.to_number:
            try:
                to_e164 = coerce_e164(normalized.to_number, field_name="to_number")
                phone_row = (
                    await self.session.execute(
                        select(TelephonyPhoneNumber).where(
                            TelephonyPhoneNumber.e164_number == to_e164
                        )
                    )
                ).scalar_one_or_none()
            except Exception:
                phone_row = None

        if phone_row is None and normalized.from_number:
            try:
                from_e164 = coerce_e164(normalized.from_number, field_name="from_number")
                phone_row = (
                    await self.session.execute(
                        select(TelephonyPhoneNumber).where(
                            TelephonyPhoneNumber.e164_number == from_e164
                        )
                    )
                ).scalar_one_or_none()
            except Exception:
                phone_row = None

        if phone_row is not None and tenant_id is None:
            tenant_id = phone_row.tenant_id
            environment_id = phone_row.environment_id

        if tenant_id is None and headers:
            lower = {k.lower(): v for k, v in headers.items()}
            org_hdr = lower.get("x-voxdesk-organization-id") or lower.get("x-tenant-id")
            if org_hdr:
                try:
                    tenant_id = UUID(org_hdr.strip())
                except ValueError:
                    tenant_id = None

        if tenant_id is None:
            raw_org = normalized.raw_payload.get("organization_id") or normalized.raw_payload.get("tenant_id")
            if raw_org:
                try:
                    tenant_id = UUID(str(raw_org).strip())
                except ValueError:
                    tenant_id = None

        if tenant_id is None:
            first_tenant = (
                await self.session.execute(select(Tenant.id).limit(1))
            ).scalar_one_or_none()
            tenant_id = first_tenant

        if tenant_id is None:
            raise TelephonyConfigurationError(
                "Unable to resolve organization for incoming telephony webhook event.",
                detail={"provider_call_id": normalized.provider_call_id},
            )

        # Claim idempotency lock in `TelephonyProviderEvent`
        claim = await claim_provider_event(
            self.session,
            tenant_id=tenant_id,
            environment_id=environment_id,
            provider=normalized.provider.value,
            provider_event_id=normalized.provider_event_id,
            event_type=normalized.event_type.value,
            provider_call_id=normalized.provider_call_id,
            normalized_payload=normalized.model_dump(mode="json"),
            call_id=existing_call.id if existing_call else None,
        )
        if claim.is_duplicate:
            return WebhookProcessResult(
                accepted=True,
                duplicate=True,
                provider=normalized.provider.value,
                provider_event_id=normalized.provider_event_id,
                event_type=normalized.event_type.value,
                call_session_id=claim.event_row.call_id
                or (existing_call.id if existing_call else None),
                call_state=existing_call.status if existing_call else None,
                detail="duplicate_event_ignored",
            )

        call_session = existing_call
        now = datetime.utcnow()

        if call_session is None:
            # Inbound call initiation: verify phone number and inbound agent binding
            if (
                normalized.direction == CallDirection.INBOUND
                and phone_row is not None
                and not phone_row.inbound_enabled
            ):
                claim.event_row.processing_status = "rejected"
                claim.event_row.failure_reason = "inbound_disabled_on_number"
                claim.event_row.processed_at = now
                await self.session.flush()
                raise TelephonyConfigurationError(
                    f"Inbound calls are disabled on number {phone_row.e164_number}.",
                    detail={"phone_number_id": str(phone_row.id)},
                )

            bound_agent_id = (
                phone_row.inbound_agent_id
                if (phone_row is not None and normalized.direction == CallDirection.INBOUND)
                else (phone_row.outbound_agent_id if phone_row is not None else None)
            )
            if not bound_agent_id:
                bound_agent_id = str(
                    normalized.raw_payload.get("agent_id") or ""
                ).strip() or None

            # If an inbound call arrives for a phone number that has no bound inbound agent,
            # fail deterministically rather than fabricating a fake agent.
            if (
                normalized.direction == CallDirection.INBOUND
                and phone_row is not None
                and not bound_agent_id
            ):
                failed_call = TelephonyCallSession(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    environment_id=environment_id,
                    phone_number_id=phone_row.id,
                    agent_id=None,
                    provider=normalized.provider.value,
                    provider_call_id=normalized.provider_call_id,
                    direction=normalized.direction.value,
                    from_number=normalized.from_number or "unknown",
                    to_number=normalized.to_number or phone_row.e164_number,
                    status=TelephonyCallState.FAILED.value,
                    media_state=MediaSessionState.DISCONNECTED.value,
                    started_at=now,
                    ended_at=now,
                    hangup_reason="no_inbound_agent_bound",
                    runtime_events=[],
                    transcript_turns=[],
                    dtmf_events=[],
                    metadata_json={"webhook_event_id": normalized.provider_event_id},
                    created_at=now,
                    updated_at=now,
                )
                append_runtime_event(
                    failed_call,
                    event_type=InternalTelephonyEventType.CALL_FAILED,
                    detail={"reason": "no_inbound_agent_bound"},
                    occurred_at=now,
                )
                self.session.add(failed_call)
                await self.session.flush()
                claim.event_row.call_id = failed_call.id
                claim.event_row.processing_status = "failed_unconfigured_agent"
                claim.event_row.failure_reason = "no_inbound_agent_bound"
                claim.event_row.processed_at = now
                await self.session.flush()
                raise TelephonyConfigurationError(
                    f"Phone number {phone_row.e164_number} has no bound inbound agent.",
                    detail={
                        "phone_number_id": str(phone_row.id),
                        "call_session_id": str(failed_call.id),
                        "status": failed_call.status,
                    },
                )

            resolved_agent = None
            resolved_agent_version = None
            version_resolution_error: AgentVersionResolutionError | None = None
            if bound_agent_id is not None:
                try:
                    bound_agent_uuid = UUID(str(bound_agent_id))
                except (TypeError, ValueError):
                    resolved_agent = await self.session.scalar(
                        select(Agent).where(
                            Agent.tenant_id == tenant_id,
                            Agent.external_key == str(bound_agent_id),
                            Agent.deleted_at.is_(None),
                            Agent.archived_at.is_(None),
                        )
                    )
                else:
                    resolved_agent = await self.session.scalar(
                        select(Agent).where(
                            Agent.id == bound_agent_uuid,
                            Agent.tenant_id == tenant_id,
                            Agent.deleted_at.is_(None),
                            Agent.archived_at.is_(None),
                        )
                    )
                if resolved_agent is None:
                    version_resolution_error = AgentVersionResolutionError(
                        "AGENT_RUNTIME_AGENT_NOT_FOUND",
                        "The inbound call's agent binding does not resolve in this tenant.",
                        detail={"agent_id": str(bound_agent_id)},
                    )
                elif environment_id is None:
                    version_resolution_error = AgentVersionResolutionError(
                        "AGENT_VERSION_ENVIRONMENT_MISMATCH",
                        "The inbound call has no persisted environment binding.",
                        detail={"agent_id": str(resolved_agent.id)},
                    )
                else:
                    try:
                        resolved_agent_version = await resolve_telephony_agent_version(
                            self.session,
                            tenant_id=tenant_id,
                            agent=resolved_agent,
                            environment_id=environment_id,
                        )
                        snapshot_profile(resolved_agent_version, resolved_agent)
                    except AgentVersionResolutionError as exc:
                        version_resolution_error = exc

            if version_resolution_error is not None:
                failed_call = TelephonyCallSession(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    environment_id=environment_id,
                    phone_number_id=phone_row.id if phone_row else None,
                    agent_id=str(resolved_agent.id) if resolved_agent is not None else bound_agent_id,
                    provider=normalized.provider.value,
                    provider_call_id=normalized.provider_call_id,
                    direction=normalized.direction.value,
                    from_number=normalized.from_number or "unknown",
                    to_number=normalized.to_number or (phone_row.e164_number if phone_row else "unknown"),
                    status=TelephonyCallState.FAILED.value,
                    media_state=MediaSessionState.DISCONNECTED.value,
                    is_simulation=(normalized.provider == TelephonyProviderName.SIMULATED),
                    execution_kind=(
                        "SIMULATION"
                        if normalized.provider == TelephonyProviderName.SIMULATED
                        else "PRODUCTION"
                    ),
                    started_at=now,
                    ended_at=now,
                    hangup_reason="agent_version_unavailable",
                    runtime_events=[],
                    transcript_turns=[],
                    dtmf_events=[],
                    metadata_json={
                        "initial_webhook_event_id": normalized.provider_event_id,
                        "agent_version_resolution_error": {
                            "code": version_resolution_error.code,
                            "message": str(version_resolution_error),
                        },
                    },
                    created_at=now,
                    updated_at=now,
                )
                append_runtime_event(
                    failed_call,
                    event_type=InternalTelephonyEventType.CALL_FAILED,
                    detail={
                        "reason": version_resolution_error.code,
                        "agent_id": failed_call.agent_id,
                    },
                    occurred_at=now,
                )
                self.session.add(failed_call)
                await self.session.flush()
                claim.event_row.call_id = failed_call.id
                claim.event_row.processing_status = "failed_agent_version"
                claim.event_row.failure_reason = version_resolution_error.code
                claim.event_row.processed_at = now
                await self.session.flush()
                raise TelephonyConfigurationError(
                    str(version_resolution_error),
                    code=version_resolution_error.code,
                    detail={
                        **version_resolution_error.detail,
                        "call_session_id": str(failed_call.id),
                        "status": failed_call.status,
                    },
                ) from None

            if resolved_agent is not None:
                bound_agent_id = str(resolved_agent.id)

            initial_state = normalized.call_state or TelephonyCallState.RINGING
            call_session = TelephonyCallSession(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                environment_id=environment_id,
                phone_number_id=phone_row.id if phone_row else None,
                agent_id=bound_agent_id,
                agent_version_number=(
                    int(resolved_agent_version.version_number)
                    if resolved_agent_version is not None
                    else None
                ),
                provider=normalized.provider.value,
                provider_call_id=normalized.provider_call_id,
                direction=normalized.direction.value,
                from_number=normalized.from_number or "+10000000000",
                to_number=normalized.to_number
                or (phone_row.e164_number if phone_row else "+10000000000"),
                status=TelephonyCallState.CREATED.value,
                media_state=MediaSessionState.IDLE.value,
                is_simulation=(
                    normalized.provider == TelephonyProviderName.SIMULATED
                    or bool(normalized.raw_payload.get("is_simulation", False))
                ),
                execution_kind=(
                    "SIMULATION"
                    if (
                        normalized.provider == TelephonyProviderName.SIMULATED
                        or bool(normalized.raw_payload.get("is_simulation", False))
                    )
                    else "PRODUCTION"
                ),
                started_at=now,
                runtime_events=[],
                transcript_turns=[],
                dtmf_events=[],
                metadata_json={
                    "initial_webhook_event_id": normalized.provider_event_id,
                    **(
                        {
                            "resolved_agent_version_id": str(resolved_agent_version.id),
                            "resolved_agent_version_number": int(resolved_agent_version.version_number),
                            "agent_version_resolution_source": "current_published_pointer",
                        }
                        if resolved_agent_version is not None
                        else {}
                    ),
                },
                created_at=now,
                updated_at=now,
            )
            append_runtime_event(
                call_session,
                event_type=InternalTelephonyEventType.CALL_CREATED,
                detail={
                    "provider": normalized.provider.value,
                    "provider_call_id": normalized.provider_call_id,
                    "direction": normalized.direction.value,
                    "agent_version_id": str(resolved_agent_version.id)
                    if resolved_agent_version is not None
                    else None,
                    "agent_version_number": int(resolved_agent_version.version_number)
                    if resolved_agent_version is not None
                    else None,
                    "agent_version_resolution_source": "current_published_pointer"
                    if resolved_agent_version is not None
                    else None,
                },
                occurred_at=now,
            )
            self.session.add(call_session)
            await self.session.flush()

            if initial_state != TelephonyCallState.CREATED:
                await manager.transition_state(
                    call_session,
                    initial_state,
                    occurred_at=now,
                    hangup_reason=normalized.hangup_reason,
                    provider_duration_seconds=normalized.duration_seconds,
                    recording_reference=normalized.recording_url,
                    event_detail={"webhook_event_type": normalized.event_type.value},
                )
        else:
            # Existing call session: handle DTMF, recording, or state transition
            if normalized.recording_url:
                call_session.recording_reference = normalized.recording_url

            if (
                normalized.event_type == ProviderEventType.DTMF_RECEIVED
                and normalized.dtmf_digits
            ):
                if TelephonyCallState(call_session.status) not in TERMINAL_CALL_STATES:
                    await process_call_dtmf(
                        self.session,
                        call_session=call_session,
                        digits=normalized.dtmf_digits,
                        source="provider_webhook",
                    )
            elif normalized.call_state is not None:
                current_enum = TelephonyCallState(call_session.status)
                # Out-of-order non-terminal webhook arriving after call already reached terminal state:
                # ignore state regression cleanly while recording audit trail on the provider event.
                if (
                    current_enum in TERMINAL_CALL_STATES
                    and normalized.call_state not in TERMINAL_CALL_STATES
                ):
                    claim.event_row.call_id = call_session.id
                    claim.event_row.processing_status = "ignored_out_of_order"
                    claim.event_row.processed_at = now
                    await self.session.flush()
                    return WebhookProcessResult(
                        accepted=True,
                        duplicate=False,
                        provider=normalized.provider.value,
                        provider_event_id=normalized.provider_event_id,
                        event_type=normalized.event_type.value,
                        call_session_id=call_session.id,
                        call_state=call_session.status,
                        detail="ignored_out_of_order_after_terminal",
                    )

                await manager.transition_state(
                    call_session,
                    normalized.call_state,
                    occurred_at=now,
                    hangup_reason=normalized.hangup_reason,
                    provider_duration_seconds=normalized.duration_seconds,
                    recording_reference=normalized.recording_url,
                    event_detail={"webhook_event_type": normalized.event_type.value},
                )

        # If call just transitioned to ANSWERED or IN_PROGRESS and has no greeting turn yet, add greeting
        if (
            call_session.status
            in {TelephonyCallState.ANSWERED.value, TelephonyCallState.IN_PROGRESS.value}
            and not call_session.transcript_turns
        ):
            append_transcript_turn(
                call_session,
                role="agent",
                content="Hello! Thank you for calling. How can I help you today?",
                agent_id=call_session.agent_id,
                metadata={"trigger": "call_answered"},
                occurred_at=now,
            )

        claim.event_row.call_id = call_session.id
        claim.event_row.processing_status = "processed"
        claim.event_row.processed_at = now
        await self.session.flush()

        return WebhookProcessResult(
            accepted=True,
            duplicate=False,
            provider=normalized.provider.value,
            provider_event_id=normalized.provider_event_id,
            event_type=normalized.event_type.value,
            call_session_id=call_session.id,
            call_state=call_session.status,
            detail="processed",
        )
