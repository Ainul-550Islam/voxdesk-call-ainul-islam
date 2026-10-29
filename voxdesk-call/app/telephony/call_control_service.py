"""Live call control: hold/resume, DTMF batches, transfer cancel/whisper/bulk.

Everything in here is a side effect on a call the carrier still owns, so the
ordering rules are the whole design:

1. **Load the call inside the tenant and environment scope.** An id is not
   authorization; every lookup filters on ``tenant_id`` and the caller's
   resolved environment.
2. **Validate the state machine before touching the provider.** Pause is legal
   only from an in-progress, un-held call; resume only from a held one.
3. **Claim the receipt, then call the provider, then persist the result.**
   A replay returns the stored result and never re-dials. The receipt lives in
   ``TelephonyProviderEvent`` via :mod:`app.telephony.idempotency`, so it
   survives a process restart and is scoped per tenant.
4. **Never report success before the provider confirms.** Every provider leg
   returns a ``CallResult`` only after the carrier accepted the redirect.

Where the hold state is stored
------------------------------

``CallStatus`` is a closed vocabulary shared with the Twilio status webhook and
the billing/analytics readers, and it is pinned by
``tests/test_enum_consistency.py``. Adding a ``PAUSED`` member there would
change persisted enum meaning and needs a migration, so the hold marker lives
in the call's existing ``transfer_context`` JSON column under the
``call_control`` key. It is read and written only through this module, which
keeps the state machine in exactly one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext
from app.core.logging import log
from app.db.models import TRANSFER_IN_FLIGHT, Call, CallStatus
from app.schemas.call_control import (
    BulkTransferItemResult,
    BulkTransferResponse,
    CallControlCapabilities,
    CallPauseResponse,
    CallResumeResponse,
    DtmfBatchResponse,
    DtmfItemResult,
    TransferCancelResponse,
    TransferWhisperResponse,
)
from app.telephony import transfer_service
from app.telephony.idempotency import claim_provider_event, derive_idempotency_key
from app.telephony.provider_errors import (
    ProviderConfigurationError,
    ProviderNotFoundError,
    ProviderStateError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.telephony.providers.factory import build as build_adapter
from app.telephony.transfer_service import _safe_whisper_reason

CONTROL_CONTEXT_KEY = "call_control"
PAUSED = "paused"
RESUMED = "in_progress"

DEFAULT_PROVIDER = "twilio"

#: Event types recorded on :class:`TelephonyCallbackEvent`. They are part of
#: the receipt key material, so two different operations can never collide.
EVENT_PAUSE = "call.pause"
EVENT_RESUME = "call.resume"
EVENT_DTMF = "call.dtmf"
EVENT_TRANSFER_CANCEL = "call.transfer.cancel"
EVENT_WHISPER = "call.transfer.whisper"
EVENT_BULK_TRANSFER = "call.transfer.bulk"

TERMINAL_STATUSES = (
    CallStatus.COMPLETED,
    CallStatus.FAILED,
    CallStatus.NO_ANSWER,
    CallStatus.CANCELLED,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class CallControlError(HTTPException):
    """A control action the state machine refuses.

    Raised as an ``HTTPException`` so a route can let it through unchanged;
    the ``detail`` always carries the ``{"code", "message"}`` shape the rest of
    the API uses.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})
        self.code = code


def control_state(call: Call) -> dict[str, Any]:
    """The call-control sub-state stored on the call row."""
    context = dict(call.transfer_context or {})
    value = context.get(CONTROL_CONTEXT_KEY)
    return dict(value) if isinstance(value, dict) else {}


def is_paused(call: Call) -> bool:
    return bool(control_state(call).get("paused"))


def _set_control_state(call: Call, **values: Any) -> None:
    context = dict(call.transfer_context or {})
    state = dict(context.get(CONTROL_CONTEXT_KEY) or {})
    state.update(values)
    context[CONTROL_CONTEXT_KEY] = state
    # Reassign rather than mutate: SQLAlchemy does not track in-place edits of
    # a JSON column, so a mutated dict would silently never be written.
    call.transfer_context = context


class CallControlService:
    """Provider-backed live call control for one tenant and one request."""

    def __init__(
        self,
        session: AsyncSession,
        ctx: TenantContext,
        *,
        provider_name: str = DEFAULT_PROVIDER,
    ) -> None:
        self._session = session
        self._ctx = ctx
        self._provider_name = (provider_name or DEFAULT_PROVIDER).strip().lower() or DEFAULT_PROVIDER

    # ------------------------------------------------------------- plumbing ---

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def _build_adapter(self):
        adapter = build_adapter(self._provider_name)
        # Credentials are checked before the state machine so an unconfigured
        # deployment never reports a pause it could not perform.
        adapter.require_configured()
        return adapter

    def capabilities(self) -> CallControlCapabilities:
        """Capability flags for the active provider, without touching a call."""
        try:
            adapter = self._build_adapter()
        except ProviderConfigurationError:
            # An unconfigured provider confirms nothing, so every control is
            # reported unavailable rather than offered and refused.
            return CallControlCapabilities()
        caps = adapter.capabilities()
        voice = bool(caps.allows("voice"))
        return CallControlCapabilities(
            pause=bool(caps.allows_control("supports_hold")),
            resume=bool(caps.allows_control("supports_hold")),
            dtmf=voice,
            transfer_cancel=bool(caps.allows_control("supports_cancel_transfer")),
            whisper=voice,
            bulk_transfer=voice,
        )

    async def _load_call(self, call_id: uuid.UUID, *, environment_id: uuid.UUID) -> Call:
        row = await self._session.scalar(
            select(Call).where(
                Call.id == call_id,
                Call.tenant_id == self._ctx.tenant_id,
                Call.environment_id == environment_id,
            )
        )
        if row is None:
            raise CallControlError(404, "call_not_found", "call not found")
        return row

    async def _claim(
        self,
        *,
        call: Call,
        event_type: str,
        idempotency_key: str | None,
        request_data: dict[str, Any],
        discriminator: str = "",
    ) -> tuple[Any, bool, str]:
        """Claim the durable receipt for one provider side effect.

        Returns ``(event_row, is_duplicate, key)``. A duplicate means the
        effect already happened; the caller must return the stored result and
        must not contact the provider again.
        """
        if not idempotency_key:
            raise CallControlError(
                400,
                "idempotency_key_required",
                "Idempotency-Key is required for a call-control side effect.",
            )
        key = derive_idempotency_key(
            provider=self._provider_name,
            event_type=event_type,
            provider_call_id=call.call_sid,
            explicit_event_id=idempotency_key,
            extra_discriminator=discriminator,
        )
        claim = await claim_provider_event(
            self._session,
            tenant_id=self._ctx.tenant_id,
            environment_id=call.environment_id,
            provider=self._provider_name,
            provider_event_id=key,
            event_type=event_type,
            provider_call_id=call.call_sid,
            normalized_payload={
                "call_id": str(call.id),
                "event_type": event_type,
                "provider": self._provider_name,
                **request_data,
            },
            call_id=call.id,
        )
        return claim.event_row, bool(claim.is_duplicate), key

    @staticmethod
    def _stored_result(event_row: Any) -> dict[str, Any] | None:
        payload = dict(getattr(event_row, "normalized_payload", None) or {})
        result = payload.get("result")
        return dict(result) if isinstance(result, dict) else None

    @staticmethod
    def _store_result(event_row: Any, result: dict[str, Any]) -> None:
        payload = dict(getattr(event_row, "normalized_payload", None) or {})
        payload["result"] = result
        # Reassign the whole dict: SQLAlchemy does not track in-place mutation
        # of a JSON column, so a mutated dict would never be written.
        event_row.normalized_payload = payload
        event_row.processing_status = "processed"

    async def _emit_call_event(
        self,
        *,
        call: Call,
        event_type: str,
        event_id: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        """Record the control action on the durable call-event table.

        ``app.telephony.call_events.accept`` inserts once per
        ``(provider, event_type, external_id, event_id)`` and scrubs anything
        that looks like a secret before it is stored, so the audit trail never
        becomes a credential leak.
        """
        from app.telephony import call_events

        async def _apply(_session: AsyncSession, _event: Any, envelope: dict) -> tuple[str, bool]:
            return (str(envelope.get("outcome") or "applied"), True)

        try:
            await call_events.accept(
                self._session,
                {
                    "provider": self._provider_name,
                    "event_type": event_type,
                    "external_id": call.call_sid,
                    "event_id": event_id,
                    "tenant_id": str(self._ctx.tenant_id),
                    "observed_at": _now(),
                    "outcome": outcome,
                    "metadata": metadata,
                    "status": outcome,
                },
                _apply,
            )
        except Exception as exc:  # pragma: no cover - defensive
            # A failed audit insert must not turn a completed carrier operation
            # into a client error: the provider side effect already happened.
            log.warning(
                "call_control.event_emit_failed",
                call_id=str(call.id),
                event_type=event_type,
                error_category=type(exc).__name__,
            )

    def _require_live(self, call: Call, *, action: str) -> None:
        if call.status in TERMINAL_STATUSES:
            raise CallControlError(
                409,
                f"call_{action}_not_allowed",
                f"The call is {call.status.value}; control actions require a live call.",
            )
        if call.status is not CallStatus.IN_PROGRESS:
            raise CallControlError(
                409,
                f"call_{action}_not_allowed",
                "The call is not in progress; control actions require an in-progress call.",
            )

    # ---------------------------------------------------------------- pause ---

    async def pause_call(
        self,
        call_id: uuid.UUID,
        *,
        environment_id: uuid.UUID,
        reason: str | None = None,
        hold_message: str | None = None,
        idempotency_key: str | None = None,
    ) -> CallPauseResponse:
        call = await self._load_call(call_id, environment_id=environment_id)
        adapter = self._build_adapter()
        if not adapter.capabilities().allows_control("supports_hold"):
            raise CallControlError(
                501,
                "UNSUPPORTED_CAPABILITY",
                f"Provider '{self._provider_name}' has no verified hold operation for this deployment.",
            )
        # The receipt is claimed *before* the state machine runs. A retry after
        # a network timeout is the same operation, so it must answer with the
        # result already produced rather than with a state error the first
        # attempt would never have given.
        event_row, duplicate, key = await self._claim(
            call=call,
            event_type=EVENT_PAUSE,
            idempotency_key=idempotency_key,
            request_data={"reason": reason, "hold_message": hold_message},
        )
        if duplicate:
            stored = self._stored_result(event_row)
            if stored is None:
                raise CallControlError(
                    409,
                    "idempotency_in_progress",
                    "A pause with this Idempotency-Key has not completed; it requires reconciliation.",
                )
            return CallPauseResponse(**stored)

        self._require_live(call, action="pause")
        if is_paused(call):
            raise CallControlError(409, "call_already_paused", "The call is already on hold.")

        try:
            result = await adapter.hold_call(call.call_sid)
        except ProviderConfigurationError:
            raise
        except UnsupportedCapability as exc:
            raise CallControlError(501, "UNSUPPORTED_CAPABILITY", str(exc)) from exc
        except (ProviderStateError, ProviderNotFoundError) as exc:
            raise CallControlError(409, "call_pause_rejected", str(exc)) from exc
        except ProviderValidationError as exc:
            raise CallControlError(422, "call_pause_rejected", str(exc)) from exc

        _set_control_state(
            call,
            paused=True,
            paused_at=_now_iso(),
            pause_reason=reason,
            last_action="pause",
        )
        response = CallPauseResponse(
            call_id=str(call.id),
            action="pause",
            status=PAUSED,
            provider=self._provider_name,
            provider_status=result.status,
            reason=reason,
            idempotency_key=key,
            replayed=False,
            capabilities=self.capabilities(),
            at=_now_iso(),
        )
        self._store_result(event_row, response.model_dump(mode="json"))
        await self._emit_call_event(
            call=call,
            event_type=EVENT_PAUSE,
            event_id=key,
            outcome="paused",
            metadata={"reason": reason or "", "provider_status": result.status},
        )
        return response

    # --------------------------------------------------------------- resume ---

    async def resume_call(
        self,
        call_id: uuid.UUID,
        *,
        environment_id: uuid.UUID,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> CallResumeResponse:
        call = await self._load_call(call_id, environment_id=environment_id)
        adapter = self._build_adapter()
        if not adapter.capabilities().allows_control("supports_hold"):
            raise CallControlError(
                501,
                "UNSUPPORTED_CAPABILITY",
                f"Provider '{self._provider_name}' has no verified resume operation for this deployment.",
            )
        # See pause_call: the receipt is claimed first, so a retry answers with
        # the stored result instead of a state error.
        event_row, duplicate, key = await self._claim(
            call=call,
            event_type=EVENT_RESUME,
            idempotency_key=idempotency_key,
            request_data={"reason": reason},
        )
        if duplicate:
            stored = self._stored_result(event_row)
            if stored is None:
                raise CallControlError(
                    409,
                    "idempotency_in_progress",
                    "A resume with this Idempotency-Key has not completed; it requires reconciliation.",
                )
            return CallResumeResponse(**stored)

        self._require_live(call, action="resume")
        if not is_paused(call):
            raise CallControlError(
                409, "call_not_paused", "Only a call this service put on hold can be resumed."
            )

        try:
            result = await adapter.resume_call(call.call_sid, "")
        except ProviderConfigurationError:
            raise
        except UnsupportedCapability as exc:
            raise CallControlError(501, "UNSUPPORTED_CAPABILITY", str(exc)) from exc
        except (ProviderStateError, ProviderNotFoundError) as exc:
            raise CallControlError(409, "call_resume_rejected", str(exc)) from exc
        except ProviderValidationError as exc:
            raise CallControlError(422, "call_resume_rejected", str(exc)) from exc

        _set_control_state(
            call,
            paused=False,
            resumed_at=_now_iso(),
            resume_reason=reason,
            last_action="resume",
        )
        response = CallResumeResponse(
            call_id=str(call.id),
            action="resume",
            status=RESUMED,
            provider=self._provider_name,
            provider_status=result.status,
            reason=reason,
            idempotency_key=key,
            replayed=False,
            capabilities=self.capabilities(),
            at=_now_iso(),
        )
        self._store_result(event_row, response.model_dump(mode="json"))
        await self._emit_call_event(
            call=call,
            event_type=EVENT_RESUME,
            event_id=key,
            outcome="resumed",
            metadata={"reason": reason or "", "provider_status": result.status},
        )
        return response

    # ----------------------------------------------------------- dtmf batch ---

    async def send_dtmf_batch(
        self,
        call_id: uuid.UUID,
        *,
        environment_id: uuid.UUID,
        items: list[Any],
    ) -> DtmfBatchResponse:
        """Send several digit runs, each with its own receipt.

        Items are processed independently: a rejected run is reported in its
        own result and the rest are still sent, because the caller can see
        exactly which runs reached the carrier and must not have to retry the
        whole batch to get the ones that failed.
        """
        call = await self._load_call(call_id, environment_id=environment_id)
        adapter = self._build_adapter()
        self._require_live(call, action="dtmf")
        if is_paused(call):
            raise CallControlError(
                409, "call_paused", "A held call cannot receive DTMF; resume it first."
            )

        results: list[DtmfItemResult] = []
        for index, item in enumerate(items):
            digits = str(getattr(item, "digits", "") or "")
            item_key = getattr(item, "idempotency_key", None)
            if not item_key:
                results.append(
                    DtmfItemResult(
                        index=index,
                        digits=digits,
                        status="rejected",
                        idempotency_key="",
                        error="Idempotency-Key is required for every DTMF item.",
                    )
                )
                continue
            event_row, duplicate, key = await self._claim(
                call=call,
                event_type=EVENT_DTMF,
                idempotency_key=item_key,
                request_data={"digits": digits, "index": index},
                discriminator=f"{index}:{digits}",
            )
            if duplicate:
                stored = self._stored_result(event_row)
                if stored is None:
                    results.append(
                        DtmfItemResult(
                            index=index,
                            digits=digits,
                            status="pending",
                            idempotency_key=key,
                            error="This Idempotency-Key is still in progress and requires reconciliation.",
                        )
                    )
                    continue
                results.append(
                    DtmfItemResult(
                        index=index,
                        digits=digits,
                        status=stored.get("status", "sent"),
                        idempotency_key=key,
                        replayed=True,
                        error=stored.get("error"),
                    )
                )
                continue
            try:
                await adapter.send_dtmf(call.call_sid, digits)
            except ProviderValidationError as exc:
                results.append(
                    DtmfItemResult(
                        index=index,
                        digits=digits,
                        status="rejected",
                        idempotency_key=key,
                        error=str(exc),
                    )
                )
                self._store_result(
                    event_row,
                    {"status": "rejected", "digits": digits, "error": str(exc)},
                )
                continue
            except (ProviderStateError, ProviderNotFoundError, UnsupportedCapability) as exc:
                results.append(
                    DtmfItemResult(
                        index=index,
                        digits=digits,
                        status="failed",
                        idempotency_key=key,
                        error=str(exc),
                    )
                )
                self._store_result(
                    event_row,
                    {"status": "failed", "digits": digits, "error": str(exc)},
                )
                continue
            results.append(
                DtmfItemResult(
                    index=index, digits=digits, status="sent", idempotency_key=key
                )
            )
            self._store_result(event_row, {"status": "sent", "digits": digits})

        await self._emit_call_event(
            call=call,
            event_type=EVENT_DTMF,
            event_id=f"batch:{_now_iso()}",
            outcome="dtmf_batch_completed",
            metadata={"items": len(items), "sent": sum(1 for r in results if r.status == "sent")},
        )
        return DtmfBatchResponse(
            call_id=str(call.id),
            action="dtmf_batch",
            provider=self._provider_name,
            items=results,
            sent=sum(1 for r in results if r.status == "sent"),
            replayed=sum(1 for r in results if r.replayed),
            rejected=sum(1 for r in results if r.status in {"rejected", "failed", "pending"}),
            capabilities=self.capabilities(),
            at=_now_iso(),
        )

    # ------------------------------------------------------- transfer cancel ---

    async def cancel_transfer(
        self,
        call_id: uuid.UUID,
        *,
        environment_id: uuid.UUID,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> TransferCancelResponse:
        call = await self._load_call(call_id, environment_id=environment_id)
        adapter = self._build_adapter()
        if not adapter.capabilities().allows_control("supports_cancel_transfer"):
            raise CallControlError(
                501,
                "UNSUPPORTED_CAPABILITY",
                f"Provider '{self._provider_name}' has no verified transfer-cancel operation for this deployment.",
            )
        # See pause_call: the receipt is claimed first, so a retry answers with
        # the stored result rather than a state error caused by that result.
        event_row, duplicate, key = await self._claim(
            call=call,
            event_type=EVENT_TRANSFER_CANCEL,
            idempotency_key=idempotency_key,
            request_data={"reason": reason, "transfer_state": call.transfer_state.value},
        )
        if duplicate:
            stored = self._stored_result(event_row)
            if stored is None:
                raise CallControlError(
                    409,
                    "idempotency_in_progress",
                    "A transfer cancel with this Idempotency-Key has not completed; it requires reconciliation.",
                )
            return TransferCancelResponse(**stored)

        if call.status in TERMINAL_STATUSES:
            raise CallControlError(
                409, "call_transfer_cancel_not_allowed", "The call is already terminal."
            )
        if call.transfer_state not in TRANSFER_IN_FLIGHT:
            raise CallControlError(
                409,
                "transfer_not_in_flight",
                "No transfer is in flight for this call, so there is nothing to cancel.",
            )

        try:
            result = await adapter.cancel_transfer(call.call_sid)
        except ProviderConfigurationError:
            raise
        except UnsupportedCapability as exc:
            raise CallControlError(501, "UNSUPPORTED_CAPABILITY", str(exc)) from exc
        except (ProviderStateError, ProviderNotFoundError) as exc:
            raise CallControlError(409, "transfer_cancel_rejected", str(exc)) from exc
        except ProviderValidationError as exc:
            raise CallControlError(422, "transfer_cancel_rejected", str(exc)) from exc

        # The carrier accepted the return, so the transfer record must say it
        # did not complete and the call must be live with the AI again.
        await transfer_service.mark_transfer_failed(
            self._session, call, f"cancelled_by_operator: {reason or 'unspecified'}"
        )
        if call.status is CallStatus.TRANSFERRED:
            call.status = CallStatus.IN_PROGRESS
        _set_control_state(call, last_action="transfer_cancel", cancelled_at=_now_iso())

        response = TransferCancelResponse(
            call_id=str(call.id),
            action="transfer_cancel",
            transfer_state=call.transfer_state.value,
            provider=self._provider_name,
            provider_status=result.status,
            reason=reason,
            idempotency_key=key,
            replayed=False,
            capabilities=self.capabilities(),
            at=_now_iso(),
        )
        self._store_result(event_row, response.model_dump(mode="json"))
        await self._emit_call_event(
            call=call,
            event_type=EVENT_TRANSFER_CANCEL,
            event_id=key,
            outcome="transfer_cancelled",
            metadata={"reason": reason or "", "provider_status": result.status},
        )
        return response

    # --------------------------------------------------------------- whisper ---

    async def whisper_to_agent(
        self,
        call_id: uuid.UUID,
        *,
        environment_id: uuid.UUID,
        message: str,
        idempotency_key: str | None = None,
    ) -> TransferWhisperResponse:
        """Speak a briefing to the human leg of an in-flight transfer.

        The text is passed through ``_safe_whisper_reason`` first: it
        originates with an operator but is spoken aloud to staff, so
        credential assignments and phone-digit runs are removed before it
        reaches the carrier. The stored and returned value is the sanitized
        text, never the raw input.
        """
        call = await self._load_call(call_id, environment_id=environment_id)
        adapter = self._build_adapter()
        warm = control_state(call).get("warm_transfer") or dict(
            (call.transfer_context or {}).get("warm_transfer") or {}
        )
        target_leg = str(warm.get("target_call_sid") or "").strip()
        safe_message = _safe_whisper_reason(message)

        # See pause_call: the receipt is claimed before the state machine, so a
        # retry answers with the stored result.
        event_row, duplicate, key = await self._claim(
            call=call,
            event_type=EVENT_WHISPER,
            idempotency_key=idempotency_key,
            request_data={"message": safe_message, "target_leg": target_leg},
        )
        if duplicate:
            stored = self._stored_result(event_row)
            if stored is None:
                raise CallControlError(
                    409,
                    "idempotency_in_progress",
                    "A whisper with this Idempotency-Key has not completed; it requires reconciliation.",
                )
            return TransferWhisperResponse(**stored)

        if call.status in TERMINAL_STATUSES:
            raise CallControlError(
                409, "call_whisper_not_allowed", "The call is already terminal."
            )
        if call.transfer_state not in TRANSFER_IN_FLIGHT:
            raise CallControlError(
                409,
                "transfer_not_in_flight",
                "A whisper is delivered to the human leg of an in-flight transfer; none is in flight.",
            )
        if not target_leg:
            raise CallControlError(
                409,
                "whisper_target_unavailable",
                "This transfer has no separate human leg to whisper to; start a warm transfer first.",
            )
        if not safe_message:
            raise CallControlError(
                422,
                "whisper_empty_after_sanitization",
                "The whisper contained no speakable text once credentials and digit runs were removed.",
            )

        try:
            await adapter.send_whisper_to_leg(target_leg, safe_message)
        except UnsupportedCapability as exc:
            raise CallControlError(501, "UNSUPPORTED_CAPABILITY", str(exc)) from exc
        except (ProviderStateError, ProviderNotFoundError) as exc:
            raise CallControlError(409, "whisper_rejected", str(exc)) from exc
        except ProviderValidationError as exc:
            raise CallControlError(422, "whisper_rejected", str(exc)) from exc

        context = dict(call.transfer_context or {})
        warm_meta = dict(context.get("warm_transfer") or {})
        warm_meta["last_whisper"] = safe_message
        warm_meta["human_briefed"] = True
        context["warm_transfer"] = warm_meta
        call.transfer_context = context

        response = TransferWhisperResponse(
            call_id=str(call.id),
            action="transfer_whisper",
            transfer_state=call.transfer_state.value,
            provider=self._provider_name,
            provider_status="whisper_delivered",
            message=safe_message,
            idempotency_key=key,
            replayed=False,
            capabilities=self.capabilities(),
            at=_now_iso(),
        )
        self._store_result(event_row, response.model_dump(mode="json"))
        await self._emit_call_event(
            call=call,
            event_type=EVENT_WHISPER,
            event_id=key,
            outcome="whisper_delivered",
            metadata={"target_leg_present": True},
        )
        return response

    # ---------------------------------------------------------- bulk transfer ---

    async def bulk_transfer(
        self,
        *,
        environment_id: uuid.UUID,
        items: list[Any],
        batch_key: str | None = None,
    ) -> BulkTransferResponse:
        """Transfer several calls, each with an independent receipt.

        One call failing must not undo the calls that already reached the
        carrier, so each item is attempted on its own and its failure is
        isolated with a rollback of the in-flight session state before the
        next item starts. The response lists every call's outcome.
        """
        import hashlib

        adapter = self._build_adapter()
        if not adapter.capabilities().allows("voice"):
            raise CallControlError(
                501,
                "UNSUPPORTED_CAPABILITY",
                f"Provider '{self._provider_name}' voice calling is not confirmed for this deployment.",
            )
        batch_id = (
            batch_key
            or hashlib.sha256(
                "|".join(
                    [str(self._ctx.tenant_id), _now_iso()]
                    + [str(getattr(item, "call_id", "")) for item in items]
                ).encode("utf-8")
            ).hexdigest()[:32]
        )
        results: list[BulkTransferItemResult] = []
        for index, item in enumerate(items):
            call_id_raw = str(getattr(item, "call_id", "") or "")
            destination = str(getattr(item, "destination", "") or "")
            item_key = getattr(item, "idempotency_key", None)
            try:
                call_uuid = uuid.UUID(call_id_raw)
            except (ValueError, AttributeError):
                results.append(
                    BulkTransferItemResult(
                        call_id=call_id_raw,
                        index=index,
                        status="rejected",
                        destination=destination,
                        error_code="invalid_call_id",
                        error="call_id is not a valid UUID.",
                    )
                )
                continue
            try:
                call = await self._load_call(call_uuid, environment_id=environment_id)
                results.append(
                    await self._transfer_one(
                        call,
                        index=index,
                        destination=destination,
                        reason=getattr(item, "reason", None),
                        idempotency_key=item_key,
                        batch_id=batch_id,
                    )
                )
            except CallControlError as exc:
                detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
                results.append(
                    BulkTransferItemResult(
                        call_id=call_id_raw,
                        index=index,
                        status="failed",
                        destination=destination,
                        error_code=str(detail.get("code") or "transfer_failed"),
                        error=str(detail.get("message") or exc.detail),
                    )
                )
                await self._session.rollback()
            except Exception as exc:
                # An unexpected failure must not abort the remaining calls, and
                # the session may hold a half-applied transfer, so it is reset
                # before the next item.
                results.append(
                    BulkTransferItemResult(
                        call_id=call_id_raw,
                        index=index,
                        status="failed",
                        destination=destination,
                        error_code=type(exc).__name__.lower(),
                        error=str(exc)[:300],
                    )
                )
                await self._session.rollback()

        return BulkTransferResponse(
            batch_id=batch_id,
            action="bulk_transfer",
            provider=self._provider_name,
            items=results,
            total=len(results),
            succeeded=sum(1 for r in results if r.status == "succeeded"),
            replayed=sum(1 for r in results if r.replayed),
            failed=sum(1 for r in results if r.status in {"failed", "rejected"}),
            capabilities=self.capabilities(),
            at=_now_iso(),
        )

    async def _transfer_one(
        self,
        call: Call,
        *,
        index: int,
        destination: str,
        reason: str | None,
        idempotency_key: str | None,
        batch_id: str,
    ) -> BulkTransferItemResult:
        if not idempotency_key:
            raise CallControlError(
                400,
                "idempotency_key_required",
                "Every bulk-transfer item needs its own Idempotency-Key.",
            )
        # See pause_call: the receipt is claimed before the state machine, so
        # retrying a batch replays the stored outcome instead of re-dialling a
        # call that is already in flight because of the first attempt.
        event_row, duplicate, key = await self._claim(
            call=call,
            event_type=EVENT_BULK_TRANSFER,
            idempotency_key=idempotency_key,
            request_data={
                "destination": destination,
                "reason": reason,
                "batch_id": batch_id,
            },
            discriminator=f"bulk:{batch_id}",
        )
        if duplicate:
            stored = self._stored_result(event_row) or {}
            return BulkTransferItemResult(
                call_id=str(call.id),
                index=index,
                status=str(stored.get("status") or "succeeded"),
                transfer_state=stored.get("transfer_state"),
                destination=destination,
                idempotency_key=key,
                replayed=True,
                error_code=stored.get("error_code"),
                error=stored.get("error"),
            )
        if call.status in TERMINAL_STATUSES:
            raise CallControlError(
                409,
                "call_already_terminal",
                f"The call is {call.status.value} and cannot be transferred.",
            )
        if call.transfer_state in TRANSFER_IN_FLIGHT:
            raise CallControlError(
                409, "transfer_already_in_flight", "Another transfer is already in progress."
            )

        result = await transfer_service.request_transfer(
            self._session,
            tenant=self._ctx.tenant,
            call=call,
            destination_override=destination,
            reason=reason or "operator_requested",
        )
        if not result.ok:
            error_code = result.error.value if result.error else "transfer_rejected"
            payload = {
                "status": "failed",
                "error_code": error_code,
                "error": result.message,
                "transfer_state": result.state.value,
            }
            self._store_result(event_row, payload)
            return BulkTransferItemResult(
                call_id=str(call.id),
                index=index,
                status="failed",
                transfer_state=result.state.value,
                destination=destination,
                idempotency_key=key,
                error_code=error_code,
                error=result.message,
            )

        payload = {
            "status": "succeeded",
            "transfer_state": result.state.value,
            "destination": destination,
        }
        self._store_result(event_row, payload)
        await self._emit_call_event(
            call=call,
            event_type=EVENT_BULK_TRANSFER,
            event_id=key,
            outcome="transfer_started",
            metadata={"batch_id": batch_id, "destination_present": bool(destination)},
        )
        return BulkTransferItemResult(
            call_id=str(call.id),
            index=index,
            status="succeeded",
            transfer_state=result.state.value,
            destination=destination,
            idempotency_key=key,
        )


def transfer_in_flight(call: Call) -> bool:
    return call.transfer_state in TRANSFER_IN_FLIGHT


__all__ = [
    "CallControlError",
    "CallControlService",
    "EVENT_BULK_TRANSFER",
    "EVENT_DTMF",
    "EVENT_PAUSE",
    "EVENT_RESUME",
    "EVENT_TRANSFER_CANCEL",
    "EVENT_WHISPER",
    "PAUSED",
    "RESUMED",
    "TERMINAL_STATUSES",
    "control_state",
    "is_paused",
    "transfer_in_flight",
]
