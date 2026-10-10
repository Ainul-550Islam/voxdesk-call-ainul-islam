"""
app/telephony/runtime.py
Central Telephony Runtime Orchestrator for outbound call origination, active call
control (hangup, DTMF, transfer), and truthful runtime health reporting.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any as Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, Call, Environment
from app.services.experiment_service import assign_call_to_experiment
from app.telephony.agent_version import (
    AgentVersionResolutionError,
    resolve_telephony_agent_version,
    snapshot_profile,
)
from app.db.telephony_models import (
    TelephonyCallSession,
    TelephonyPhoneNumber,
    TelephonySipConnection,
)
from app.telephony.call_session import (
    CallSessionManager,
    append_runtime_event,
    serialize_call_session,
)
from app.telephony.dtmf import process_call_dtmf
from app.telephony.enums import (
    ACTIVE_CALL_STATES,
    TERMINAL_CALL_STATES,
    CallDirection,
    InternalTelephonyEventType,
    MediaSessionState,
    PhoneNumberLifecycleStatus,
    SipConnectionStatus,
    TelephonyCallState,
    TelephonyHealthState,
    TelephonyProviderName,
)
from app.telephony.exceptions import (
    CallStateTransitionError as CallStateTransitionError,
    PhoneNumberNotFoundError,
    PhoneNumberValidationError,
    TelephonyAuthorizationError,
    TelephonyConfigurationError,
    TelephonyRuntimeError,
)
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
    claim_request,
    complete_request,
)
from app.telephony.media_gateway import media_gateway_manager
from app.telephony.providers.base import SimulatedTelephonyAdapter, TelephonyAdapter
from app.telephony.providers.telnyx import TelnyxAdapter
from app.telephony.providers.twilio import TwilioAdapter
from app.telephony.providers.vonage import VonageAdapter
from app.telephony.schemas import (
    CallDtmfRequest,
    CallDtmfResponse,
    CallHangupRequest,
    CallSessionResponse,
    CallTransferRequest,
    CallTransferResponse,
    OutboundCallCreateRequest,
    ProviderRuntimeStatus,
    TelephonyHealthStatusResponse,
    coerce_e164,
)
from app.telephony.transfer import CallTransferService
from app.runtime.agent_config_resolver import RuntimeConfig, resolve_runtime_config


async def resolve_call_runtime_config(
    session: AsyncSession,
    call: Any,
    tenant: Any = None,
) -> RuntimeConfig:
    """Resolve and bind the call's runtime configuration (Sub-Phase 2E)."""
    return await resolve_runtime_config(session, call, tenant)


def get_provider_adapter(provider_name: str | TelephonyProviderName) -> TelephonyAdapter:
    """Return only a provider that has a real adapter implementation.

    Unknown values never fall back to the simulated provider. Simulation must
    be explicitly selected and remains visibly marked as non-production.
    """
    raw = (
        provider_name.value
        if isinstance(provider_name, TelephonyProviderName)
        else str(provider_name or "")
    )
    prov = raw.strip().upper()
    if prov == TelephonyProviderName.TWILIO.value:
        return TwilioAdapter()
    if prov == TelephonyProviderName.TELNYX.value:
        return TelnyxAdapter()
    if prov == TelephonyProviderName.VONAGE.value:
        return VonageAdapter()
    if prov == TelephonyProviderName.SIMULATED.value:
        return SimulatedTelephonyAdapter()
    if prov == TelephonyProviderName.SIP.value:
        raise TelephonyConfigurationError(
            "SIP call origination is not implemented by the outbound runtime.",
            status_code=501,
            code="SIP_OUTBOUND_NOT_IMPLEMENTED",
            detail={"provider": prov},
        )
    raise TelephonyConfigurationError(
        "The requested telephony provider is not supported.",
        status_code=422,
        code="TELEPHONY_PROVIDER_UNSUPPORTED",
        detail={"provider": prov or "unspecified"},
    )


class TelephonyRuntimeService:
    """Authoritative orchestrator for outbound calls, call control, and runtime health."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assign_experiment_for_call(
        self,
        *,
        call: Call,
        agent_id: str | UUID,
        experiment_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        """Assign an inbound or outbound ``Call`` to the active experiment for ``agent_id``."""
        return await assign_call_to_experiment(
            self.session,
            tenant_id=call.tenant_id,
            agent_id=agent_id,
            call_sid=call.call_sid,
            call_id=call.id,
            experiment_id=experiment_id,
        )

    async def initiate_outbound_call(
        self,
        *,
        tenant_id: UUID,
        payload: OutboundCallCreateRequest,
        environment_id: UUID | None = None,
    ) -> CallSessionResponse:
        """Place one outbound call with database-backed idempotency.

        The pending call and its receipt are committed before contacting a
        provider. If the provider outcome is ambiguous, the receipt stays
        ``in_progress`` so another worker cannot redial on retry. Only an
        actual provider response identifier is stored for live calls.
        """
        tenant_uuid = UUID(str(tenant_id))
        to_e164 = coerce_e164(payload.to_number, field_name="to_number")
        if payload.environment_id is not None and environment_id is not None:
            if payload.environment_id != environment_id:
                raise TelephonyAuthorizationError(
                    "The requested environment differs from the credential environment."
                )
        resolved_env = payload.environment_id or environment_id
        if resolved_env is None:
            resolved_env = await self.session.scalar(
                select(Environment.id).where(
                    Environment.tenant_id == tenant_uuid,
                    Environment.kind == "production",
                    Environment.status == "active",
                ).order_by(Environment.is_default.desc())
            )
        if resolved_env is None:
            raise TelephonyConfigurationError(
                "No active production environment is available for outbound calls.",
                code="TELEPHONY_ENVIRONMENT_NOT_CONFIGURED",
            )
        environment = await self.session.get(Environment, resolved_env)
        if (
            environment is None
            or environment.tenant_id != tenant_uuid
            or environment.status != "active"
        ):
            raise TelephonyAuthorizationError(
                "The requested environment is not active for this tenant."
            )

        phone_row: TelephonyPhoneNumber | None = None
        if payload.phone_number_id is not None:
            phone_row = (
                await self.session.execute(
                    select(TelephonyPhoneNumber).where(
                        TelephonyPhoneNumber.id == payload.phone_number_id,
                        TelephonyPhoneNumber.tenant_id == tenant_uuid,
                    )
                )
            ).scalar_one_or_none()
            if phone_row is None:
                raise PhoneNumberNotFoundError(
                    "The requested phone number is not available in this tenant."
                )
            if phone_row.environment_id not in (None, resolved_env):
                raise TelephonyAuthorizationError(
                    "The phone number is not available in the selected environment.",
                    detail={"phone_number_id": str(payload.phone_number_id)},
                )
            if not phone_row.outbound_enabled:
                raise TelephonyConfigurationError(
                    "Outbound calling is disabled for this phone number.",
                    detail={"phone_number_id": str(phone_row.id)},
                )
            if phone_row.status not in {
                PhoneNumberLifecycleStatus.READY.value,
                PhoneNumberLifecycleStatus.ACTIVE.value,
            }:
                raise TelephonyConfigurationError(
                    "The selected phone number is not ready for outbound calls.",
                    detail={"phone_number_id": str(phone_row.id), "state": phone_row.status},
                )
        elif payload.from_number:
            from_norm = coerce_e164(payload.from_number, field_name="from_number")
            phone_row = (
                await self.session.execute(
                    select(TelephonyPhoneNumber).where(
                        TelephonyPhoneNumber.tenant_id == tenant_uuid,
                        TelephonyPhoneNumber.e164_number == from_norm,
                    )
                )
            ).scalar_one_or_none()
            if phone_row is not None and phone_row.environment_id not in (None, resolved_env):
                raise TelephonyAuthorizationError(
                    "The selected caller ID is not available in the requested environment."
                )

        from_e164 = (
            phone_row.e164_number
            if phone_row is not None
            else (
                coerce_e164(payload.from_number, field_name="from_number")
                if payload.from_number
                else None
            )
        )
        if not from_e164:
            raise PhoneNumberValidationError(
                "Either a registered phone_number_id or a valid E.164 from_number is required."
            )

        resolved_agent_id = payload.agent_id or (
            phone_row.outbound_agent_id if phone_row is not None else None
        )
        if not resolved_agent_id:
            raise TelephonyConfigurationError(
                "Outbound calling requires an agent_id or an agent bound to the phone number.",
                detail={"phone_number_id": str(phone_row.id) if phone_row else None},
            )
        try:
            agent_uuid = UUID(str(resolved_agent_id))
        except (TypeError, ValueError):
            agent_uuid = None
        agent_query = select(Agent).where(Agent.tenant_id == tenant_uuid)
        if agent_uuid is not None:
            agent_query = agent_query.where(Agent.id == agent_uuid)
        else:
            agent_query = agent_query.where(Agent.external_key == str(resolved_agent_id))
        agent_row = (await self.session.execute(agent_query)).scalar_one_or_none()
        if (
            agent_row is None
            or agent_row.deleted_at is not None
            or agent_row.archived_at is not None
        ):
            raise TelephonyConfigurationError(
                "The requested agent is unavailable in this tenant.",
                detail={"agent_id": str(resolved_agent_id)},
            )
        if agent_row.environment_id not in (None, resolved_env):
            raise TelephonyAuthorizationError(
                "The requested agent is not available in the selected environment.",
                detail={"agent_id": str(resolved_agent_id)},
            )

        is_simulation = bool(payload.is_simulation)
        requested_provider = (
            payload.provider.value if payload.provider is not None else None
        )
        number_provider = phone_row.provider if phone_row is not None else None
        if is_simulation:
            if requested_provider and requested_provider.upper() not in {
                TelephonyProviderName.SIMULATED.value,
                (number_provider or "").upper(),
            }:
                raise TelephonyConfigurationError(
                    "A simulation request cannot select a different live provider.",
                    code="SIMULATION_PROVIDER_MISMATCH",
                )
            resolved_provider = TelephonyProviderName.SIMULATED.value
        else:
            resolved_provider = (
                requested_provider or number_provider or TelephonyProviderName.TWILIO.value
            ).strip().upper()
            if number_provider and requested_provider and requested_provider.upper() != number_provider.upper():
                raise TelephonyConfigurationError(
                    "The selected provider does not own the registered phone number.",
                    code="TELEPHONY_PROVIDER_NUMBER_MISMATCH",
                )
            if resolved_provider == TelephonyProviderName.SIMULATED.value:
                raise TelephonyConfigurationError(
                    "provider='SIMULATED' requires is_simulation=true so the result is not presented as a live call.",
                    code="SIMULATION_FLAG_REQUIRED",
                )
            elif resolved_provider == TelephonyProviderName.SIP.value:
                raise TelephonyConfigurationError(
                    "SIP call origination is not implemented by the outbound runtime.",
                    status_code=501,
                    code="SIP_OUTBOUND_NOT_IMPLEMENTED",
                )
            elif resolved_provider not in {
                TelephonyProviderName.TWILIO.value,
                TelephonyProviderName.TELNYX.value,
                TelephonyProviderName.VONAGE.value,
            }:
                raise TelephonyConfigurationError(
                    "The requested telephony provider is not supported.",
                    detail={"provider": resolved_provider},
                )

        try:
            agent_version = await resolve_telephony_agent_version(
                self.session,
                tenant_id=tenant_uuid,
                agent=agent_row,
                environment_id=resolved_env,
                requested_version_number=payload.agent_version_number,
            )
        except AgentVersionResolutionError as exc:
            raise TelephonyConfigurationError(
                str(exc),
                code=exc.code,
                detail=exc.detail,
            ) from None
        try:
            snapshot_profile(agent_version, agent_row)
        except AgentVersionResolutionError as exc:
            raise TelephonyConfigurationError(
                str(exc),
                code=exc.code,
                detail=exc.detail,
            ) from None
        agent_version_number = int(agent_version.version_number)

        idem_key = (payload.idempotency_key or "").strip()
        if not idem_key:
            raise TelephonyRuntimeError(
                "Idempotency-Key is required for outbound calls.",
                status_code=400,
                code="IDEMPOTENCY_KEY_REQUIRED",
            )

        adapter: TelephonyAdapter | None = None
        if not is_simulation:
            adapter = get_provider_adapter(resolved_provider)
            if not adapter.configured():
                raise TelephonyConfigurationError(
                    f"Telephony provider '{resolved_provider}' is not configured with live credentials.",
                    detail={"provider": resolved_provider, "state": "NOT_CONFIGURED"},
                )

        request_data = payload.model_dump(mode="json", exclude={"idempotency_key"})
        request_data.update(
            {
                "environment_id": str(resolved_env),
                "provider_resolved": resolved_provider,
                "agent_resolved": str(agent_row.id),
                "agent_version_resolved": agent_version_number,
                "agent_version_id_resolved": str(agent_version.id),
                "is_simulation_resolved": is_simulation,
            }
        )
        try:
            claim = await claim_request(
                self.session,
                tenant_id=tenant_uuid,
                environment_id=resolved_env,
                operation="telephony.call.outbound",
                key=idem_key,
                request_data=request_data,
            )
        except (IdempotencyConflict, IdempotencyInProgress, IdempotencyPreviousFailure):
            raise
        except ValueError as exc:
            raise TelephonyRuntimeError(
                "Idempotency-Key must be printable and no longer than 256 characters.",
                status_code=400,
                code="IDEMPOTENCY_KEY_INVALID",
            ) from exc

        if claim.replayed:
            try:
                existing_id = UUID(claim.receipt.resource_id)
            except (ValueError, TypeError):
                raise TelephonyRuntimeError(
                    "The stored idempotency result is not recoverable.",
                    status_code=500,
                    code="IDEMPOTENCY_RESULT_CORRUPT",
                ) from None
            existing = await self.session.scalar(
                select(TelephonyCallSession).where(
                    TelephonyCallSession.id == existing_id,
                    TelephonyCallSession.tenant_id == tenant_uuid,
                    TelephonyCallSession.environment_id == resolved_env,
                )
            )
            if existing is None:
                raise TelephonyRuntimeError(
                    "The stored idempotency result no longer exists.",
                    status_code=500,
                    code="IDEMPOTENCY_RESULT_MISSING",
                )
            return serialize_call_session(existing)

        call_id = uuid.uuid4()
        now = datetime.utcnow()
        pending_id = f"pending_{call_id.hex}"
        call_session = TelephonyCallSession(
            id=call_id,
            tenant_id=tenant_uuid,
            environment_id=resolved_env,
            phone_number_id=phone_row.id if phone_row else None,
            agent_id=str(agent_row.id),
            agent_version_number=agent_version_number,
            provider=resolved_provider,
            provider_call_id=pending_id,
            direction=CallDirection.OUTBOUND.value,
            from_number=from_e164,
            to_number=to_e164,
            status=TelephonyCallState.CREATED.value,
            media_state=MediaSessionState.IDLE.value,
            is_simulation=is_simulation,
            execution_kind="SIMULATION" if is_simulation else "PRODUCTION",
            started_at=None,
            runtime_events=[],
            transcript_turns=[],
            dtmf_events=[],
            metadata_json={
                **(payload.metadata or {}),
                "idempotency_receipt_id": str(claim.receipt.id),
                "provider_outcome": "pending",
                "resolved_agent_version_id": str(agent_version.id),
                "resolved_agent_version_number": agent_version_number,
                "resolved_agent_version_status": str(agent_version.status),
                "agent_version_resolution_source": (
                    "request_pin" if payload.agent_version_number is not None else "current_published_pointer"
                ),
            },
            created_at=now,
            updated_at=now,
        )
        claim.receipt.resource_type = "telephony_call_session"
        claim.receipt.resource_id = str(call_id)
        append_runtime_event(
            call_session,
            event_type=InternalTelephonyEventType.CALL_CREATED,
            detail={
                "direction": CallDirection.OUTBOUND.value,
                "provider": resolved_provider,
                "from_number": from_e164,
                "to_number": to_e164,
                "agent_id": str(agent_row.id),
                "agent_version_id": str(agent_version.id),
                "agent_version_number": agent_version_number,
                "environment_id": str(resolved_env),
                "execution_kind": "SIMULATION" if is_simulation else "PRODUCTION",
                "provider_outcome": "pending",
            },
            occurred_at=now,
        )
        self.session.add(call_session)
        await self.session.flush()
        exp_assignment = await assign_call_to_experiment(
            self.session,
            tenant_id=tenant_uuid,
            agent_id=agent_row.id,
            call_sid=pending_id,
            call_id=call_id,
        )
        if exp_assignment is not None:
            call_session.experiment_id = UUID(exp_assignment["experiment_id"])
            call_session.variant_id = UUID(exp_assignment["variant_id"])
        # Durable intent is visible to every worker before a phone provider is
        # contacted. If the next operation has an ambiguous outcome, this row
        # and its in-progress receipt remain available for reconciliation.
        await self.session.commit()

        manager = CallSessionManager(self.session)
        if is_simulation:
            simulated_adapter = SimulatedTelephonyAdapter()
            simulated_result = await simulated_adapter.create_outbound(
                to_number=to_e164,
                from_number=from_e164,
            )
            call_session.provider_call_id = simulated_result.external_id
            call_session.metadata_json = {
                **(call_session.metadata_json or {}),
                "provider_outcome": "simulated",
            }
            await manager.transition_state(
                call_session,
                TelephonyCallState.DIALING,
                occurred_at=datetime.utcnow(),
                event_detail={
                    "provider_call_id": simulated_result.external_id,
                    "execution_kind": "SIMULATION",
                },
            )
            await complete_request(
                self.session,
                claim.receipt,
                resource_type="telephony_call_session",
                resource_id=call_id,
            )
            await self.session.commit()
            return serialize_call_session(call_session)

        try:
            provider_result = await adapter.create_outbound(
                to_number=to_e164,
                from_number=from_e164,
            )
            external_id = str(provider_result.external_id or "").strip()
            if (
                not external_id
                or len(external_id) > 96
                or any(ord(char) < 33 for char in external_id)
            ):
                raise ValueError("provider returned no valid external call identifier")
        except Exception as exc:
            # Do not mark this retryable: a timeout may happen after provider
            # acceptance. Keep the durable receipt in progress so a retry
            # cannot place a second call. Exception text is never persisted.
            call_session.metadata_json = {
                **(call_session.metadata_json or {}),
                "provider_outcome": "unknown",
                "provider_error_category": type(exc).__name__[:64],
            }
            append_runtime_event(
                call_session,
                event_type=InternalTelephonyEventType.CALL_FAILED,
                detail={
                    "provider": resolved_provider,
                    "provider_outcome": "unknown",
                    "error_category": type(exc).__name__[:64],
                },
                occurred_at=datetime.utcnow(),
            )
            await self.session.commit()
            raise TelephonyRuntimeError(
                "The provider did not return a confirmed call identifier. The outcome is unknown; "
                "the idempotency key is locked pending reconciliation.",
                status_code=502,
                code="TELEPHONY_PROVIDER_OUTCOME_UNKNOWN",
                detail={"call_id": str(call_id), "provider": resolved_provider},
            ) from None

        call_session.provider_call_id = external_id
        call_session.metadata_json = {
            **(call_session.metadata_json or {}),
            "provider_outcome": "accepted",
            "provider_status": str(provider_result.status or "accepted")[:64],
        }
        await manager.transition_state(
            call_session,
            TelephonyCallState.DIALING,
            occurred_at=datetime.utcnow(),
            event_detail={
                "provider_call_id": external_id,
                "provider_status": str(provider_result.status or "accepted")[:64],
                "execution_kind": "PRODUCTION",
            },
        )
        await complete_request(
            self.session,
            claim.receipt,
            resource_type="telephony_call_session",
            resource_id=call_id,
        )
        await self.session.commit()
        return serialize_call_session(call_session)

    async def hangup_call(
        self,
        *,
        tenant_id: UUID,
        call_id: UUID,
        payload: CallHangupRequest | None = None,
    ) -> CallSessionResponse:
        req = payload or CallHangupRequest()
        manager = CallSessionManager(self.session)
        call_session = await manager.get_call_session(
            tenant_id=tenant_id, call_id=call_id
        )

        current_state = TelephonyCallState(call_session.status)
        if current_state in TERMINAL_CALL_STATES:
            return serialize_call_session(call_session)

        now = datetime.utcnow()
        media_gateway_manager.close_session(call_session.id)

        if current_state in {
            TelephonyCallState.CREATED,
            TelephonyCallState.DIALING,
            TelephonyCallState.RINGING,
        }:
            await manager.transition_state(
                call_session,
                TelephonyCallState.CANCELLED,
                occurred_at=now,
                hangup_reason=req.reason,
                event_detail={"reason": req.reason, "cancelled_before_answer": True},
            )
        else:
            await manager.transition_state(
                call_session,
                TelephonyCallState.ENDING,
                occurred_at=now,
                hangup_reason=req.reason,
                event_detail={"reason": req.reason},
            )
            await manager.transition_state(
                call_session,
                TelephonyCallState.COMPLETED,
                occurred_at=datetime.utcnow(),
                hangup_reason=req.reason,
                event_detail={"reason": req.reason},
            )

        return serialize_call_session(call_session)

    async def send_call_dtmf(
        self,
        *,
        tenant_id: UUID,
        call_id: UUID,
        payload: CallDtmfRequest,
    ) -> CallDtmfResponse:
        manager = CallSessionManager(self.session)
        call_session = await manager.get_call_session(
            tenant_id=tenant_id, call_id=call_id
        )
        return await process_call_dtmf(
            self.session,
            call_session=call_session,
            digits=payload.digits,
            source=payload.source,
        )

    async def transfer_call(
        self,
        *,
        tenant_id: UUID,
        call_id: UUID,
        payload: CallTransferRequest,
    ) -> CallTransferResponse:
        manager = CallSessionManager(self.session)
        call_session = await manager.get_call_session(
            tenant_id=tenant_id, call_id=call_id
        )
        xfer_svc = CallTransferService(self.session)
        return await xfer_svc.execute_call_transfer(
            tenant_id=tenant_id,
            call_session=call_session,
            payload=payload,
        )

    async def get_health_status(
        self,
        *,
        tenant_id: UUID,
    ) -> TelephonyHealthStatusResponse:
        total_numbers = int(
            (
                await self.session.execute(
                    select(func.count(TelephonyPhoneNumber.id)).where(
                        TelephonyPhoneNumber.tenant_id == tenant_id
                    )
                )
            ).scalar_one()
            or 0
        )
        ready_numbers = int(
            (
                await self.session.execute(
                    select(func.count(TelephonyPhoneNumber.id)).where(
                        TelephonyPhoneNumber.tenant_id == tenant_id,
                        TelephonyPhoneNumber.status.in_(
                            [
                                PhoneNumberLifecycleStatus.READY.value,
                                PhoneNumberLifecycleStatus.ACTIVE.value,
                            ]
                        ),
                    )
                )
            ).scalar_one()
            or 0
        )
        total_sip = int(
            (
                await self.session.execute(
                    select(func.count(TelephonySipConnection.id)).where(
                        TelephonySipConnection.tenant_id == tenant_id
                    )
                )
            ).scalar_one()
            or 0
        )
        ready_sip = int(
            (
                await self.session.execute(
                    select(func.count(TelephonySipConnection.id)).where(
                        TelephonySipConnection.tenant_id == tenant_id,
                        TelephonySipConnection.status == SipConnectionStatus.READY.value,
                    )
                )
            ).scalar_one()
            or 0
        )
        active_calls = int(
            (
                await self.session.execute(
                    select(func.count(TelephonyCallSession.id)).where(
                        TelephonyCallSession.tenant_id == tenant_id,
                        TelephonyCallSession.status.in_(
                            [s.value for s in ACTIVE_CALL_STATES]
                        ),
                    )
                )
            ).scalar_one()
            or 0
        )

        adapters: list[TelephonyAdapter] = [
            TwilioAdapter(),
            TelnyxAdapter(),
            VonageAdapter(),
            SimulatedTelephonyAdapter(),
        ]
        provider_statuses: list[ProviderRuntimeStatus] = [
            adapter.validate_configuration() for adapter in adapters
        ]

        any_live_provider_ready = any(
            p.configured and p.provider in {"TWILIO", "TELNYX", "VONAGE"}
            for p in provider_statuses
        )
        if ready_numbers > 0 or ready_sip > 0 or any_live_provider_ready:
            overall = TelephonyHealthState.READY
        elif total_numbers > 0 or total_sip > 0:
            overall = TelephonyHealthState.CONFIGURED
        else:
            overall = TelephonyHealthState.NOT_CONFIGURED

        default_provider = (
            next(
                (
                    p.provider
                    for p in provider_statuses
                    if p.configured and p.provider != "SIMULATED"
                ),
                None,
            )
            or "SIMULATED"
        )

        return TelephonyHealthStatusResponse(
            state=overall,
            default_provider=default_provider,
            configured_phone_numbers=total_numbers,
            ready_phone_numbers=ready_numbers,
            configured_sip_connections=total_sip,
            ready_sip_connections=ready_sip,
            active_calls=active_calls,
            providers=provider_statuses,
            checked_at=datetime.utcnow(),
        )
