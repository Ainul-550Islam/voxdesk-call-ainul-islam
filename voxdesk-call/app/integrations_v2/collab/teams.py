"""Microsoft Teams Graph v1.0 (Adaptive Cards v1.4, onlineMeetings, calendar events, user lookup, subscriptions) adapter."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.integrations_v2.core.errors import AuthExpired, CapabilityUnsupported
from app.integrations_v2.core.http_client import Int2HttpClient
from app.integrations_v2.core.provider_base import (
    AuthKind,
    Capability,
    HealthStatus,
    ProviderV2,
)

GRAPH_V1_BASE = "https://graph.microsoft.com/v1.0"


def build_teams_call_adaptive_card(
    *,
    title: str,
    summary: str,
    caller_phone: str = "",
    disposition: str = "completed",
    duration_seconds: int = 0,
    dashboard_url: str = "",
) -> dict[str, Any]:
    """Build a valid Microsoft Teams Adaptive Card v1.4 payload for call summaries/escalations."""
    facts = [
        {"title": "Caller", "value": caller_phone or "Unknown"},
        {"title": "Disposition", "value": disposition},
        {"title": "Duration", "value": f"{duration_seconds}s"},
    ]
    actions: list[dict[str, Any]] = []
    if dashboard_url:
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "Open in VoxDesk",
                "url": dashboard_url,
            }
        )
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "size": "Medium",
                "weight": "Bolder",
                "text": title,
            },
            {
                "type": "TextBlock",
                "text": summary,
                "wrap": True,
            },
            {"type": "FactSet", "facts": facts},
        ],
        "actions": actions,
    }


class TeamsProvider(ProviderV2):
    slug = "teams"
    display_name = "Microsoft Teams"
    category = "collab"
    auth_kind = AuthKind.OAUTH2
    default_scopes = (
        "offline_access",
        "User.Read",
        "User.ReadBasic.All",
        "Team.ReadBasic.All",
        "Channel.ReadBasic.All",
        "ChannelMessage.Send",
        "OnlineMeetings.ReadWrite",
        "Calendars.ReadWrite",
        "Presence.Read.All",
    )
    docs_url = "https://learn.microsoft.com/en-us/graph/teams-concept-overview"
    capabilities = frozenset(
        {
            Capability.MESSAGES_WRITE,
            Capability.MEETINGS_WRITE,
            Capability.WEBHOOKS_INBOUND,
            Capability.INTERACTIVE_ACTIONS,
            Capability.MID_CALL_TOOLS,
        }
    )

    def _client(self) -> Int2HttpClient:
        return self._http_client or Int2HttpClient()

    def _headers(self) -> dict[str, str]:
        token = str(self.context.credentials.get("access_token") or "").strip()
        if not token:
            raise AuthExpired("Microsoft Teams Graph access_token is missing", provider=self.slug)
        return {"Authorization": f"Bearer {token}"}

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{GRAPH_V1_BASE}/me",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
        )
        me = resp.json() or {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(me.get("id") or ""),
            account_name=str(me.get("displayName") or me.get("userPrincipalName") or ""),
            detail="graph_v1=ok",
        )

    async def list_joined_teams(self) -> list[dict[str, Any]]:
        resp = await self._client().request(
            "GET",
            f"{GRAPH_V1_BASE}/me/joinedTeams",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
        )
        return [
            {
                "id": str(t.get("id") or ""),
                "name": str(t.get("displayName") or ""),
                "description": str(t.get("description") or ""),
            }
            for t in (resp.json() or {}).get("value") or []
        ]

    async def list_channels(self, team_id: str) -> list[dict[str, Any]]:
        resp = await self._client().request(
            "GET",
            f"{GRAPH_V1_BASE}/teams/{team_id}/channels",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
        )
        return [
            {
                "id": str(c.get("id") or ""),
                "name": str(c.get("displayName") or ""),
                "membership_type": str(c.get("membershipType") or "standard"),
            }
            for c in (resp.json() or {}).get("value") or []
        ]

    async def post_channel_adaptive_card(
        self,
        *,
        team_id: str,
        channel_id: str,
        card: dict[str, Any],
        summary_text: str = "VoxDesk Notification",
    ) -> str:
        attachment_id = str(uuid.uuid4())
        body = {
            "subject": summary_text[:120],
            "body": {
                "contentType": "html",
                "content": f'<attachment id="{attachment_id}"></attachment>',
            },
            "attachments": [
                {
                    "id": attachment_id,
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": card,
                }
            ],
        }
        resp = await self._client().request(
            "POST",
            f"{GRAPH_V1_BASE}/teams/{team_id}/channels/{channel_id}/messages",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body=body,
        )
        return str((resp.json() or {}).get("id") or "")

    async def post_chat_message(self, *, chat_id: str, content: str) -> str:
        resp = await self._client().request(
            "POST",
            f"{GRAPH_V1_BASE}/chats/{chat_id}/messages",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={"body": {"contentType": "text", "content": content}},
        )
        return str((resp.json() or {}).get("id") or "")

    async def create_online_meeting(
        self,
        *,
        subject: str,
        start_time: datetime | None = None,
        duration_minutes: int = 30,
    ) -> dict[str, Any]:
        start = start_time or (datetime.now(timezone.utc) + timedelta(minutes=5))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = start + timedelta(minutes=max(5, duration_minutes))

        resp = await self._client().request(
            "POST",
            f"{GRAPH_V1_BASE}/me/onlineMeetings",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={
                "subject": subject,
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
            },
        )
        m = resp.json() or {}
        return {
            "meeting_id": str(m.get("id") or ""),
            "join_url": str(m.get("joinWebUrl") or ""),
            "subject": str(m.get("subject") or subject),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }

    async def create_calendar_event(
        self,
        *,
        subject: str,
        start_time: datetime | None = None,
        duration_minutes: int = 30,
        attendee_email: str = "",
        attendee_name: str = "",
        attendees: list[str] | None = None,
        is_online_meeting: bool = True,
    ) -> dict[str, Any]:
        base_start = start_time or (datetime.now(timezone.utc) + timedelta(minutes=15))
        start = base_start if base_start.tzinfo else base_start.replace(tzinfo=timezone.utc)
        end = start + timedelta(minutes=max(5, duration_minutes))
        attendee_objs: list[dict[str, Any]] = []
        if attendee_email:
            attendee_objs.append(
                {
                    "emailAddress": {
                        "address": attendee_email,
                        "name": attendee_name or attendee_email,
                    },
                    "type": "required",
                }
            )
        for addr in attendees or []:
            if addr and addr != attendee_email:
                attendee_objs.append(
                    {
                        "emailAddress": {"address": addr, "name": addr},
                        "type": "required",
                    }
                )
        payload: dict[str, Any] = {
            "subject": subject,
            "start": {"dateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "end": {"dateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "attendees": attendee_objs,
            "isOnlineMeeting": is_online_meeting,
        }
        if is_online_meeting:
            payload["onlineMeetingProvider"] = "teamsForBusiness"

        resp = await self._client().request(
            "POST",
            f"{GRAPH_V1_BASE}/me/events",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body=payload,
        )
        ev = resp.json() or {}
        online = ev.get("onlineMeeting") or {}
        return {
            "event_id": str(ev.get("id") or ""),
            "join_url": str(online.get("joinUrl") or ""),
            "subject": str(ev.get("subject") or subject),
        }

    async def lookup_user(self, *, email: str = "", phone: str = "") -> dict[str, Any] | None:
        clauses: list[str] = []
        if email:
            safe_mail = email.replace("'", "''")
            clauses.append(f"mail eq '{safe_mail}' or userPrincipalName eq '{safe_mail}'")
        if phone:
            safe_phone = phone.replace("'", "''")
            clauses.append(f"mobilePhone eq '{safe_phone}'")
        if not clauses:
            return None
        resp = await self._client().request(
            "GET",
            f"{GRAPH_V1_BASE}/users",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            params={"$filter": " or ".join(clauses), "$top": 1},
        )
        items = (resp.json() or {}).get("value") or []
        return dict(items[0]) if items else None

    async def create_subscription(
        self,
        *,
        resource: str,
        notification_url: str,
        client_state: str,
        change_type: str = "created,updated",
        ttl_minutes: int = 55,
    ) -> dict[str, Any]:
        exp = (datetime.now(timezone.utc) + timedelta(minutes=max(5, ttl_minutes))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        resp = await self._client().request(
            "POST",
            f"{GRAPH_V1_BASE}/subscriptions",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={
                "changeType": change_type,
                "notificationUrl": notification_url,
                "resource": resource,
                "expirationDateTime": exp,
                "clientState": client_state,
            },
        )
        return dict(resp.json() or {})

    async def renew_subscription(
        self, subscription_id: str, *, ttl_minutes: int = 55
    ) -> dict[str, Any]:
        exp = (datetime.now(timezone.utc) + timedelta(minutes=max(5, ttl_minutes))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        resp = await self._client().request(
            "PATCH",
            f"{GRAPH_V1_BASE}/subscriptions/{subscription_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={"expirationDateTime": exp},
        )
        return dict(resp.json() or {})

    async def get_user_presence(self, user_id: str) -> dict[str, str]:
        resp = await self._client().request(
            "GET",
            f"{GRAPH_V1_BASE}/users/{user_id}/presence",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
        )
        p = resp.json() or {}
        return {
            "user_id": str(p.get("id") or user_id),
            "availability": str(p.get("availability") or "PresenceUnknown"),
            "activity": str(p.get("activity") or "PresenceUnknown"),
        }

    async def place_direct_routing_pstn_call(self, *_args: Any, **_kwargs: Any) -> None:
        raise CapabilityUnsupported(
            "Teams Direct Routing PSTN carrier origination requires certified SBC provisioning and is unsupported by the Graph collaboration adapter",
            provider=self.slug,
        )
