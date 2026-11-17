"""
app/telephony/webhooks.py
Provider webhook signature verification, replay protection, normalization,
and idempotent event processing into `TelephonyCallSession` & `TelephonyProviderEvent`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
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
from app.telephony.number_routing import build_routing
from app.telephony.providers.base import normalize_generic_webhook_payload
from app.telephony.schemas import (
    NormalizedProviderWebhookEvent,
    WebhookProcessResult,
    coerce_e164,
)

def get_webhook_signing_secret(provider: str) -> str:
    """Return the signing secret for a provider. Fails closed when none is configured.

    There is deliberately no built-in default: a public default secret would let
    anyone forge webhooks on a deployment that forgot to configure one.
    """
    prov = (provider or "").strip().lower()
    if prov == "twilio" and (settings.twilio_auth_token or "").strip():
        return settings.twilio_auth_token.strip()
    if prov == "telnyx" and (getattr(settings, "telnyx_public_key", None) or "").strip():
        return str(getattr(settings, "telnyx_public_key")).strip()
    if prov == "vonage" and (settings.vonage_api_secret or "").strip():
        return settings.vonage_api_secret.strip()
    secret = (getattr(settings, "telephony_webhook_secret", None) or "").strip()
    if not secret:
        raise ProviderWebhookVerificationError(
            f"No webhook signing secret configured for provider '{(provider or '').upper()}'.",
            detail={"provider": (provider or "").upper(), "reason": "webhook_secret_not_configured"},
        )
    return secret


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
    valid = any(
        hmac.compare_digest(provided_sig, candidate)
        for candidate in (expected_with_ts, expected_raw)
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
        self._pending_slot_releases: set[tuple[str, str]] = set()
        self._pending_slot_acquisitions: set[tuple[str, str]] = set()

    def _queue_call_slot_acquisition(self, tenant_id: Any, call_id: Any) -> None:
        self._pending_slot_acquisitions.add((str(tenant_id), str(call_id)))

    def commit_pending_call_slot_acquisitions(self) -> None:
        """Keep new leases once the corresponding call-session commit succeeds."""
        self._pending_slot_acquisitions.clear()

    async def rollback_pending_call_slot_acquisitions(self) -> None:
        """Compensate leases if the database transaction that created them fails."""
        from app.telephony.concurrency_policy import release_call_slot

        pending = tuple(self._pending_slot_acquisitions)
        for tenant_id, call_id in pending:
            await release_call_slot(tenant_id, call_id)
        self._pending_slot_acquisitions.difference_update(pending)

    def _queue_call_slot_release(self, tenant_id: Any, call_id: Any) -> None:
        self._pending_slot_releases.add((str(tenant_id), str(call_id)))

    async def release_pending_call_slots(self) -> None:
        """Release terminal-call leases only after the webhook transaction commits."""
        from app.telephony.concurrency_policy import release_call_slot

        pending = tuple(self._pending_slot_releases)
        for tenant_id, call_id in pending:
            await release_call_slot(tenant_id, call_id)
        self._pending_slot_releases.difference_update(pending)

    async def _record_failed_inbound_history(
        self,
        normalized: NormalizedProviderWebhookEvent,
        *,
        tenant_id: UUID,
        environment_id: UUID | None,
        reason: str,
        agent_id: str | None = None,
    ):
        from app.telephony.qos import record_failed_inbound

        return await record_failed_inbound(
            self.session,
            tenant_id,
            call_sid=normalized.provider_call_id,
            from_number=normalized.from_number or "unknown",
            to_number=normalized.to_number or "unknown",
            reason=reason,
            environment_id=environment_id,
            provider=normalized.provider.value,
            agent_id=agent_id,
        )

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
                __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
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
                __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
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
            if (
                existing_call is not None
                and TelephonyCallState(existing_call.status) in TERMINAL_CALL_STATES
            ):
                self._queue_call_slot_release(existing_call.tenant_id, existing_call.id)
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
        now = datetime.now(timezone.utc)
        answer_policy_denied = False

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
                failed_history = await self._record_failed_inbound_history(
                    normalized,
                    tenant_id=tenant_id,
                    environment_id=environment_id,
                    reason="inbound_disabled_on_number",
                )
                failed_call = TelephonyCallSession(
                    id=uuid.uuid4(),
                    legacy_call_id=failed_history.id,
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
                    hangup_reason="inbound_disabled_on_number",
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
                    detail={"reason": "inbound_disabled_on_number"},
                    occurred_at=now,
                )
                self.session.add(failed_call)
                claim.event_row.call_id = failed_call.id
                await self.session.flush()
                raise TelephonyConfigurationError(
                    f"Inbound calls are disabled on number {phone_row.e164_number}.",
                    detail={
                        "phone_number_id": str(phone_row.id),
                        "call_session_id": str(failed_call.id),
                    },
                )

            channel = (
                "inbound"
                if normalized.direction == CallDirection.INBOUND
                else "outbound"
            )
            bound_agent_id = None
            number_routing_decision: dict[str, Any] | None = None
            if phone_row is not None:
                legacy_in = str(phone_row.inbound_agent_id or "")
                legacy_out = str(phone_row.outbound_agent_id or "")
                routing = build_routing(
                    phone_row.routing,
                    legacy_inbound_agent_id=legacy_in,
                    legacy_outbound_agent_id=legacy_out,
                )
                entries = routing.entries_for(channel)
                if entries:
                    caller_identity = (
                        normalized.from_number
                        if channel == "inbound"
                        else normalized.to_number
                    ) or ""
                    routing_salt = f"{phone_row.e164_number}|{channel}"
                    bound_agent_id = routing.select(
                        channel,
                        caller=caller_identity,
                        salt=routing_salt,
                    )
                    number_routing_decision = {
                        "phone_number_id": str(phone_row.id),
                        "number": phone_row.e164_number,
                        "channel": channel,
                        "selected_agent_id": bound_agent_id,
                        "bucket": routing.bucket(
                            channel, caller=caller_identity, salt=routing_salt
                        ),
                        "candidate_count": len(entries),
                    }
            if not bound_agent_id:
                bound_agent_id = str(
                    normalized.raw_payload.get("agent_id") or ""
                ).strip() or None

            raw_overrides = normalized.raw_payload.get("voxdesk_overrides")
            if normalized.direction == CallDirection.INBOUND and isinstance(raw_overrides, Mapping):
                parsed_overrides = parse_inbound_overrides(raw_overrides)
                selected_overrides = resolve_inbound_agent(
                    parsed_overrides,
                    number_agent_id=str(bound_agent_id or ""),
                )
                if selected_overrides.agent_id:
                    bound_agent_id = selected_overrides.agent_id
            else:
                parsed_overrides = InboundOverrides()
                selected_overrides = parsed_overrides

            resolution_metadata: dict[str, Any] = {}
            if number_routing_decision is not None:
                resolution_metadata["number_routing"] = number_routing_decision
            if selected_overrides.source == "webhook":
                resolution_metadata["inbound_overrides"] = selected_overrides.as_dict()
                if selected_overrides.dynamic_variables:
                    resolution_metadata["dynamic_variables"] = dict(
                        selected_overrides.dynamic_variables
                    )
                if selected_overrides.metadata:
                    resolution_metadata["metadata"] = dict(selected_overrides.metadata)

            # If an inbound call arrives for a phone number that has no bound inbound agent,
            # fail deterministically rather than fabricating a fake agent.
            if (
                normalized.direction == CallDirection.INBOUND
                and phone_row is not None
                and not bound_agent_id
            ):
                failed_history = await self._record_failed_inbound_history(
                    normalized,
                    tenant_id=tenant_id,
                    environment_id=environment_id,
                    reason="no_inbound_agent_bound",
                )
                failed_call = TelephonyCallSession(
                    id=uuid.uuid4(),
                    legacy_call_id=failed_history.id,
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
                    metadata_json={
                        "webhook_event_id": normalized.provider_event_id,
                        **resolution_metadata,
                    },
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
                            requested_version_number=(
                                selected_overrides.agent_version
                                if selected_overrides.source == "webhook"
                                and selected_overrides.agent_id
                                else None
                            ),
                        )
                        snapshot_profile(resolved_agent_version, resolved_agent)
                    except AgentVersionResolutionError as exc:
                        version_resolution_error = exc

            if version_resolution_error is not None:
                failed_history = None
                if normalized.direction == CallDirection.INBOUND:
                    failed_history = await self._record_failed_inbound_history(
                        normalized,
                        tenant_id=tenant_id,
                        environment_id=environment_id,
                        reason=version_resolution_error.code,
                        agent_id=resolved_agent.id if resolved_agent is not None else None,
                    )
                failed_call = TelephonyCallSession(
                    id=uuid.uuid4(),
                    legacy_call_id=failed_history.id if failed_history is not None else None,
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
                        **resolution_metadata,
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

            if normalized.direction == CallDirection.INBOUND:
                from app.telephony.country_policy import decide_for_session

                country_decision = await decide_for_session(
                    self.session,
                    tenant_id,
                    normalized.from_number,
                    stage="answer",
                    number_id=str(phone_row.id) if phone_row is not None else None,
                    agent_id=str(bound_agent_id) if bound_agent_id else None,
                )
                if not country_decision.allowed:
                    failed_history = await self._record_failed_inbound_history(
                        normalized,
                        tenant_id=tenant_id,
                        environment_id=environment_id,
                        reason="inbound_country_policy_denied",
                        agent_id=bound_agent_id,
                    )
                    failed_call = TelephonyCallSession(
                        id=uuid.uuid4(),
                        legacy_call_id=failed_history.id,
                        tenant_id=tenant_id,
                        environment_id=environment_id,
                        phone_number_id=phone_row.id if phone_row else None,
                        agent_id=bound_agent_id,
                        provider=normalized.provider.value,
                        provider_call_id=normalized.provider_call_id,
                        direction=normalized.direction.value,
                        from_number=normalized.from_number or "unknown",
                        to_number=normalized.to_number or (phone_row.e164_number if phone_row else "unknown"),
                        status=TelephonyCallState.FAILED.value,
                        media_state=MediaSessionState.DISCONNECTED.value,
                        started_at=now,
                        ended_at=now,
                        hangup_reason="inbound_country_policy_denied",
                        runtime_events=[],
                        transcript_turns=[],
                        dtmf_events=[],
                        metadata_json={
                            "webhook_event_id": normalized.provider_event_id,
                            "country_policy": country_decision.as_dict(),
                            **resolution_metadata,
                        },
                        created_at=now,
                        updated_at=now,
                    )
                    append_runtime_event(
                        failed_call,
                        event_type=InternalTelephonyEventType.CALL_FAILED,
                        detail={"reason": "inbound_country_policy_denied", "country": country_decision.country},
                        occurred_at=now,
                    )
                    self.session.add(failed_call)
                    claim.event_row.call_id = failed_call.id
                    claim.event_row.processing_status = "failed_country_policy"
                    claim.event_row.failure_reason = "inbound_country_policy_denied"
                    claim.event_row.processed_at = now
                    await self.session.flush()
                    return WebhookProcessResult(
                        accepted=True,
                        duplicate=False,
                        provider=normalized.provider.value,
                        provider_event_id=normalized.provider_event_id,
                        event_type=normalized.event_type.value,
                        call_session_id=failed_call.id,
                        call_state=failed_call.status,
                        detail="inbound_country_policy_denied",
                    )

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
                    **resolution_metadata,
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

            if (
                normalized.direction == CallDirection.INBOUND
                and initial_state not in TERMINAL_CALL_STATES
                and not call_session.is_simulation
            ):
                from app.telephony.concurrency_policy import (
                    ConcurrencyError,
                    acquire_call_slot,
                )

                try:
                    lease = await acquire_call_slot(
                        self.session,
                        tenant_id,
                        call_session.id,
                        direction="inbound",
                    )
                except ConcurrencyError:
                    failed_history = await self._record_failed_inbound_history(
                        normalized,
                        tenant_id=tenant_id,
                        environment_id=environment_id,
                        reason="inbound_concurrency_exhausted",
                        agent_id=bound_agent_id,
                    )
                    call_session.legacy_call_id = failed_history.id
                    call_session.status = TelephonyCallState.FAILED.value
                    call_session.media_state = MediaSessionState.DISCONNECTED.value
                    call_session.ended_at = now
                    call_session.hangup_reason = "inbound_concurrency_exhausted"
                    append_runtime_event(
                        call_session,
                        event_type=InternalTelephonyEventType.CALL_FAILED,
                        detail={"reason": "inbound_concurrency_exhausted"},
                        occurred_at=now,
                    )
                    claim.event_row.call_id = call_session.id
                    claim.event_row.processing_status = "failed_concurrency"
                    claim.event_row.failure_reason = "inbound_concurrency_exhausted"
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
                        detail="inbound_concurrency_exhausted",
                    )
                self._queue_call_slot_acquisition(tenant_id, call_session.id)
                metadata = dict(call_session.metadata_json or {})
                metadata["concurrency_lease"] = {
                    "direction": lease.direction,
                    "burst": lease.burst,
                    "billable_overage": lease.billable_overage,
                    "base": lease.base,
                    "ceiling": lease.ceiling,
                }
                call_session.metadata_json = metadata

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
                    self._queue_call_slot_release(call_session.tenant_id, call_session.id)
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

                target_state = normalized.call_state
                answer_policy_decision = None
                if (
                    call_session.direction == CallDirection.INBOUND.value
                    and target_state
                    in {TelephonyCallState.ANSWERED, TelephonyCallState.IN_PROGRESS}
                ):
                    from app.telephony.country_policy import decide_for_session

                    answer_policy_decision = await decide_for_session(
                        self.session,
                        call_session.tenant_id,
                        call_session.from_number,
                        stage="answer",
                        number_id=(
                            str(call_session.phone_number_id)
                            if call_session.phone_number_id is not None
                            else None
                        ),
                        agent_id=(
                            str(call_session.agent_id) if call_session.agent_id else None
                        ),
                    )
                    if not answer_policy_decision.allowed:
                        from app.telephony.qos import record_failed_inbound

                        failed_history = await record_failed_inbound(
                            self.session,
                            call_session.tenant_id,
                            call_sid=call_session.provider_call_id,
                            from_number=(
                                call_session.from_number or normalized.from_number or "unknown"
                            ),
                            to_number=(
                                call_session.to_number or normalized.to_number or "unknown"
                            ),
                            reason="inbound_country_policy_denied",
                            environment_id=call_session.environment_id,
                            provider=call_session.provider,
                            agent_id=call_session.agent_id,
                        )
                        if failed_history is not None:
                            call_session.legacy_call_id = failed_history.id
                        metadata = dict(call_session.metadata_json or {})
                        metadata["country_policy"] = answer_policy_decision.as_dict()
                        call_session.metadata_json = metadata
                        target_state = TelephonyCallState.FAILED

                answer_policy_denied = bool(
                    answer_policy_decision is not None
                    and not answer_policy_decision.allowed
                )
                await manager.transition_state(
                    call_session,
                    target_state,
                    occurred_at=now,
                    hangup_reason=(
                        "inbound_country_policy_denied"
                        if answer_policy_denied
                        else normalized.hangup_reason
                    ),
                    provider_duration_seconds=normalized.duration_seconds,
                    recording_reference=normalized.recording_url,
                    event_detail={
                        "webhook_event_type": normalized.event_type.value,
                        **(
                            {
                                "failure_reason": "inbound_country_policy_denied",
                                "country_policy": answer_policy_decision.as_dict(),
                            }
                            if answer_policy_denied
                            else {}
                        ),
                    },
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

        if TelephonyCallState(call_session.status) in TERMINAL_CALL_STATES:
            self._queue_call_slot_release(call_session.tenant_id, call_session.id)

        claim.event_row.call_id = call_session.id
        claim.event_row.processing_status = (
            "failed_country_policy" if answer_policy_denied else "processed"
        )
        if answer_policy_denied:
            claim.event_row.failure_reason = "inbound_country_policy_denied"
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
            detail=(
                "inbound_country_policy_denied" if answer_policy_denied else "processed"
            ),
        )

# ---------------------------------------------------------------------------
# PART 12 (P12-25) — inbound webhook may choose the agent, version and variables
# ---------------------------------------------------------------------------

#: What an inbound-call webhook is allowed to set. Anything else is ignored rather than merged
#: blindly: an unknown key usually means the caller's integration is out of date.
INBOUND_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {"agent_id", "agent_version", "dynamic_variables", "metadata", "fallback_agent_id"}
)

WEBHOOK_TIMEOUT_S = 3.0
WEBHOOK_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class InboundOverrides:
    """The resolved response of an inbound-call webhook."""

    agent_id: str = ""
    agent_version: int | None = None
    dynamic_variables: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    fallback_agent_id: str = ""
    source: str = "default"  # webhook | fallback | default
    ignored_keys: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "dynamic_variables": self.dynamic_variables,
            "metadata": self.metadata,
            "fallback_agent_id": self.fallback_agent_id,
            "source": self.source,
            "ignored_keys": list(self.ignored_keys),
        }


def parse_inbound_overrides(payload: Any) -> InboundOverrides:
    """Validate a webhook response body.

    A malformed body is not fatal: the call still has to be answered, so we keep the keys we
    understand, record the ones we dropped, and let the caller fall back to the number's agent.
    """
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            return InboundOverrides(source="default", ignored_keys=("payload",))
        payload = decoded
    if not isinstance(payload, Mapping):
        return InboundOverrides(source="default", ignored_keys=("payload",))
    unknown = tuple(sorted(str(key) for key in payload if str(key) not in INBOUND_OVERRIDE_KEYS))

    variables = payload.get("dynamic_variables")
    metadata = payload.get("metadata")
    if isinstance(variables, str):
        try:
            variables = json.loads(variables)
        except (TypeError, ValueError):
            variables = None
            unknown = tuple(sorted(set(unknown) | {"dynamic_variables"}))
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = None
            unknown = tuple(sorted(set(unknown) | {"metadata"}))

    version = payload.get("agent_version")
    try:
        parsed_version = int(version) if version is not None else None
        if parsed_version is not None and parsed_version < 1:
            parsed_version = None
            unknown = tuple(sorted(set(unknown) | {"agent_version"}))
    except (TypeError, ValueError):
        parsed_version = None
        unknown = tuple(sorted(set(unknown) | {"agent_version"}))

    return InboundOverrides(
        agent_id=str(payload.get("agent_id") or "").strip(),
        agent_version=parsed_version,
        dynamic_variables=dict(variables) if isinstance(variables, Mapping) else {},
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        fallback_agent_id=str(payload.get("fallback_agent_id") or "").strip(),
        source="webhook",
        ignored_keys=unknown,
    )


def resolve_inbound_agent(
    overrides: InboundOverrides,
    *,
    number_agent_id: str = "",
    webhook_failed: bool = False,
) -> InboundOverrides:
    """Decide which agent takes the call when the webhook did not produce a usable answer."""
    if overrides.agent_id and not webhook_failed:
        return overrides
    if overrides.fallback_agent_id or webhook_failed:
        return InboundOverrides(
            agent_id=overrides.fallback_agent_id or number_agent_id,
            agent_version=overrides.agent_version,
            dynamic_variables=overrides.dynamic_variables,
            metadata=overrides.metadata,
            fallback_agent_id=overrides.fallback_agent_id,
            source="fallback",
            ignored_keys=overrides.ignored_keys,
        )
    return InboundOverrides(
        agent_id=number_agent_id,
        dynamic_variables=overrides.dynamic_variables,
        metadata=overrides.metadata,
        source="default",
        ignored_keys=overrides.ignored_keys,
    )


def apply_inbound_overrides_to_call(call: Any, payload: Any) -> InboundOverrides:
    """Persist verified per-call overrides for the live runtime resolver."""
    parsed = parse_inbound_overrides(payload)
    resolved = resolve_inbound_agent(parsed)
    context = dict(getattr(call, "transfer_context", None) or {})
    context["inbound_overrides"] = resolved.as_dict()

    if resolved.dynamic_variables:
        variable_overrides = context.get("dynamic_variable_overrides")
        variable_overrides = (
            dict(variable_overrides) if isinstance(variable_overrides, Mapping) else {}
        )
        variable_overrides.update(resolved.dynamic_variables)
        context["dynamic_variable_overrides"] = variable_overrides
        context["dynamic_variables"] = variable_overrides
    if resolved.metadata:
        metadata = context.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        metadata.update(resolved.metadata)
        context["metadata"] = metadata

    call.transfer_context = context
    return resolved
