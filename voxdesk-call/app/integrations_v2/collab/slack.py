"""Slack Web API v2 + Block Kit + signed ``/voxdesk`` slash commands + interactive actions."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any
from urllib.parse import parse_qs

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.integrations_v2_models import IntegrationConnection
from app.db.models import Call, CallStatus, Lead, User
from app.integrations_v2.core.connection_store import load_runtime_context
from app.integrations_v2.core.errors import (
    AuthExpired,
    CapabilityUnsupported,
    IntegrationV2Error,
    ProviderRejected,
    RateLimited,
)
from app.integrations_v2.core.http_client import Int2HttpClient
from app.integrations_v2.core.provider_base import (
    AuthKind,
    Capability,
    HealthStatus,
    ProviderV2,
)
from app.integrations_v2.core.webhook_inbound import (
    resolve_webhook_signing_secret,
    verify_slack_signature,
)
from app.outbox.publisher import publish as publish_outbox_event

SLACK_API_BASE = "https://slack.com/api"
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def build_slack_call_blocks(
    *,
    header: str,
    summary: str,
    caller_phone: str = "",
    call_id: str = "",
    lead_id: str = "",
    disposition: str = "completed",
    duration_seconds: int = 0,
    recording_url: str = "",
) -> list[dict[str, Any]]:
    """Build valid Slack Block Kit blocks with interactive ``actions`` buttons."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header[:150], "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Caller:*\n`{caller_phone or 'Unknown'}`"},
                {"type": "mrkdwn", "text": f"*Disposition:*\n`{disposition}`"},
                {"type": "mrkdwn", "text": f"*Duration:*\n{duration_seconds}s"},
                {"type": "mrkdwn", "text": f"*Call ID:*\n`{call_id or 'n/a'}`"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary[:2800] or "Call completed."},
        },
    ]

    if recording_url:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"<{recording_url}|Listen to time-boxed signed recording>",
                    }
                ],
            }
        )

    action_elements: list[dict[str, Any]] = []
    if caller_phone:
        action_elements.append(
            {
                "type": "button",
                "action_id": "voxdesk_call_back",
                "style": "primary",
                "text": {"type": "plain_text", "text": "Call Back"},
                "value": json.dumps({"action": "call_back", "phone": caller_phone, "call_id": call_id}),
            }
        )
    if lead_id:
        action_elements.append(
            {
                "type": "button",
                "action_id": "voxdesk_assign_lead",
                "text": {"type": "plain_text", "text": "Assign Lead to Me"},
                "value": json.dumps({"action": "assign_lead", "lead_id": lead_id}),
            }
        )
    if call_id:
        action_elements.append(
            {
                "type": "button",
                "action_id": "voxdesk_open_ticket",
                "text": {"type": "plain_text", "text": "Open Ticket"},
                "value": json.dumps({"action": "open_ticket", "call_id": call_id}),
            }
        )

    if action_elements:
        blocks.append({"type": "actions", "elements": action_elements})
    return blocks


class SlackProvider(ProviderV2):
    slug = "slack"
    display_name = "Slack"
    category = "collab"
    auth_kind = AuthKind.OAUTH2
    default_scopes = (
        "chat:write",
        "chat:write.public",
        "channels:read",
        "groups:read",
        "commands",
        "users:read",
        "users:read.email",
    )
    docs_url = "https://api.slack.com/web"
    capabilities = frozenset(
        {
            Capability.CHANNEL_MESSAGE,
            Capability.SLASH_COMMAND,
            Capability.INTERACTIVE_ACTIONS,
            Capability.WEBHOOK_INBOUND,
            Capability.MID_CALL_TOOLS,
        }
    )

    def _client(self) -> Int2HttpClient:
        return self._http_client or Int2HttpClient()

    def _headers(self) -> dict[str, str]:
        token = str(
            self.context.credentials.get("access_token")
            or self.context.credentials.get("bot_token")
            or ""
        ).strip()
        if not token:
            raise AuthExpired("Slack bot access_token (xoxb-...) is missing", provider=self.slug)
        return {"Authorization": f"Bearer {token}"}

    async def _slack_call(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._client().request(
            method,
            f"{SLACK_API_BASE}/{endpoint.lstrip('/')}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            params=params,
            json_body=json_body,
        )
        data = resp.json() or {}
        if data.get("ok") is False:
            err = str(data.get("error") or "slack_error")
            if err in {"invalid_auth", "token_revoked", "account_inactive", "not_authed"}:
                raise AuthExpired(f"Slack authentication failed: {err}", provider=self.slug)
            if err == "missing_scope":
                raise CapabilityUnsupported(
                    f"Slack bot missing required scope ({data.get('needed', '')})",
                    provider=self.slug,
                )
            if err == "ratelimited":
                raise RateLimited("Slack API rate limited", provider=self.slug)
            raise ProviderRejected(f"Slack API rejected call ({err})", provider=self.slug)
        return data

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        data = await self._slack_call("POST", "auth.test")
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(data.get("team_id") or ""),
            account_name=str(data.get("team") or data.get("user") or ""),
            detail=f"bot_user_id={data.get('user_id', '')}",
        )

    async def list_channels(self, *, include_private: bool = True) -> list[dict[str, Any]]:
        types = "public_channel,private_channel" if include_private else "public_channel"
        data = await self._slack_call(
            "GET",
            "conversations.list",
            params={"types": types, "exclude_archived": "true", "limit": 200},
        )
        return [
            {
                "id": str(ch.get("id") or ""),
                "name": str(ch.get("name") or ""),
                "is_private": bool(ch.get("is_private", False)),
            }
            for ch in data.get("channels") or []
        ]

    async def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str = "",
    ) -> dict[str, str]:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        data = await self._slack_call("POST", "chat.postMessage", json_body=payload)
        return {
            "channel": str(data.get("channel") or channel),
            "ts": str(data.get("ts") or ""),
        }

    async def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            payload["blocks"] = blocks
        data = await self._slack_call("POST", "chat.update", json_body=payload)
        return {"channel": str(data.get("channel") or channel), "ts": str(data.get("ts") or ts)}


async def _resolve_slack_actor_for_tenant(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    slack_user_id: str,
    required_permission: Permission,
) -> User:
    """Resolve a Slack user to a tenant-scoped VoxDesk ``User`` and enforce RBAC."""
    user_map: dict[str, str] = dict((connection.config_json or {}).get("slack_user_map") or {})
    mapped_user_id = user_map.get(slack_user_id)
    if not mapped_user_id:
        raise AuthExpired(
            f"Slack user {slack_user_id!r} is not linked to an authorized VoxDesk user in this tenant",
            provider="slack",
        )
    try:
        user_uuid = uuid.UUID(mapped_user_id)
    except ValueError as exc:
        raise AuthExpired("Invalid mapped VoxDesk user_id", provider="slack") from exc

    stmt = select(User).where(
        User.id == user_uuid,
        User.tenant_id == connection.tenant_id,
        User.is_active.is_(True),
    )
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise AuthExpired("Mapped VoxDesk user is inactive or belongs to another tenant", provider="slack")

    if not has_permission(user.role, required_permission):
        raise AuthExpired(
            f"VoxDesk user lacks required permission {required_permission.value}",
            provider="slack",
        )
    return user


async def _initiate_tenant_outbound_call(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    initiated_by_user: User,
    to_number: str,
    source_tag: str,
) -> Call:
    if not _E164_RE.fullmatch(to_number):
        raise IntegrationV2Error(
            f"Invalid destination phone number {to_number!r}; expected E.164 format (+15551234567)",
            provider="slack",
        )
    call_row = Call(
        tenant_id=tenant_id,
        call_sid=f"slack-{uuid.uuid4().hex[:24]}",
        from_number="voxdesk-outbound",
        to_number=to_number,
        direction="outbound",
        status=CallStatus.RINGING,
    )
    session.add(call_row)
    await session.flush()

    await publish_outbox_event(
        session,
        tenant_id=tenant_id,
        event_type="telephony.outbound_call.requested",
        aggregate_type="call",
        aggregate_id=str(call_row.id),
        payload={
            "call_id": str(call_row.id),
            "to_number": to_number,
            "initiated_by": str(initiated_by_user.id),
            "source": source_tag,
        },
        idempotency_key=f"slack-call:{call_row.id}",
    )
    return call_row


async def handle_slack_slash_command(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    raw_body: bytes,
    headers: dict[str, str],
    http_client: Int2HttpClient | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify Slack signature, enforce VoxDesk RBAC, and handle ``/voxdesk status`` or ``/voxdesk call <phone>``."""
    lower = {k.lower(): v for k, v in headers.items()}
    secret = resolve_webhook_signing_secret(connection)
    verify_slack_signature(
        signing_secret=secret,
        timestamp_header=lower.get("x-slack-request-timestamp", ""),
        signature_header=lower.get("x-slack-signature", ""),
        raw_body=raw_body,
        now=now,
    )

    form = {k: v[0] for k, v in parse_qs(raw_body.decode("utf-8")).items() if v}
    text = str(form.get("text") or "").strip()
    slack_user_id = str(form.get("user_id") or "").strip()
    parts = text.split()
    subcommand = parts[0].lower() if parts else "status"

    if subcommand == "status":
        provider = SlackProvider(load_runtime_context(connection), http_client=http_client)
        _ = provider.supports(Capability.SLASH_COMMAND)
        return {
            "response_type": "ephemeral",
            "text": f"VoxDesk Slack integration `{connection.display_name}` is *{connection.status}*.",
        }

    if subcommand == "call":
        if len(parts) < 2:
            return {
                "response_type": "ephemeral",
                "text": "Usage: `/voxdesk call +15551234567`",
            }
        actor = await _resolve_slack_actor_for_tenant(
            session,
            connection=connection,
            slack_user_id=slack_user_id,
            required_permission=Permission.CALL_WRITE,
        )
        call_row = await _initiate_tenant_outbound_call(
            session,
            tenant_id=connection.tenant_id,
            initiated_by_user=actor,
            to_number=parts[1].strip(),
            source_tag=f"slack:slash:{slack_user_id}",
        )
        return {
            "response_type": "ephemeral",
            "text": f"Outbound call `{call_row.id}` queued to `{parts[1].strip()}`.",
        }

    return {
        "response_type": "ephemeral",
        "text": "Supported commands: `/voxdesk status`, `/voxdesk call <+E164>`.",
    }


async def handle_slack_interaction(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    raw_body: bytes,
    headers: dict[str, str],
    now: int | None = None,
) -> dict[str, Any]:
    """Verify Slack signature + VoxDesk RBAC and execute Block Kit button actions."""
    lower = {k.lower(): v for k, v in headers.items()}
    secret = resolve_webhook_signing_secret(connection)
    verify_slack_signature(
        signing_secret=secret,
        timestamp_header=lower.get("x-slack-request-timestamp", ""),
        signature_header=lower.get("x-slack-signature", ""),
        raw_body=raw_body,
        now=now,
    )

    form = parse_qs(raw_body.decode("utf-8"))
    raw_payload = (form.get("payload") or [""])[0]
    if not raw_payload:
        raise IntegrationV2Error("Missing Slack interaction payload", provider="slack")
    payload = json.loads(raw_payload)

    slack_user_id = str((payload.get("user") or {}).get("id") or "")
    actions = payload.get("actions") or []
    if not actions:
        return {"ok": True, "detail": "no_actions"}

    action_obj = actions[0]
    action_id = str(action_obj.get("action_id") or "")
    raw_val = action_obj.get("value") or "{}"
    action_val = json.loads(raw_val) if isinstance(raw_val, str) else {}
    kind = action_val.get("action") or action_id

    if kind in ("call_back", "voxdesk_call_back"):
        actor = await _resolve_slack_actor_for_tenant(
            session,
            connection=connection,
            slack_user_id=slack_user_id,
            required_permission=Permission.CALL_WRITE,
        )
        phone = str(action_val.get("phone") or "").strip()
        call_row = await _initiate_tenant_outbound_call(
            session,
            tenant_id=connection.tenant_id,
            initiated_by_user=actor,
            to_number=phone,
            source_tag=f"slack:button:{slack_user_id}",
        )
        return {"ok": True, "action": "call_back", "call_id": str(call_row.id)}

    if kind in ("assign_lead", "voxdesk_assign_lead"):
        actor = await _resolve_slack_actor_for_tenant(
            session,
            connection=connection,
            slack_user_id=slack_user_id,
            required_permission=Permission.LEAD_UPDATE,
        )
        lead_id_str = str(action_val.get("lead_id") or "").strip()
        try:
            lead_uuid = uuid.UUID(lead_id_str)
        except ValueError as exc:
            raise IntegrationV2Error("Invalid lead_id in Slack button action", provider="slack") from exc

        lead = (
            await session.execute(
                select(Lead).where(
                    Lead.id == lead_uuid,
                    Lead.tenant_id == connection.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if lead is None:
            raise IntegrationV2Error("Lead not found in tenant", provider="slack")
        extra = dict(lead.custom_fields or {})
        extra["assigned_user_id"] = str(actor.id)
        lead.custom_fields = extra
        await session.flush()
        return {"ok": True, "action": "assign_lead", "lead_id": str(lead.id), "assigned_to": str(actor.id)}

    if kind in ("open_ticket", "voxdesk_open_ticket"):
        actor = await _resolve_slack_actor_for_tenant(
            session,
            connection=connection,
            slack_user_id=slack_user_id,
            required_permission=Permission.INTEGRATION_WRITE,
        )
        call_id = str(action_val.get("call_id") or "").strip()
        await publish_outbox_event(
            session,
            tenant_id=connection.tenant_id,
            event_type="integrations_v2.ticket_from_slack_requested",
            aggregate_type="call",
            aggregate_id=call_id or "slack",
            payload={"call_id": call_id, "requested_by": str(actor.id)},
            idempotency_key=f"slack-ticket:{connection.tenant_id}:{call_id}"[:120],
        )
        return {"ok": True, "action": "open_ticket", "call_id": call_id}

    await _resolve_slack_actor_for_tenant(
        session,
        connection=connection,
        slack_user_id=slack_user_id,
        required_permission=Permission.INTEGRATION_READ,
    )
    return {"ok": True, "action": kind}
