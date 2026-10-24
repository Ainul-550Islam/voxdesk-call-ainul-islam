"""Bounded mid-call voice agent tool executor (<=3000 ms latency ceiling, call caps, RBAC, truncation+redaction)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_enterprise_audit
from app.auth.rbac import has_permission
from app.db.integrations_v2_models import IntegrationConnection, ToolEnablement
from app.db.models import UserRole
from app.integrations_v2.agent_tools.tool_defs import AGENT_TOOL_SPECS, AgentToolSpec
from app.integrations_v2.collab.meeting_service import schedule_meeting
from app.integrations_v2.collab.slack import SlackProvider, build_slack_call_blocks
from app.integrations_v2.collab.teams import TeamsProvider, build_teams_call_adaptive_card
from app.integrations_v2.collab.zoom import ZoomProvider
from app.integrations_v2.core.errors import IntegrationV2Error, redact_secrets
from app.integrations_v2.crm import CrmProviderV2
from app.integrations_v2.helpdesk.models import (
    HelpdeskProviderV2,
    TicketCreateInput,
    TicketPriority,
)
from app.integrations_v2.register_all import instantiate_provider_v2
from app.integrations_v2.settings import get_int2_settings

MAX_TOOL_RESULT_STR_LEN = 1200
_CALL_COUNTS: dict[tuple[str, str, str], int] = {}


def reset_conversation_tool_counts() -> None:
    _CALL_COUNTS.clear()


def _scrub_and_truncate(obj: Any, secrets: list[str]) -> Any:
    if isinstance(obj, str):
        return redact_secrets(obj, extra_secrets=secrets, max_len=MAX_TOOL_RESULT_STR_LEN)
    if isinstance(obj, list):
        return [_scrub_and_truncate(item, secrets) for item in obj[:25]]
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            key_str = str(k)
            if any(
                tok in key_str.lower()
                for tok in ("access_token", "refresh_token", "client_secret", "api_key", "signing_secret")
            ):
                continue
            cleaned[key_str] = _scrub_and_truncate(v, secrets)
        return cleaned
    return obj


@dataclass(frozen=True)
class AgentToolExecutionResult:
    ok: bool
    tool_name: str
    provider: str
    latency_ms: float
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool_name": self.tool_name,
            "provider": self.provider,
            "latency_ms": round(self.latency_ms, 2),
            "data": self.data,
            "error": self.error,
        }


class AgentToolExecutor:
    """Executes enabled v2 integration tools within strict voice-turn latency and safety ceilings."""

    def __init__(self, session: AsyncSession, *, http_client: Any = None) -> None:
        self.session = session
        self.http_client = http_client

    async def _resolve_enablement(
        self, *, tenant_id: uuid.UUID, agent_id: str, tool_def: AgentToolSpec
    ) -> ToolEnablement | None:
        candidate_names = tuple(dict.fromkeys((tool_def.name, tool_def.canonical_action)))
        if agent_id:
            stmt_agent = select(ToolEnablement).where(
                ToolEnablement.tenant_id == tenant_id,
                ToolEnablement.agent_id == agent_id,
                ToolEnablement.tool_name.in_(candidate_names),
            )
            specific = (await self.session.execute(stmt_agent)).scalars().first()
            if specific is not None:
                return specific

        stmt_default = select(ToolEnablement).where(
            ToolEnablement.tenant_id == tenant_id,
            ToolEnablement.agent_id == "",
            ToolEnablement.tool_name.in_(candidate_names),
        )
        return (await self.session.execute(stmt_default)).scalars().first()

    async def dry_run(
        self,
        *,
        tenant_id: uuid.UUID,
        tool_name: str,
        arguments: dict[str, Any],
        agent_id: str = "",
        actor_role: UserRole | None = None,
    ) -> dict[str, Any]:
        """Validate arguments, RBAC, and tenant enablement WITHOUT making any vendor HTTP call."""
        tool_def = AGENT_TOOL_SPECS.get(tool_name)
        if tool_def is None:
            return {"valid": False, "tool_name": tool_name, "error": f"Unknown tool {tool_name!r}"}

        if actor_role is not None and not has_permission(
            actor_role, tool_def.required_permission
        ):
            return {
                "valid": False,
                "tool_name": tool_name,
                "error": f"Insufficient permission ({tool_def.required_permission.value})",
            }

        try:
            parsed = tool_def.args_model.model_validate(arguments or {})
        except ValidationError as exc:
            return {
                "valid": False,
                "tool_name": tool_name,
                "error": f"Invalid tool arguments: {exc.errors()[0].get('msg', 'validation failed')}",
                "field_errors": [
                    {"field": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "")}
                    for e in exc.errors()
                ],
            }

        enablement = await self._resolve_enablement(
            tenant_id=tenant_id, agent_id=agent_id, tool_def=tool_def
        )
        return {
            "valid": True,
            "tool_name": tool_name,
            "canonical_action": tool_def.canonical_action,
            "enabled": bool(enablement is not None and enablement.enabled),
            "validated_arguments": parsed.model_dump(),
            "vendor_called": False,
        }

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str = "",
        agent_id: str = "",
        actor_user_id: uuid.UUID | None = None,
        actor_role: UserRole | None = None,
    ) -> AgentToolExecutionResult:
        t0 = time.perf_counter()
        tool_def = AGENT_TOOL_SPECS.get(tool_name)
        if tool_def is None:
            return AgentToolExecutionResult(
                ok=False,
                tool_name=tool_name,
                provider="",
                latency_ms=0.0,
                error=f"Unknown integration tool {tool_name!r}",
            )

        if actor_role is not None and not has_permission(
            actor_role, tool_def.required_permission
        ):
            return AgentToolExecutionResult(
                ok=False,
                tool_name=tool_name,
                provider="",
                latency_ms=0.0,
                error=f"Insufficient permission ({tool_def.required_permission.value}) for tool {tool_name}",
            )

        enablement = await self._resolve_enablement(
            tenant_id=tenant_id, agent_id=agent_id, tool_def=tool_def
        )
        if enablement is None or not enablement.enabled:
            return AgentToolExecutionResult(
                ok=False,
                tool_name=tool_name,
                provider="",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error=f"Tool {tool_name!r} is disabled for this tenant/agent",
            )

        try:
            validated_args = tool_def.args_model.model_validate(arguments or {})
        except ValidationError as exc:
            return AgentToolExecutionResult(
                ok=False,
                tool_name=tool_name,
                provider="",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error=f"Invalid tool arguments: {exc.errors()[0].get('msg', 'validation failed')}",
            )

        if call_id:
            counter_key = (str(tenant_id), call_id, tool_def.canonical_action)
            used = _CALL_COUNTS.get(counter_key, 0)
            if used >= max(1, enablement.max_calls_per_conversation):
                return AgentToolExecutionResult(
                    ok=False,
                    tool_name=tool_name,
                    provider="",
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    error=f"Per-conversation invocation ceiling ({enablement.max_calls_per_conversation}) reached for {tool_name}",
                )
            _CALL_COUNTS[counter_key] = used + 1

        conn_row = (
            await self.session.execute(
                select(IntegrationConnection).where(
                    IntegrationConnection.id == enablement.connection_id,
                    IntegrationConnection.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if conn_row is None or conn_row.status != "active":
            return AgentToolExecutionResult(
                ok=False,
                tool_name=tool_name,
                provider=conn_row.provider if conn_row else "",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error="Bound integration connection is missing or not active",
            )

        cfg = get_int2_settings()
        hard_ceiling_ms = min(
            max(200, int(enablement.timeout_ms or 3000)),
            max(500, int(cfg.agent_tool_max_timeout_ms)),
        )
        provider_instance = instantiate_provider_v2(conn_row, http_client=self.http_client)
        secret_vals = [
            str(v)
            for v in provider_instance.context.credentials.values()
            if isinstance(v, str) and len(v) >= 6
        ]

        try:
            raw_output = await asyncio.wait_for(
                self._dispatch(
                    conn_row,
                    provider_instance,
                    tool_def.canonical_action,
                    validated_args,
                    call_id=call_id,
                ),
                timeout=hard_ceiling_ms / 1000.0,
            )
            scrubbed = _scrub_and_truncate(raw_output, secret_vals)
            latency = (time.perf_counter() - t0) * 1000.0
            await record_enterprise_audit(
                self.session,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                action="integrations_v2.agent_tool.executed",
                resource_type="int2_tool",
                resource_id=tool_name,
                detail={
                    "provider": conn_row.provider,
                    "call_id": call_id,
                    "ok": True,
                    "latency_ms": round(latency, 2),
                },
            )
            return AgentToolExecutionResult(
                ok=True,
                tool_name=tool_name,
                provider=conn_row.provider,
                latency_ms=latency,
                data=scrubbed,
            )
        except asyncio.TimeoutError:
            latency = (time.perf_counter() - t0) * 1000.0
            return AgentToolExecutionResult(
                ok=False,
                tool_name=tool_name,
                provider=conn_row.provider,
                latency_ms=latency,
                error=f"Tool {tool_name} exceeded {hard_ceiling_ms}ms voice-turn latency budget",
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            return AgentToolExecutionResult(
                ok=False,
                tool_name=tool_name,
                provider=conn_row.provider,
                latency_ms=latency,
                error=redact_secrets(str(exc), extra_secrets=secret_vals, max_len=280),
            )

    async def _dispatch(
        self,
        conn_row: IntegrationConnection,
        provider: Any,
        action: str,
        args: Any,
        *,
        call_id: str,
    ) -> dict[str, Any]:
        if action == "crm_lookup_contact":
            if not isinstance(provider, CrmProviderV2):
                raise IntegrationV2Error("Connection is not a CRM provider")
            rec = await provider.lookup_contact(email=args.email, phone=args.phone)
            return {"found": rec is not None, "contact": rec.to_dict() if rec else None}

        if action == "crm_upsert_contact":
            if not isinstance(provider, CrmProviderV2):
                raise IntegrationV2Error("Connection is not a CRM provider")
            rec = await provider.upsert_contact(
                first_name=args.first_name,
                last_name=args.last_name,
                email=args.email,
                phone=args.phone,
                company=args.company,
            )
            return {"contact": rec.to_dict()}

        if action == "crm_log_call":
            if not isinstance(provider, CrmProviderV2):
                raise IntegrationV2Error("Connection is not a CRM provider")
            ext_id = await provider.log_call_activity(
                {
                    "subject": args.subject,
                    "summary": args.summary,
                    "duration_seconds": args.duration_seconds,
                    "direction": args.direction,
                    "disposition": args.disposition,
                    "external_contact_id": args.external_contact_id,
                }
            )
            return {"activity_id": ext_id}

        if action == "crm_create_deal":
            if not isinstance(provider, CrmProviderV2):
                raise IntegrationV2Error("Connection is not a CRM provider")
            deal = await provider.create_or_update_deal(
                title=args.title,
                amount=args.amount,
                currency=args.currency,
                stage=args.stage,
                contact_id=args.contact_id,
            )
            return {"deal": deal.to_dict()}

        if action == "helpdesk_create_ticket":
            if not isinstance(provider, HelpdeskProviderV2):
                raise IntegrationV2Error("Connection is not a Helpdesk provider")
            ticket = await provider.create_ticket(
                TicketCreateInput(
                    subject=args.subject,
                    description=args.description,
                    priority=TicketPriority(args.priority),
                    requester_name=args.requester_name,
                    requester_email=args.requester_email,
                    requester_phone=args.requester_phone,
                    idempotency_key=f"agent-ticket:{call_id}:{args.subject[:24]}" if call_id else "",
                )
            )
            return {"ticket": ticket.to_dict()}

        if action == "helpdesk_lookup_customer":
            if not isinstance(provider, HelpdeskProviderV2):
                raise IntegrationV2Error("Connection is not a Helpdesk provider")
            tickets = await provider.search_tickets(
                email=args.email, phone=args.phone, query=args.query, limit=args.limit
            )
            return {"tickets": [t.to_dict() for t in tickets]}

        if action == "helpdesk_add_comment":
            if not isinstance(provider, HelpdeskProviderV2):
                raise IntegrationV2Error("Connection is not a Helpdesk provider")
            c = await provider.add_comment(
                args.ticket_id, body=args.body, public=args.public
            )
            return {"comment_id": c.external_id, "ticket_id": c.ticket_id}

        if action == "notify_channel":
            if isinstance(provider, SlackProvider):
                blocks = build_slack_call_blocks(
                    header=args.title,
                    summary=args.message,
                    caller_phone=args.caller_phone,
                    call_id=call_id,
                )
                sent = await provider.post_message(
                    channel=args.channel_id, text=args.message, blocks=blocks
                )
                return {"message_id": sent["ts"], "channel": sent["channel"]}
            if isinstance(provider, TeamsProvider):
                if not args.team_id:
                    raise IntegrationV2Error("Teams channel notification requires 'team_id'")
                card = build_teams_call_adaptive_card(
                    title=args.title,
                    summary=args.message,
                    caller_phone=args.caller_phone,
                )
                mid = await provider.post_channel_adaptive_card(
                    team_id=args.team_id,
                    channel_id=args.channel_id,
                    card=card,
                    summary_text=args.title,
                )
                return {"message_id": mid, "channel": args.channel_id}
            if isinstance(provider, ZoomProvider):
                mid = await provider.send_chat_message(
                    to_channel=args.channel_id, message=f"{args.title}: {args.message}"
                )
                return {"message_id": mid, "channel": args.channel_id}
            raise IntegrationV2Error("Connection does not support channel notifications")

        if action == "meeting_schedule":
            start_dt = None
            if args.start_time_iso:
                start_dt = datetime.fromisoformat(args.start_time_iso.replace("Z", "+00:00"))
            rec, _ = await schedule_meeting(
                self.session,
                connection=conn_row,
                topic=args.topic,
                when=start_dt,
                duration_minutes=args.duration_minutes,
                lead_id=getattr(args, "lead_id", ""),
                call_id=call_id,
                http_client=self.http_client,
            )
            return {
                "meeting": {
                    "id": str(rec.id),
                    "external_meeting_id": rec.external_meeting_id,
                    "join_url": rec.join_url,
                    "topic": rec.topic,
                    "start_time": rec.start_time.isoformat(),
                }
            }

        raise IntegrationV2Error(f"Unhandled tool action {action!r}")
