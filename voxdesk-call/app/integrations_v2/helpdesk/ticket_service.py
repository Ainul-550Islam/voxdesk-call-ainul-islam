"""Provider-neutral Helpdesk ticket service for post-call pipelines and voice-agent tools."""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import ExternalObjectLink, IntegrationConnection
from app.db.models import Call
from app.integrations_v2.core.errors import IntegrationV2Error, redact_secrets
from app.integrations_v2.core.writeback import issue_signed_recording_url
from app.integrations_v2.helpdesk.models import (
    HelpdeskProviderV2,
    TicketCommentRecord,
    TicketCreateInput,
    TicketPriority,
    TicketRecord,
    TicketStatus,
)
from app.integrations_v2.register_all import instantiate_provider_v2

_PHI_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED-PAN]"),
    (re.compile(r"(?i)\bdob\s*[:=]\s*[0-9/\-]{6,10}"), "DOB: [REDACTED]"),
)


def apply_redaction_policy(text: str, *, connection: IntegrationConnection) -> str:
    """Apply secret scrubbing and optional PHI/transcript redaction flags from connection config."""
    out = redact_secrets(text, max_len=6000)
    cfg = dict(connection.config_json or {})
    if cfg.get("redact_phi", True):
        for pat, repl in _PHI_PATTERNS:
            out = pat.sub(repl, out)
    return out


async def find_or_create_requester(
    provider: HelpdeskProviderV2,
    *,
    name: str = "",
    email: str = "",
    phone: str = "",
    create_if_missing: bool = False,
) -> dict[str, str]:
    """Search existing tickets/requesters by phone or email (or create via vendor API when requested)."""
    if create_if_missing:
        req = await provider.find_or_create_requester(
            name=name or email or phone or "Voice Caller",
            email=email,
            phone=phone,
        )
        return {
            "requester_id": req.external_id,
            "email": req.email or email,
            "phone": req.phone or phone,
            "name": req.name or name or "Voice Caller",
            "existing": "true" if req.external_id else "false",
        }
    matches = await provider.search_tickets(email=email, phone=phone, limit=1)
    if matches:
        first = matches[0]
        return {
            "requester_id": first.requester_id,
            "email": first.requester_email or email,
            "phone": first.requester_phone or phone,
            "name": name or first.requester_email or "Voice Caller",
            "existing": "true",
        }
    return {
        "requester_id": "",
        "email": email,
        "phone": phone,
        "name": name or email or phone or "Voice Caller",
        "existing": "false",
    }


async def create_ticket_from_call(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    call_id: uuid.UUID | str,
    subject: str = "",
    summary_override: str = "",
    priority: TicketPriority = TicketPriority.NORMAL,
    requester_email: str = "",
    requester_phone: str = "",
    requester_name: str = "",
    http_client: Any = None,
) -> TicketRecord:
    """Create a vendor ticket from a tenant-owned ``Call`` row, linking signed recording URL when permitted."""
    provider = instantiate_provider_v2(connection, http_client=http_client)
    if not isinstance(provider, HelpdeskProviderV2):
        raise IntegrationV2Error(
            f"Connection {connection.provider} is not a helpdesk provider",
            provider=connection.provider,
        )

    call_row: Call | None = None
    try:
        call_uuid = call_id if isinstance(call_id, uuid.UUID) else uuid.UUID(str(call_id))
        call_row = (
            await session.execute(
                select(Call).where(
                    Call.id == call_uuid, Call.tenant_id == connection.tenant_id
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        call_row = None

    cfg = dict(connection.config_json or {})
    share_transcripts = bool(cfg.get("share_transcripts", True))
    raw_summary = summary_override
    caller_num = requester_phone
    if call_row is not None:
        if not caller_num:
            direction_val = getattr(call_row.direction, "value", str(call_row.direction or "inbound"))
            caller_num = call_row.from_number if direction_val == "inbound" else call_row.to_number
        if not raw_summary:
            raw_summary = (
                str(getattr(call_row, "summary", "") or "")
                if share_transcripts
                else f"Call completed ({getattr(call_row, 'duration_seconds', 0) or 0}s). Transcript withheld by policy."
            )

    clean_summary = apply_redaction_policy(
        raw_summary or "Inbound AI voice call handled by VoxDesk.",
        connection=connection,
    )

    signed_rec = await issue_signed_recording_url(
        session, tenant_id=connection.tenant_id, call_id=str(call_id)
    )
    description_lines = [clean_summary]
    if signed_rec and cfg.get("include_recording_link", True):
        description_lines.append(f"Signed Recording (15m): {signed_rec}")

    ticket = await provider.create_ticket(
        TicketCreateInput(
            subject=subject or f"Voice Call from {caller_num or 'Caller'}",
            description="\n\n".join(description_lines),
            priority=priority,
            status=TicketStatus.OPEN,
            requester_name=requester_name or "Voice Caller",
            requester_email=requester_email,
            requester_phone=caller_num,
            tags=["voxdesk", "voice-call"],
            idempotency_key=f"call-ticket:{connection.id}:{call_id}"[:120],
        )
    )

    existing_link = (
        await session.execute(
            select(ExternalObjectLink).where(
                ExternalObjectLink.connection_id == connection.id,
                ExternalObjectLink.entity_type == "Ticket",
                ExternalObjectLink.external_id == ticket.external_id,
            )
        )
    ).scalar_one_or_none()
    if existing_link is None:
        session.add(
            ExternalObjectLink(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                entity_type="Ticket",
                voxdesk_id=str(call_id)[:120],
                external_id=ticket.external_id,
            )
        )
        await session.flush()

    return ticket


async def append_call_summary(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    ticket_id: str,
    call_id: str,
    summary: str,
    public: bool = False,
    http_client: Any = None,
) -> TicketCommentRecord:
    """Append a redacted call summary (+ signed recording URL if available) to an existing ticket."""
    provider = instantiate_provider_v2(connection, http_client=http_client)
    if not isinstance(provider, HelpdeskProviderV2):
        raise IntegrationV2Error(
            f"Connection {connection.provider} is not a helpdesk provider",
            provider=connection.provider,
        )
    clean = apply_redaction_policy(summary, connection=connection)
    signed_rec = await issue_signed_recording_url(
        session, tenant_id=connection.tenant_id, call_id=call_id
    )
    if signed_rec and dict(connection.config_json or {}).get("include_recording_link", True):
        clean = f"{clean}\n\nSigned Recording: {signed_rec}"
    return await provider.add_comment(ticket_id, body=clean, public=public)
