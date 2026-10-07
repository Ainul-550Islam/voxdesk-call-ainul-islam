"""Web Call and Phone Call Test Execution Service (PostgreSQL-backed).

Responsibilities
----------------
1. Manage interactive Web Call test sessions (``mode='web_call'``) backed by
   durable ``TestRun`` rows pinned to an exact ``AgentVersion``.
2. Manage outbound Phone Call test executions (``mode='phone_call'``) backed by
   durable ``TestRun`` rows pinned to an exact ``AgentVersion``.
3. Enforce strict honesty when WebRTC or Telephony carrier infrastructure is
   not configured: persist ``status='not_run'`` or ``status='error'`` with an
   explicit ``error_code`` and ``error_message`` rather than fabricating a
   successful call or fake pass.
"""
from __future__ import annotations

import copy
import os
import re
import time as time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditAction
from app.auth.identity.events import scrub
from app.auth.service import record_audit
from app.core.errors import BadRequestError
from app.db.retell_models import (
    TestRun,
    TestRunModeEnum,
    TestRunStatusEnum,
)
from app.domain.evaluation_models import (
    PhoneCallTestRequest,
    ScorecardStatus,
    TestRunResponse,
    WebCallSessionEventRequest,
    WebCallSessionRequest,
)
from app.services.agent_service import resolve_pinned_agent_version_async
from app.services.evaluation_service import evaluate_test_run
from app.services.simulation_service import (
    _build_run_response,
    _execute_turn_against_pinned_config,
    _extract_system_prompt_and_greeting,
    _interpolate_variables,
    get_test_run_row,
)

_PHONE_RE = re.compile(r"^\+?[1-9]\d{3,14}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(value: uuid.UUID | str, field_name: str = "id") -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {field_name}: {value}") from exc


def is_webrtc_live_configured() -> bool:
    return bool(
        os.environ.get("WEBRTC_SIGNALING_URL")
        or os.environ.get("LIVEKIT_URL")
        or os.environ.get("DAILY_API_KEY")
    )


def is_telephony_carrier_configured() -> bool:
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID")
        or os.environ.get("TELNYX_API_KEY")
        or os.environ.get("VONAGE_API_KEY")
    )


# --------------------------------------------------------- Web Call Test Flow


async def start_web_call_test_session(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    payload: WebCallSessionRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    """Start a version-pinned Web Call test session and persist a durable TestRun."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    pinned = await resolve_pinned_agent_version_async(
        session,
        t_id,
        payload.agent_id,
        payload.agent_version_number,
        agent_kind="voice",
    )

    now = _now()
    correlation_id = f"webcall-{uuid.uuid4().hex[:16]}"
    frozen_snapshot = copy.deepcopy(pinned["config_snapshot"])
    dyn_vars = dict(payload.dynamic_variables or {})
    inline_rules = [r.model_dump(mode="json") for r in payload.evaluation_rules]

    live_webrtc = is_webrtc_live_configured()
    if payload.require_live_webrtc and not live_webrtc:
        run = TestRun(
            id=uuid.uuid4(),
            tenant_id=t_id,
            environment_id=pinned.get("environment_id"),
            agent_id=str(pinned["agent_id"]),
            agent_kind="voice",
            agent_version_id=pinned["version_id"],
            agent_version_number=int(pinned["version_number"]),
            agent_config_hash=str(pinned["config_hash"]),
            pinned_config_snapshot=frozen_snapshot,
            mode=TestRunModeEnum.WEB_CALL.value,
            status=TestRunStatusEnum.NOT_RUN.value,
            is_mock_provider=False,
            provider=str(pinned["provider"]),
            model=str(pinned["model"]),
            correlation_id=correlation_id,
            transcript_snapshot=[],
            events_snapshot=[
                {
                    "event": "web_call_unavailable",
                    "error_code": "WEBRTC_TRANSPORT_NOT_CONFIGURED",
                    "timestamp": now.isoformat(),
                }
            ],
            usage_metadata={"turn_count": 0, "total_tokens": 0},
            latency_metadata={},
            final_output={
                "final_state": "not_run",
                "dynamic_variables": dyn_vars,
                "inline_rules": inline_rules,
                "webrtc_state": "UNAVAILABLE",
            },
            scorecard_summary={
                "status": ScorecardStatus.NOT_RUN.value,
                "overall_score": None,
                "explanation": "WebRTC media transport is not configured; live browser audio session could not be started.",
                "evaluated_at": now.isoformat(),
            },
            error_code="WEBRTC_TRANSPORT_NOT_CONFIGURED",
            error_message="Live WebRTC signaling/media server is not configured in this environment.",
            started_at=now,
            completed_at=now,
            duration_ms=0,
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.flush()
        return await _build_run_response(session, run)

    transcript: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = [
        {
            "event": "web_call_started",
            "transport": "webrtc_live" if live_webrtc else "simulated_web_audio",
            "agent_version_number": int(pinned["version_number"]),
            "timestamp": now.isoformat(),
        }
    ]
    _, greeting = _extract_system_prompt_and_greeting(frozen_snapshot)
    if greeting:
        transcript.append(
            {
                "role": "assistant",
                "content": _interpolate_variables(greeting, dyn_vars),
                "turn_index": 0,
                "intent": "greeting",
                "tool_calls": [],
                "latency_ms": 5,
                "timestamp": now.isoformat(),
            }
        )

    run = TestRun(
        id=uuid.uuid4(),
        tenant_id=t_id,
        environment_id=pinned.get("environment_id"),
        agent_id=str(pinned["agent_id"]),
        agent_kind="voice",
        agent_version_id=pinned["version_id"],
        agent_version_number=int(pinned["version_number"]),
        agent_config_hash=str(pinned["config_hash"]),
        pinned_config_snapshot=frozen_snapshot,
        mode=TestRunModeEnum.WEB_CALL.value,
        status=TestRunStatusEnum.RUNNING.value,
        is_mock_provider=not live_webrtc,
        provider=str(pinned["provider"]),
        model=str(pinned["model"]),
        correlation_id=correlation_id,
        transcript_snapshot=transcript,
        events_snapshot=events,
        usage_metadata={"turn_count": len(transcript), "total_tokens": 0},
        latency_metadata={"avg_turn_latency_ms": 5 if transcript else 0},
        final_output={
            "final_state": "listening",
            "webrtc_state": "LISTENING",
            "dynamic_variables": dyn_vars,
            "variables": dict(dyn_vars),
            "inline_rules": inline_rules,
        },
        scorecard_summary={},
        started_at=now,
        created_by=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    await session.flush()

    if payload.initial_user_utterance and payload.initial_user_utterance.strip():
        return await send_web_call_event(
            session,
            t_id,
            run.id,
            WebCallSessionEventRequest(
                event="user_utterance",
                text=payload.initial_user_utterance.strip(),
            ),
            allow_mock_fallback=payload.allow_mock_fallback,
            actor_user_id=actor_user_id,
        )

    return await _build_run_response(session, run)


async def send_web_call_event(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
    payload: WebCallSessionEventRequest,
    *,
    allow_mock_fallback: bool = True,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    """Process an interactive event (utterance, interrupt, dtmf, stop/complete) on an active Web Call TestRun."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    run = await get_test_run_row(session, t_id, run_id)
    now = _now()

    transcript = list(run.transcript_snapshot or [])
    events = list(run.events_snapshot or [])
    final_output = dict(run.final_output or {})
    dyn_vars = dict(final_output.get("dynamic_variables") or {})
    runtime_vars = dict(final_output.get("variables") or {})

    if payload.event == "interrupt":
        events.append({"event": "barge_in_interrupt", "timestamp": now.isoformat()})
        final_output["webrtc_state"] = "INTERRUPTED"
        final_output["final_state"] = "interrupted"
        run.events_snapshot = events
        run.final_output = final_output
        run.updated_at = now
        await session.flush()
        return await _build_run_response(session, run)

    if payload.event == "dtmf":
        digits = str(payload.dtmf_digits or "").strip()
        events.append({"event": "dtmf_received", "digits": digits, "timestamp": now.isoformat()})
        runtime_vars["last_dtmf"] = digits
        final_output["variables"] = runtime_vars
        run.events_snapshot = events
        run.final_output = final_output
        run.updated_at = now
        await session.flush()
        return await _build_run_response(session, run)

    if payload.event == "user_utterance":
        user_text = str(payload.text or "").strip()
        if not user_text:
            raise BadRequestError("Web call 'user_utterance' event requires non-empty 'text'.")
        u_idx = len(transcript)
        transcript.append(
            {
                "role": "user",
                "content": user_text,
                "turn_index": u_idx,
                "timestamp": now.isoformat(),
            }
        )
        turn_out = await _execute_turn_against_pinned_config(
            pinned_config=dict(run.pinned_config_snapshot or {}),
            agent_name=str(run.pinned_config_snapshot.get("name") or run.agent_id),
            provider=run.provider,
            model=run.model,
            turn_index=u_idx + 1,
            user_text=user_text,
            conversation_history=transcript[:-1],
            dynamic_variables=dyn_vars,
            runtime_variables=runtime_vars,
            allow_mock_fallback=allow_mock_fallback,
        )
        a_idx = len(transcript)
        transcript.append(
            {
                "role": "assistant",
                "content": turn_out["reply"],
                "turn_index": a_idx,
                "intent": turn_out["intent"],
                "tool_calls": turn_out["tool_calls"],
                "latency_ms": turn_out["latency_ms"],
                "timestamp": _now().isoformat(),
            }
        )
        events.extend(turn_out["events"])
        events.append(
            {
                "event": "assistant_spoke",
                "turn_index": a_idx,
                "latency_ms": turn_out["latency_ms"],
                "timestamp": _now().isoformat(),
            }
        )
        runtime_vars.update(turn_out["variables_delta"])
        final_output["variables"] = runtime_vars
        final_output["webrtc_state"] = "SPEAKING"
        final_output["last_reply"] = turn_out["reply"]
        if turn_out["transferred"]:
            final_output["transferred"] = True
            final_output["transfer_destination"] = turn_out["transfer_destination"]
            final_output["final_state"] = "transferred"

        usage = dict(run.usage_metadata or {})
        usage["prompt_tokens"] = int(usage.get("prompt_tokens", 0)) + turn_out["prompt_tokens"]
        usage["completion_tokens"] = int(usage.get("completion_tokens", 0)) + turn_out["completion_tokens"]
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        usage["turn_count"] = len(transcript)

        lat_list = [int(t.get("latency_ms", 0)) for t in transcript if t.get("latency_ms") is not None]
        run.transcript_snapshot = transcript
        run.events_snapshot = events
        run.usage_metadata = usage
        run.latency_metadata = {
            "avg_turn_latency_ms": round(sum(lat_list) / len(lat_list), 2) if lat_list else 0,
            "max_turn_latency_ms": max(lat_list) if lat_list else 0,
            "p95_ms": max(lat_list) if lat_list else 0,
        }
        run.final_output = final_output
        run.updated_at = _now()
        await session.flush()
        return await _build_run_response(session, run)

    if payload.event in ("stop", "complete"):
        final_output["webrtc_state"] = "ENDED"
        if final_output.get("final_state") in (None, "listening", "speaking", "interrupted"):
            final_output["final_state"] = "completed"
        events.append({"event": "web_call_ended", "timestamp": now.isoformat()})
        run.events_snapshot = events
        run.final_output = final_output
        run.completed_at = now
        if run.started_at:
            started_utc = (
                run.started_at
                if run.started_at.tzinfo is not None
                else run.started_at.replace(tzinfo=timezone.utc)
            )
            run.duration_ms = max(1, int((now - started_utc).total_seconds() * 1000))
        await evaluate_test_run(
            session,
            t_id,
            run,
            inline_rules=final_output.get("inline_rules"),
            allow_mock_judge=allow_mock_fallback,
            actor_user_id=actor_user_id,
        )
        await session.flush()
        return await _build_run_response(session, run)

    return await _build_run_response(session, run)


# ------------------------------------------------------- Phone Call Test Flow


async def initiate_phone_call_test(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    payload: PhoneCallTestRequest,
    *,
    actor_user_id: uuid.UUID | None = None,
) -> TestRunResponse:
    """Execute or honestly report an outbound Phone Call test pinned to an AgentVersion."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    clean_to = payload.to_number.strip()
    if not _PHONE_RE.match(clean_to):
        raise BadRequestError(
            f"Invalid destination phone number {payload.to_number!r}. Must be E.164 format (e.g. +14155550100)."
        )

    pinned = await resolve_pinned_agent_version_async(
        session,
        t_id,
        payload.agent_id,
        payload.agent_version_number,
        agent_kind="voice",
    )

    now = _now()
    correlation_id = f"phonecall-{uuid.uuid4().hex[:16]}"
    frozen_snapshot = copy.deepcopy(pinned["config_snapshot"])
    dyn_vars = dict(payload.dynamic_variables or {})
    inline_rules = [r.model_dump(mode="json") for r in payload.evaluation_rules]
    carrier_ready = is_telephony_carrier_configured()

    # If live carrier is required OR no scripted turns were provided and no carrier is configured,
    # record an honest NOT_RUN / ERROR TestRun rather than faking a PSTN call!
    if not carrier_ready and (payload.require_live_carrier or not payload.scripted_user_turns):
        status_val = (
            TestRunStatusEnum.ERROR.value
            if payload.require_live_carrier
            else TestRunStatusEnum.NOT_RUN.value
        )
        run = TestRun(
            id=uuid.uuid4(),
            tenant_id=t_id,
            environment_id=pinned.get("environment_id"),
            agent_id=str(pinned["agent_id"]),
            agent_kind="voice",
            agent_version_id=pinned["version_id"],
            agent_version_number=int(pinned["version_number"]),
            agent_config_hash=str(pinned["config_hash"]),
            pinned_config_snapshot=frozen_snapshot,
            mode=TestRunModeEnum.PHONE_CALL.value,
            status=status_val,
            is_mock_provider=False,
            provider=str(pinned["provider"]),
            model=str(pinned["model"]),
            correlation_id=correlation_id,
            transcript_snapshot=[],
            events_snapshot=[
                {
                    "event": "carrier_unavailable",
                    "to_number": clean_to,
                    "from_number": payload.from_number,
                    "error_code": "TELEPHONY_PROVIDER_UNAVAILABLE",
                    "timestamp": now.isoformat(),
                }
            ],
            usage_metadata={"turn_count": 0, "total_tokens": 0},
            latency_metadata={},
            final_output={
                "final_state": status_val,
                "to_number": clean_to,
                "from_number": payload.from_number,
                "carrier_configured": False,
                "dynamic_variables": dyn_vars,
                "inline_rules": inline_rules,
            },
            scorecard_summary={
                "status": (
                    ScorecardStatus.EVALUATION_ERROR.value
                    if payload.require_live_carrier
                    else ScorecardStatus.NOT_RUN.value
                ),
                "overall_score": None,
                "explanation": (
                    "Telephony carrier credentials (Twilio/Telnyx/SIP) are not configured in this environment; "
                    "outbound phone call was not dialed."
                ),
                "evaluated_at": now.isoformat(),
            },
            error_code="TELEPHONY_PROVIDER_UNAVAILABLE",
            error_message=(
                "Telephony carrier credentials are not configured in this environment. "
                "Configure a verified telephony provider or supply scripted_user_turns with require_live_carrier=false for sandbox loop testing."
            ),
            started_at=now,
            completed_at=now,
            duration_ms=0,
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.flush()
        await record_audit(
            session,
            action=AuditAction.GOVERNANCE_EVENT,
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            actor_email="system",
            detail=scrub(
                {
                    "event": "phone_call_test.not_run",
                    "operation": "phone_call_test.not_run",
                    "resource_id": str(run.id),
                    "to_number": clean_to,
                    "agent_id": run.agent_id,
                    "agent_version_number": run.agent_version_number,
                    "error_code": run.error_code,
                }
            ),
            commit=False,
        )
        return await _build_run_response(session, run)

    # Execute scripted telephony loop against the pinned agent version
    from app.services.simulation_service import execute_pinned_simulation_run

    input_turns = [{"role": "user", "content": t} for t in payload.scripted_user_turns if t.strip()]
    run_resp = await execute_pinned_simulation_run(
        session,
        t_id,
        agent_id=payload.agent_id,
        agent_version_number=payload.agent_version_number,
        agent_kind="voice",
        mode=TestRunModeEnum.PHONE_CALL.value,
        input_turns=input_turns,
        dynamic_variables={**dyn_vars, "to_number": clean_to, "from_number": payload.from_number or "+15550001111"},
        inline_rules=inline_rules,
        allow_mock_fallback=not payload.require_live_carrier,
        actor_user_id=actor_user_id,
    )
    return run_resp
