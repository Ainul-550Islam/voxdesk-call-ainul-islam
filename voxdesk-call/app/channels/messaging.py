"""
WhatsApp + SMS conversational channels over Twilio.

Mk Solution and Exousia both advertise "WhatsApp & Phone Integration". This is
that feature, and it is genuinely valuable: a caller who hangs up is gone, but
a text thread survives. Booking-by-text converts the callers who would not
have left a voicemail.

Both channels are the same Twilio webhook shape, so they share one handler and
differ only in the `whatsapp:` address prefix and the reply length budget.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import structlog

from app.telephony.stream_auth import verify_twilio_request
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ProviderError
from app.ai.errors import customer_text, failure_kind, normalize
from app.agent.functions import FunctionHandlers
from app.agent.provider_observability import record_provider_error
from app.agent.text_agent import TextAgent
from app.core.correlation import correlation_scope
from app.core.metrics import record_side_effect
from app.db.models import (
    Call, CallDirection, CallStatus, Lead, LeadStatus, MessageWebhookReceipt,
    Speaker, Tenant, Turn,
)
from app.db.session import get_session
from app.telephony import phone as phoneutil

log = structlog.get_logger()
router = APIRouter(prefix="/channels", tags=["channels"])

WHATSAPP_PREFIX = "whatsapp:"
THREAD_IDLE_MINUTES = 30      # after this, treat the next message as a new thread
MAX_HISTORY_TURNS = 20

# Carrier-level keywords. These are legally significant: STOP must always work,
# and must work before the LLM ever sees the message.
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "optout"}
START_WORDS = {"start", "unstop", "yes", "subscribe", "optin"}
HELP_WORDS = {"help", "info"}


# ---------------------------------------------------------------- helpers ---

def strip_channel_prefix(address: str) -> str:
    """'whatsapp:+15551234567' -> '+15551234567'"""
    return address[len(WHATSAPP_PREFIX):] if address.startswith(WHATSAPP_PREFIX) else address


def detect_channel(address: str) -> str:
    return "whatsapp" if address.startswith(WHATSAPP_PREFIX) else "sms"


def normalize_keyword(body: str) -> str:
    return re.sub(r"[^a-z]", "", (body or "").strip().lower())


def twiml_reply(message: str | None) -> str:
    """Empty <Response/> means 'received, say nothing' -- valid and silent."""
    from twilio.twiml.messaging_response import MessagingResponse
    resp = MessagingResponse()
    if message:
        resp.message(message)
    return str(resp)


def opt_out_confirmation(business: str) -> str:
    return f"You've been unsubscribed from {business}. Reply START to opt back in."


def help_text(business: str) -> str:
    return (
        f"{business}: reply with your question and our assistant will help. "
        f"Reply STOP to unsubscribe."
    )


# ------------------------------------------------------------- thread state ---

async def _find_active_thread(
    session: AsyncSession, tenant: Tenant, customer: str, channel: str
) -> Call | None:
    cutoff = datetime.utcnow() - timedelta(minutes=THREAD_IDLE_MINUTES)
    stmt = (
        select(Call)
        .where(
            Call.tenant_id == tenant.id,
            Call.from_number == customer,
            Call.intent == f"chat:{channel}",
            Call.started_at >= cutoff,
        )
        .order_by(Call.started_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def get_or_start_thread(
    session: AsyncSession, tenant: Tenant, customer: str, channel: str
) -> Call:
    """
    Text conversations are stored as Calls with duration 0 so that every
    analytics query, CRM push and transcript view already works unchanged.

    The insert is wrapped in a SAVEPOINT and guarded by the unique constraint
    on `call_sid`: two requests racing to create the same thread (same channel,
    same customer, same second) collide there, and the loser re-reads the
    winner's row instead of minting a second thread.
    """
    thread = await _find_active_thread(session, tenant, customer, channel)
    if thread is not None:
        return thread

    thread = Call(
        tenant_id=tenant.id,
        call_sid=f"{channel}-{customer}-{int(datetime.utcnow().timestamp())}",
        from_number=customer,
        to_number=tenant.twilio_number,
        status=CallStatus.IN_PROGRESS,
        direction=CallDirection.INBOUND,
        intent=f"chat:{channel}",
    )
    session.add(thread)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        # Lost the race with a concurrent request. Reuse the thread it made.
        thread = await _find_active_thread(session, tenant, customer, channel)
        if thread is None:
            raise
        return thread
    await session.commit()
    await session.refresh(thread)
    return thread


async def load_history(session: AsyncSession, thread: Call) -> list[dict]:
    stmt = (
        select(Turn)
        .where(Turn.call_id == thread.id)
        .order_by(Turn.created_at)
        .limit(MAX_HISTORY_TURNS)
    )
    turns = (await session.execute(stmt)).scalars().all()
    return [
        {
            "role": "user" if t.speaker is Speaker.USER else "assistant",
            "content": t.text,
        }
        for t in turns
    ]


async def save_turn(session: AsyncSession, thread: Call, speaker: Speaker, text: str) -> None:
    session.add(Turn(call_id=thread.id, speaker=speaker, text=text))
    await session.commit()


async def _webhook_environment_id(session: AsyncSession, tenant: Tenant):
    """The environment a messaging webhook acts in.

    A webhook has no authenticated user, so the existing headless resolution
    rule applies: the tenant's active default production environment (the
    same ``production_environment_id`` the lead repository already uses).
    The phone-number lookups below are scoped to tenant + this environment,
    so a STOP in production can never mutate a staging lead and vice versa.
    If no active production environment exists the lookup raises and Twilio's
    retry will find it once operations restore one — a compliance write is
    never forced into an invented environment.
    """
    from app.leads.repository import production_environment_id

    return await production_environment_id(session, tenant.id)


async def set_opt_out(session: AsyncSession, tenant: Tenant, phone: str, out: bool) -> None:
    """STOP/START through the canonical lead consent + lifecycle path.

    Batch 06 rules, all enforced by ``app/leads`` rather than by direct
    status writes here:

    * STOP records a *voice consent denial*, which the canonical consent
      module turns into ``LeadStatus.DNC`` through ``lifecycle.transition``
      (history written, CRM hook fired once). DNC stays terminal — nothing
      in this path can reverse it.
    * START records an *SMS consent grant* only. The existing consent policy
      explicitly forbids clearing a voice do-not-call by granting consent,
      so a DNC lead keeps its DNC status (the reversal the old code did with
      ``lead.status = LeadStatus.NEW`` was exactly the silent reversal the
      policy forbids); a non-DNC lead keeps its current status — message
      subscription is consent state, not lifecycle state.
    * A lead created for an unknown number always carries the resolved
      environment (the schema requires it) and gets canonical creation
      history via ``lifecycle.record_created``.
    * The customer-facing replies are unchanged.
    """
    from app.leads import consent as lead_consent, lifecycle
    from app.leads.repository import find_lead_by_phone

    environment_id = await _webhook_environment_id(session, tenant)
    lead = await find_lead_by_phone(session, tenant.id, environment_id, phone)
    if lead is None:
        lead = Lead(
            tenant_id=tenant.id, environment_id=environment_id, phone=phone, name=""
        )
        session.add(lead)
        await session.flush()
        await lifecycle.record_created(session, lead, source="messaging")
    if out:
        await lead_consent.record_consent(
            session, lead, channel="voice", decision="denied", source="messaging:stop"
        )
    else:
        await lead_consent.record_consent(
            session, lead, channel="sms", decision="granted", source="messaging:start"
        )
    await session.commit()
    log.info("channel.opt_change", phone=phoneutil.redact(phone), opted_out=out)


def _log_agent_failure(
    exc: BaseException, *, channel: str, tenant_id: str, thread_id: str
) -> None:
    """Log an agent failure so an operator can tell *what* actually broke.

    Two shapes, deliberately split:

    * a typed :class:`ProviderError` is an operational fact — it is counted
      (``record_provider_error``, whose labels are a closed set, so a hostile
      provider cannot mint Prometheus series) and logged with its category and
      retryability, using the error's own safe message;
    * anything else is a defect in *our* code (a contract break, a response
      parse failure), so it is logged with its real exception type. It is never
      re-labelled as a provider problem, and it is never silently swallowed:
      the customer gets a safe answer, the operator gets the truth.

    Neither branch logs a payload, a key or customer text. ``ProviderError``
    messages are fixed text by construction, and the ``error=str(exc)`` field
    goes through the logging pipeline's secret redaction
    (``app.core.logging``), which masks the configured secret values wherever
    they appear. ``request_id`` is already bound by the request middleware and
    ``tenant_id`` by :func:`correlation_scope` at the call site.
    """
    if isinstance(exc, ProviderError):
        record_provider_error(exc.provider, exc.category)
        log.error(
            "channel.agent_provider_error",
            channel=channel, tenant_id=tenant_id, thread_id=thread_id,
            provider=exc.provider, category=exc.category, retryable=exc.retryable,
            error=exc.safe_message,
        )
        return
    log.error(
        "channel.agent_contract_error",
        channel=channel, tenant_id=tenant_id, thread_id=thread_id,
        error_type=type(exc).__name__, error=str(exc),
    )


def _log_governance_denial(
    exc: BaseException, *, channel: str, tenant_id: str, thread_id: str
) -> None:
    """A policy denial is not a provider outage and is not logged as one."""
    safe = normalize(exc)
    log.error(
        "channel.agent_governance_denied",
        channel=channel,
        tenant_id=tenant_id,
        thread_id=thread_id,
        code=safe["code"],
        error_type=safe["error_type"],
        kind=failure_kind(exc),
    )


# ------------------------------------------------------------------ webhook ---

@router.post("/message", response_class=PlainTextResponse)
async def inbound_message(
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(""),
    MessageSid: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Single webhook for both SMS and WhatsApp. Point both Twilio numbers here."""
    # Without this, anyone could POST a fake inbound message and make the
    # tenant's LLM answer it -- an unauthenticated way to spend their money.
    if not await verify_twilio_request(request):
        return PlainTextResponse("forbidden", status_code=403)

    channel = detect_channel(From)
    customer = strip_channel_prefix(From)
    business_number = strip_channel_prefix(To)

    tenant = (
        await session.execute(select(Tenant).where(Tenant.twilio_number == business_number))
    ).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        return PlainTextResponse(twiml_reply(None), media_type="application/xml")

    # Step 6 (scale-compliance): durable replay protection. The receipt is
    # flushed in *this* transaction (not committed on its own), so a crash
    # between here and the final commit rolls the receipt back too and Twilio
    # safely redelivers. A duplicate delivery violates the unique constraint
    # and is answered silently -- it must not produce a second turn, a second
    # LLM call, or a second reply SMS.
    if MessageSid:
        session.add(MessageWebhookReceipt(
            tenant_id=tenant.id, channel=channel, provider_message_id=MessageSid,
        ))
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            record_side_effect("message_webhook", "duplicate")
            return PlainTextResponse(twiml_reply(None), media_type="application/xml")

    keyword = normalize_keyword(Body)

    # 1) Compliance keywords are handled before the LLM, always.
    if keyword in STOP_WORDS:
        await set_opt_out(session, tenant, customer, True)
        return PlainTextResponse(
            twiml_reply(opt_out_confirmation(tenant.name)), media_type="application/xml"
        )
    if keyword in START_WORDS and len(keyword) > 2:
        await set_opt_out(session, tenant, customer, False)
        return PlainTextResponse(
            twiml_reply(f"You're subscribed to {tenant.name} again."),
            media_type="application/xml",
        )
    if keyword in HELP_WORDS:
        return PlainTextResponse(
            twiml_reply(help_text(tenant.name)), media_type="application/xml"
        )

    # 2) Respect an existing opt-out even if they text something else.
    # Batch 06: the lookup is scoped to tenant + the webhook's resolved
    # environment, so a DNC lead in one environment is never read (or
    # honoured) as if it belonged to another.
    from app.leads.repository import find_lead_by_phone
    from app.tenancy.isolation import NotFound as _EnvNotFound

    try:
        webhook_environment = await _webhook_environment_id(session, tenant)
        existing = await find_lead_by_phone(
            session, tenant.id, webhook_environment, customer
        )
    except _EnvNotFound:
        # No active production environment to resolve against: there is no
        # lead row this webhook could legitimately read, so the opt-out
        # check finds nothing (the conversation path below still answers).
        existing = None
    if existing is not None and existing.status is LeadStatus.DNC:
        return PlainTextResponse(twiml_reply(None), media_type="application/xml")

    # 3) Normal conversation.
    thread = await get_or_start_thread(session, tenant, customer, channel)
    history = await load_history(session, thread)
    await save_turn(session, thread, Speaker.USER, Body)

    handlers = FunctionHandlers(session, tenant, thread)

    # Provider/configuration failures are expected operational events (a
    # misconfigured tenant, an API outage); everything else here is a bug in
    # our own code. Both must produce the same safe answer to the customer, but
    # they must be *distinguishable* in the logs — so construction lives inside
    # the guard (it resolves the tenant's provider and its credential) and the
    # handler logs the real exception type, never a flattened "something went
    # wrong".
    with correlation_scope(tenant_id=str(tenant.id)):
        try:
            agent = TextAgent(tenant, handlers, channel=channel)
            result = await agent.reply(history, Body)
            answer = result["reply"]
            thread.llm_used = f"{result['provider']}/{result['model']}"
        except Exception as exc:
            if failure_kind(exc) == "governance":
                _log_governance_denial(
                    exc, channel=channel, tenant_id=str(tenant.id), thread_id=str(thread.id)
                )
            else:
                _log_agent_failure(exc, channel=channel, tenant_id=str(tenant.id),
                                   thread_id=str(thread.id))
            answer = customer_text(exc)

    await save_turn(session, thread, Speaker.ASSISTANT, answer)
    record_side_effect("message_webhook", "success")
    return PlainTextResponse(twiml_reply(answer), media_type="application/xml")


@router.post("/status", response_class=PlainTextResponse)
async def message_status(
    request: Request,
    MessageStatus: str = Form(""),
    MessageSid: str = Form(""),
):
    """Twilio delivery receipts. Undelivered SMS is the #1 silent failure."""
    if not await verify_twilio_request(request):
        return PlainTextResponse("forbidden", status_code=403)

    if MessageStatus in {"failed", "undelivered"}:
        log.warning("message.undelivered", sid=MessageSid, status=MessageStatus)
    return PlainTextResponse("", media_type="application/xml")


async def prune_receipts(session: AsyncSession, *, older_than_days: int = 30) -> int:
    """
    Drop old inbound-message replay records.

    Replay protection only needs to cover the window in which Twilio might
    plausibly redeliver. Keeping receipts forever turns a defence into an
    unbounded table.
    """
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    result = await session.execute(
        delete(MessageWebhookReceipt)
        .where(MessageWebhookReceipt.received_at < cutoff)
        # Never evaluate this predicate against in-memory rows (a loaded
        # tz-aware `received_at` vs this naive cutoff raises TypeError).
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount or 0
