"""Real PSTN latency probe: a scripted caller dials the live agent.

How it works, end to end, with no simulated step:

1. **The call is placed for real.** The probe calls the Twilio REST API
   (``client.calls.create``) from ``LIVE_PROOF_CALLER_ID`` to
   ``LIVE_PROOF_TEST_NUMBER`` -- the agent's own number, so the leg that answers
   is this deployment's AI pipeline.
2. **The caller is scripted.** The outbound leg's TwiML speaks
   ``LIVE_PROOF_SCRIPT`` line by line with ``LIVE_PROOF_TURN_GAP_SECONDS`` of
   silence between lines, so the caller audio crossing the PSTN is deterministic
   while the callee-side answers are produced by the live STT -> LLM -> TTS
   chain. Twilio's own <Say> voice is the audio source; nothing is injected by
   this process.
3. **The measurements come from the pipeline.** The inbound webhook creates the
   ``Call`` row and the media stream runs ``run_voice_agent``, whose
   ``LatencyObserver`` persists ``CallLatencyStat`` (per-stage TTFB and E2E
   percentiles) and whose turn writer persists per-utterance ``Turn`` rows. The
   probe *reads those rows back*; it never times the call itself and never
   fabricates a metric.
4. **The recording is hashed.** ``record=true`` makes Twilio record the leg; the
   probe downloads the recording and writes its SHA-256 into the evidence file.
5. **Spend is capped.** :class:`~app.telephony.live_proof.cost_guard.CostGuard`
   is checked on every poll tick; crossing either cap hangs the call up and marks
   the run aborted.

Refusal rules: without ``TWILIO_ACCOUNT_SID``, ``TWILIO_AUTH_TOKEN``,
``TWILIO_PHONE_NUMBER``, ``DEEPGRAM_API_KEY``, ``ELEVENLABS_API_KEY`` and
``OPENAI_API_KEY`` the probe raises :class:`ProviderNotConfigured` before it
places any call. It returns no metrics, writes no evidence and creates no row
that claims a run happened.

CLI::

    python -m app.telephony.live_proof.pstn_probe --minutes 1
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from xml.sax.saxutils import escape

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Call, CallStatus, Turn
from app.telephony.live_proof.cost_guard import CostGuard, CostGuardExceeded, CostReport
from app.telephony.live_proof.evidence_writer import (
    EvidenceBundle,
    ProviderCallLedger,
    TurnMeasurement,
    summarise,
    write_evidence,
)
from app.telephony.provider_errors import TelephonyError

log = structlog.get_logger()

#: The settings (and their environment names) a real run cannot do without.
REQUIRED_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("twilio_account_sid", "TWILIO_ACCOUNT_SID"),
    ("twilio_auth_token", "TWILIO_AUTH_TOKEN"),
    ("twilio_phone_number", "TWILIO_PHONE_NUMBER"),
    ("deepgram_api_key", "DEEPGRAM_API_KEY"),
    ("elevenlabs_api_key", "ELEVENLABS_API_KEY"),
    ("openai_api_key", "OPENAI_API_KEY"),
)

#: Terminal call states: the probe stops polling when it sees one of these.
TERMINAL_STATUSES = frozenset(
    {
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.NO_ANSWER,
        CallStatus.CANCELLED,
    }
)


class ProviderNotConfigured(TelephonyError):
    """A provider credential is missing. No call is placed and no metric exists."""

    code = "provider_not_configured"
    status_code = 503

    def __init__(self, message: str, *, missing: tuple[str, ...] = ()) -> None:
        super().__init__(message, provider="live_proof")
        self.missing = missing

    def as_dict(self) -> dict:
        payload = super().as_dict()
        payload["missing"] = list(self.missing)
        return payload


@dataclass(frozen=True)
class LiveProofResult:
    """What a live run actually produced. Empty fields stay empty."""

    run_id: str
    tenant_id: str
    status: str
    provider_set: tuple[str, ...]
    provider_fakes: bool
    call_sid: str = ""
    call_id: str = ""
    turns: int = 0
    e2e_p50_ms: float | None = None
    e2e_p95_ms: float | None = None
    e2e_p99_ms: float | None = None
    stt_ttfb_p50_ms: float | None = None
    llm_ttfb_p50_ms: float | None = None
    tts_ttfb_p50_ms: float | None = None
    cost_usd: float = 0.0
    minutes: float = 0.0
    evidence_path: str = ""
    evidence_sha256: str = ""
    recording_sha256: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["provider_set"] = list(self.provider_set)
        payload["notes"] = list(self.notes)
        for key in ("started_at", "finished_at"):
            value = payload.get(key)
            payload[key] = value.isoformat() if isinstance(value, datetime) else value
        return payload


def missing_provider_settings() -> tuple[str, ...]:
    """Environment names of the provider settings that are absent."""
    missing: list[str] = []
    for attribute, env_name in REQUIRED_PROVIDERS:
        value = getattr(settings, attribute, "") or ""
        if not str(value).strip():
            missing.append(env_name)
    if not (settings.live_proof_test_number or "").strip():
        missing.append("LIVE_PROOF_TEST_NUMBER")
    return tuple(missing)


def require_providers() -> None:
    """Raise :class:`ProviderNotConfigured` unless every provider is configured."""
    missing = missing_provider_settings()
    if missing:
        raise ProviderNotConfigured(
            "live PSTN proof needs real provider credentials; missing " + ", ".join(missing),
            missing=missing,
        )


def caller_twiml(lines: list[str], *, gap_seconds: float) -> str:
    """The scripted caller's TwiML: speak each line, then leave the agent room."""
    if not lines:
        raise ProviderNotConfigured("LIVE_PROOF_SCRIPT is empty; there is no caller audio to speak")
    if gap_seconds <= 0:
        raise ProviderNotConfigured("LIVE_PROOF_TURN_GAP_SECONDS must be positive")
    pause = f'<Pause length="{int(round(gap_seconds))}"/>'
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]
    for line in lines:
        parts.append(f'<Say voice="Polly.Joanna">{escape(line)}</Say>')
        parts.append(pause)
    parts.append("<Hangup/></Response>")
    return "".join(parts)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(func, *args, **kwargs)


class _TwilioClient:
    """Thin synchronous Twilio REST wrapper, run off the event loop."""

    def __init__(self, account_sid: str, auth_token: str) -> None:
        from twilio.rest import Client

        self._client = Client(account_sid, auth_token)
        self.account_sid = account_sid
        self.auth_token = auth_token

    async def place_call(self, *, to: str, from_: str, twiml: str, record: bool) -> dict[str, Any]:
        def _create() -> Any:
            return self._client.calls.create(
                to=to,
                from_=from_,
                twiml=twiml,
                record=record,
                timeout=60,
                machine_detection="Disable",
            )

        call = await _to_thread(_create)
        return {
            "sid": str(call.sid),
            "status": str(call.status),
            "to": str(call.to),
            "from": str(call.from_),
            "date_created": str(call.date_created),
        }

    async def hangup(self, call_sid: str) -> dict[str, Any]:
        def _update() -> Any:
            return self._client.calls(call_sid).update(status="completed")

        call = await _to_thread(_update)
        return {"sid": str(call.sid), "status": str(call.status)}

    async def call_status(self, call_sid: str) -> str:
        def _fetch() -> Any:
            return self._client.calls(call_sid).fetch()

        call = await _to_thread(_fetch)
        return str(call.status)

    async def latest_recording(self, call_sid: str) -> dict[str, Any] | None:
        def _list() -> Any:
            records = self._client.recordings.list(call_sid=call_sid, limit=1)
            return records[0] if records else None

        recording = await _to_thread(_list)
        if recording is None:
            return None
        return {"sid": str(recording.sid), "uri": str(recording.uri), "duration": str(recording.duration)}

    async def download_recording(self, uri: str) -> bytes:
        """Download the recording bytes with the account's own credentials."""
        import httpx

        url = f"https://api.twilio.com{uri.replace('.json', '.wav')}"
        async with httpx.AsyncClient(timeout=30.0, auth=(self.account_sid, self.auth_token)) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content


async def _find_call(
    session, *, from_number: str, to_number: str, since: datetime
) -> Call | None:
    """The inbound call this probe created, found by its real numbers and time."""
    result = await session.execute(
        select(Call)
        .where(
            Call.from_number == from_number,
            Call.to_number == to_number,
            Call.started_at >= since,
        )
        .order_by(Call.started_at.desc())
    )
    return result.scalars().first()


async def _persist_run(
    session_factory: Callable[[], Any],
    *,
    run_id: str,
    tenant_id: str,
    values: dict[str, Any],
    create: bool,
) -> None:
    """Create or update the durable ``live_proof_runs`` row for this run."""
    from app.db.live_proof_models import LiveProofRun

    async with session_factory() as session:
        row = await session.get(LiveProofRun, uuid.UUID(run_id))
        if row is None:
            if not create:
                log.warning("live_proof.row_missing", run_id=run_id)
                return
            row = LiveProofRun(
                id=uuid.UUID(run_id),
                tenant_id=uuid.UUID(tenant_id),
                status=str(values.get("status") or "queued"),
                provider_set=str(values.get("provider_set") or ""),
                created_at=_now(),
            )
            session.add(row)
        for field_name, value in values.items():
            if field_name in {"id", "tenant_id", "created_at"}:
                continue
            setattr(row, field_name, value)
        await session.commit()


def _turn_measurements(rows: list[Turn]) -> list[TurnMeasurement]:
    measurements = []
    for index, row in enumerate(rows, 1):
        speaker = getattr(row.speaker, "value", row.speaker)
        measurements.append(
            TurnMeasurement(
                turn_index=index,
                speaker=str(speaker),
                latency_ms=float(row.latency_ms) if row.latency_ms is not None else None,
                text_chars=len(row.text or ""),
                created_at=row.created_at.isoformat() if row.created_at else "",
            )
        )
    return measurements


async def run_pstn_probe(
    *,
    tenant_id: str,
    run_id: str | None = None,
    to_number: str = "",
    from_number: str = "",
    session_factory: Callable[[], Any] | None = None,
    evidence_dir: str | None = None,
    cost_guard: CostGuard | None = None,
    script: list[str] | None = None,
    max_wait_seconds: float | None = None,
    twilio_client: _TwilioClient | None = None,
    ledger: ProviderCallLedger | None = None,
) -> LiveProofResult:
    """Place one real PSTN call and record what the live pipeline measured.

    ``twilio_client`` exists so a test can prove the refusal path without a
    network; when it is supplied the provider ledger still records what really
    happened, so a substitute client can never produce ``provider_fakes=false``.

    ``ledger`` lets the HTTP layer read the same provider ledger the evidence
    file was built from, so the API and the artifact cannot disagree.
    """
    require_providers()

    if session_factory is None:
        from app.db.session import get_sessionmaker

        session_factory = get_sessionmaker()

    run_uuid = run_id or str(uuid.uuid4())
    destination = (to_number or settings.live_proof_test_number or "").strip()
    caller_id = (from_number or settings.live_proof_caller_id or settings.twilio_phone_number or "").strip()
    if not destination:
        raise ProviderNotConfigured("LIVE_PROOF_TEST_NUMBER is required", missing=("LIVE_PROOF_TEST_NUMBER",))
    if not caller_id:
        raise ProviderNotConfigured("a caller id is required", missing=("TWILIO_PHONE_NUMBER",))

    lines = list(script if script is not None else settings.live_proof_script_lines)
    gap = float(settings.live_proof_turn_gap_seconds)
    guard = cost_guard or CostGuard.from_settings()
    directory = evidence_dir or settings.live_proof_evidence_dir
    wait_seconds = float(max_wait_seconds or settings.live_proof_timeout_seconds)
    poll_seconds = float(settings.live_proof_poll_interval_seconds)

    ledger = ledger if ledger is not None else ProviderCallLedger()
    notes: list[str] = [
        "cost_usd is elapsed PSTN minutes multiplied by LIVE_PROOF_USD_PER_MINUTE "
        "(an operator-configured budget rate), not a provider invoice",
        "latency values are read back from the CallLatencyStat row persisted by "
        "the voice pipeline's LatencyObserver",
        "per-turn rows are the Turn rows persisted by the same call",
    ]

    client = twilio_client or _TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
    started_at = _now()
    provider_set = ("twilio", settings.stt_provider, settings.tts_provider, "openai")

    await _persist_run(
        session_factory,
        run_id=run_uuid,
        tenant_id=tenant_id,
        create=True,
        values={
            "status": "running",
            "provider_set": ",".join(provider_set),
            "started_at": started_at,
            "requested_to_number": destination,
        },
    )

    call_sid = ""
    internal_call_id = ""
    abort_reason = ""
    stage_metrics: dict[str, dict[str, Any]] = {}
    turns: list[TurnMeasurement] = []
    recording_sha256 = ""
    bundle: EvidenceBundle | None = None
    cost: CostReport | None = None

    try:
        guard.start()
        guard.ensure_within_cap()
        placement = await client.place_call(
            to=destination,
            from_=caller_id,
            twiml=caller_twiml(lines, gap_seconds=gap),
            record=True,
        )
        call_sid = placement["sid"]
        ledger.record(
            provider="twilio",
            stage="telephony",
            operation="calls.create",
            real=twilio_client is None,
            status=placement["status"],
            basis="Twilio REST response (call SID issued by the carrier)",
            detail={"to": destination, "from": caller_id, "recording": True},
        )
        if twilio_client is not None:
            notes.append("a substitute Twilio client was injected; provider honesty is therefore false")

        deadline = _now() + timedelta(seconds=wait_seconds)
        latency_stat = None
        call_row: Call | None = None
        while _now() < deadline:
            cost = guard.tick()
            async with session_factory() as session:
                call_row = call_row or await _find_call(
                    session, from_number=caller_id, to_number=destination, since=started_at
                )
                if call_row is not None:
                    internal_call_id = str(call_row.id)
                    from app.db.telephony_models import CallLatencyStat

                    latency_stat = await session.scalar(
                        select(CallLatencyStat).where(CallLatencyStat.call_id == call_row.id)
                    )
                    if call_row.status in TERMINAL_STATUSES and latency_stat is not None:
                        break
                    if call_row.status in TERMINAL_STATUSES:
                        # Terminal but the pipeline persisted nothing: keep
                        # waiting until the deadline for the stat row, because
                        # the observer writes it at stream teardown.
                        pass
            await asyncio.sleep(poll_seconds)
        else:
            abort_reason = "timeout"
            notes.append(f"no terminal call with latency data within {wait_seconds:.0f}s")

        cost = guard.stop()

        if internal_call_id and not abort_reason:
            async with session_factory() as session:
                from app.db.telephony_models import CallLatencyStat

                latency_stat = await session.scalar(
                    select(CallLatencyStat).where(CallLatencyStat.call_id == uuid.UUID(internal_call_id))
                )
                turn_rows = (
                    await session.execute(
                        select(Turn).where(Turn.call_id == uuid.UUID(internal_call_id)).order_by(Turn.created_at)
                    )
                ).scalars().all()
                call_row = await session.get(Call, uuid.UUID(internal_call_id))

            turns = _turn_measurements(list(turn_rows))
            caller_turns = [turn for turn in turns if turn.speaker.lower() in {"caller", "user"}]
            agent_turns = [turn for turn in turns if turn.speaker.lower() in {"agent", "assistant", "bot"}]

            if caller_turns:
                ledger.record(
                    provider=settings.stt_provider,
                    stage="stt",
                    operation="transcription",
                    real=True,
                    basis="persisted Turn rows for caller speech (produced by the STT service)",
                    detail={"turns": len(caller_turns)},
                )
            if call_row is not None and (call_row.llm_used or "").strip():
                ledger.record(
                    provider="openai",
                    stage="llm",
                    operation="completion",
                    real=True,
                    basis="Call.llm_used persisted by the pipeline's LLM service",
                    detail={"model": call_row.llm_used},
                )
            if latency_stat is not None and (latency_stat.tts_ttfb_p50_ms is not None or agent_turns):
                ledger.record(
                    provider=settings.tts_provider,
                    stage="tts",
                    operation="synthesis",
                    real=True,
                    basis="LatencyObserver TTS TTFB samples and persisted agent turns",
                    detail={"turns": len(agent_turns)},
                )

            stage_metrics = {
                "e2e": {
                    "p50_ms": latency_stat.e2e_p50_ms if latency_stat else None,
                    "p95_ms": latency_stat.e2e_p95_ms if latency_stat else None,
                    "p99_ms": latency_stat.e2e_p99_ms if latency_stat else None,
                    "samples": int(latency_stat.turns) if latency_stat else 0,
                },
                "stt": {
                    "p50_ms": latency_stat.stt_ttfb_p50_ms if latency_stat else None,
                    "p95_ms": latency_stat.stt_ttfb_p95_ms if latency_stat else None,
                    "p99_ms": None,
                    "samples": int(latency_stat.turns) if latency_stat else 0,
                },
                "llm": {
                    "p50_ms": latency_stat.llm_ttfb_p50_ms if latency_stat else None,
                    "p95_ms": latency_stat.llm_ttfb_p95_ms if latency_stat else None,
                    "p99_ms": None,
                    "samples": int(latency_stat.turns) if latency_stat else 0,
                },
                "tts": {
                    "p50_ms": latency_stat.tts_ttfb_p50_ms if latency_stat else None,
                    "p95_ms": latency_stat.tts_ttfb_p95_ms if latency_stat else None,
                    "p99_ms": None,
                    "samples": int(latency_stat.turns) if latency_stat else 0,
                },
                "turn_latency": summarise([turn.latency_ms for turn in turns if turn.latency_ms is not None]),
            }

            if not any(stage_metrics[stage]["samples"] for stage in ("e2e",)):
                abort_reason = "no_latency_samples"
                notes.append("the call finished but the pipeline persisted no E2E latency samples")

            recording = await client.latest_recording(call_sid)
            if recording is None:
                notes.append("Twilio returned no recording for this call")
            else:
                try:
                    audio = await client.download_recording(recording["uri"])
                    recording_sha256 = hashlib.sha256(audio).hexdigest()
                    ledger.record(
                        provider="twilio",
                        stage="evidence",
                        operation="recording.download",
                        real=twilio_client is None,
                        basis="carrier-hosted recording bytes hashed locally",
                        detail={"bytes": len(audio), "recording_sid": recording["sid"]},
                    )
                except Exception as exc:  # noqa: BLE001 - a missing recording is reported, not hidden
                    notes.append(f"recording download failed: {type(exc).__name__}")

    except CostGuardExceeded as exc:
        abort_reason = f"cost_cap:{exc.cap}"
        notes.append(exc.message)
        cost = guard.stop()
        if call_sid:
            try:
                await client.hangup(call_sid)
                notes.append("the call was hung up because the spend cap was reached")
            except Exception as hangup_exc:  # noqa: BLE001 - reported, never swallowed silently
                notes.append(f"hangup after cost cap failed: {type(hangup_exc).__name__}")
    except TelephonyError as exc:
        abort_reason = exc.code
        notes.append(exc.message)
        cost = guard.stop()
    except Exception as exc:  # noqa: BLE001 - the run must fail loudly, with the reason recorded
        abort_reason = f"error:{type(exc).__name__}"
        notes.append(str(exc)[:300])
        cost = guard.stop()

    finished_at = _now()
    cost = cost or guard.stop()

    status = "failed"
    if not abort_reason and stage_metrics.get("e2e", {}).get("samples"):
        status = "succeeded" if not ledger.provider_fakes else "unproven"

    result_kwargs: dict[str, Any] = {
        "run_id": run_uuid,
        "tenant_id": str(tenant_id),
        "status": status,
        "provider_set": provider_set,
        "provider_fakes": ledger.provider_fakes,
        "call_sid": call_sid,
        "call_id": internal_call_id,
        "turns": len(turns),
        "e2e_p50_ms": stage_metrics.get("e2e", {}).get("p50_ms"),
        "e2e_p95_ms": stage_metrics.get("e2e", {}).get("p95_ms"),
        "e2e_p99_ms": stage_metrics.get("e2e", {}).get("p99_ms"),
        "stt_ttfb_p50_ms": stage_metrics.get("stt", {}).get("p50_ms"),
        "llm_ttfb_p50_ms": stage_metrics.get("llm", {}).get("p50_ms"),
        "tts_ttfb_p50_ms": stage_metrics.get("tts", {}).get("p50_ms"),
        "cost_usd": round(cost.usd, 6),
        "minutes": round(cost.minutes, 4),
        "started_at": started_at,
        "finished_at": finished_at,
        "error": abort_reason,
        "notes": tuple(notes),
    }

    if ledger.calls:
        try:
            bundle = write_evidence(
                run_id=run_uuid,
                tenant_id=str(tenant_id),
                evidence_dir=directory,
                ledger=ledger,
                turns=turns,
                stage_metrics=stage_metrics,
                recording_sha256=recording_sha256,
                run_meta={
                    "status": status,
                    "call_sid": call_sid,
                    "call_id": internal_call_id,
                    "provider_set": list(provider_set),
                    "cost_report": cost.as_dict(),
                    "caller_script": lines,
                    "turn_gap_seconds": gap,
                    "abort_reason": abort_reason,
                    "to_number": destination,
                },
                notes=notes,
                clock=finished_at,
            )
            result_kwargs["evidence_path"] = bundle.json_path
            result_kwargs["evidence_sha256"] = bundle.sha256
        except Exception as exc:  # noqa: BLE001 - evidence failure invalidates the run
            notes.append(f"evidence write failed: {type(exc).__name__}")
            result_kwargs["status"] = "failed"
            result_kwargs["error"] = result_kwargs["error"] or "evidence_write_failed"
            result_kwargs["notes"] = tuple(notes)

    result_kwargs["provider_fakes"] = ledger.provider_fakes
    result_kwargs["recording_sha256"] = recording_sha256

    await _persist_run(
        session_factory,
        run_id=run_uuid,
        tenant_id=str(tenant_id),
        create=True,
        values={
            "status": result_kwargs["status"],
            "provider_set": ",".join(provider_set),
            "started_at": started_at,
            "finished_at": finished_at,
            "e2e_p50_ms": result_kwargs["e2e_p50_ms"],
            "e2e_p95_ms": result_kwargs["e2e_p95_ms"],
            "e2e_p99_ms": result_kwargs["e2e_p99_ms"],
            "turns": result_kwargs["turns"],
            "cost_usd": result_kwargs["cost_usd"],
            "evidence_path": result_kwargs["evidence_path"],
            "evidence_sha256": result_kwargs["evidence_sha256"],
            "provider_fakes": result_kwargs["provider_fakes"],
            "recording_sha256": recording_sha256,
            "call_sid": call_sid,
            "call_id": internal_call_id,
            "requested_to_number": destination,
            "error": result_kwargs["error"],
        },
    )

    live_result = LiveProofResult(**result_kwargs)
    log.info(
        "live_proof.finished",
        run_id=run_uuid,
        tenant_id=str(tenant_id),
        status=live_result.status,
        provider_fakes=live_result.provider_fakes,
        e2e_p50_ms=live_result.e2e_p50_ms,
        e2e_p95_ms=live_result.e2e_p95_ms,
        cost_usd=live_result.cost_usd,
        error=live_result.error,
    )
    return live_result


async def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", default="", help="agent number to dial (default LIVE_PROOF_TEST_NUMBER)")
    parser.add_argument("--from", dest="from_", default="", help="caller id (default LIVE_PROOF_CALLER_ID)")
    parser.add_argument("--tenant-id", default="", help="tenant that owns the agent (required)")
    parser.add_argument("--minutes", type=float, default=0.0, help="cap minutes for this run")
    parser.add_argument("--max-usd", type=float, default=0.0, help="cap spend for this run")
    parser.add_argument("--evidence-dir", default="", help="where evidence lands")
    args = parser.parse_args(argv)

    if not args.tenant_id:
        print("--tenant-id is required: a live run must be attributable to a tenant", file=sys.stderr)
        return 2

    guard = CostGuard(
        max_minutes=args.minutes or float(settings.live_proof_max_minutes),
        max_usd=args.max_usd or float(settings.live_proof_max_usd),
        usd_per_minute=float(settings.live_proof_usd_per_minute),
    )
    try:
        result = await run_pstn_probe(
            tenant_id=args.tenant_id,
            to_number=args.to,
            from_number=args.from_,
            evidence_dir=args.evidence_dir or None,
            cost_guard=guard,
        )
    except ProviderNotConfigured as exc:
        print(json.dumps(exc.as_dict(), indent=2), file=sys.stderr)
        return 2

    print(json.dumps(result.as_dict(), indent=2, default=str))
    return 0 if result.status == "succeeded" else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_cli(argv))


if __name__ == "__main__":
    raise SystemExit(main())
