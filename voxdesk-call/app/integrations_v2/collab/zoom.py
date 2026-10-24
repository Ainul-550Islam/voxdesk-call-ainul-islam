"""Zoom REST API v2 (meetings CRUD, upcoming list, user lookup, optional Zoom Phone logs, S2S OAuth) adapter."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.integrations_v2.core.errors import (
    AuthExpired,
    CapabilityUnsupported,
    IntegrationV2Error,
)
from app.integrations_v2.core.http_client import Int2HttpClient
from app.integrations_v2.core.provider_base import (
    AuthKind,
    Capability,
    HealthStatus,
    ProviderV2,
)
from app.integrations_v2.settings import get_int2_settings

ZOOM_API_BASE = "https://api.zoom.us/v2"


class ZoomProvider(ProviderV2):
    slug = "zoom"
    display_name = "Zoom Workplace & Phone"
    category = "collab"
    auth_kind = AuthKind.OAUTH2
    default_scopes = (
        "user:read:user",
        "meeting:write:meeting",
        "meeting:read:meeting",
        "phone:read:list_call_logs",
        "team_chat:write:user_message",
    )
    docs_url = "https://developers.zoom.us/docs/api/"
    capabilities = frozenset(
        {
            Capability.MEETINGS_WRITE,
            Capability.MESSAGES_WRITE,
            Capability.WEBHOOKS_INBOUND,
            Capability.MID_CALL_TOOLS,
        }
    )

    def _client(self) -> Int2HttpClient:
        return self._http_client or Int2HttpClient()

    async def _ensure_access_token(self) -> str:
        tok = str(self.context.credentials.get("access_token") or "").strip()
        if tok:
            return tok

        cfg = get_int2_settings()
        acct_id = str(
            self.context.credentials.get("account_id")
            or self.context.config.get("account_id")
            or cfg.zoom_account_id
        ).strip()
        cid = str(self.context.credentials.get("client_id") or cfg.zoom_client_id).strip()
        csec = str(self.context.credentials.get("client_secret") or cfg.zoom_client_secret).strip()
        if not (acct_id and cid and csec):
            raise AuthExpired("Zoom OAuth access_token or S2S account_credentials missing", provider=self.slug)

        resp = await self._client().request(
            "POST",
            "https://zoom.us/oauth/token",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            params={"grant_type": "account_credentials", "account_id": acct_id},
            auth=(cid, csec),
        )
        data = resp.json() or {}
        new_tok = str(data.get("access_token") or "").strip()
        if not new_tok:
            raise AuthExpired("Zoom S2S OAuth did not return an access_token", provider=self.slug)
        self.context.credentials["access_token"] = new_tok
        if data.get("scope"):
            self.context.credentials["scope"] = str(data["scope"])
        return new_tok

    async def _headers(self) -> dict[str, str]:
        tok = await self._ensure_access_token()
        return {"Authorization": f"Bearer {tok}"}

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{ZOOM_API_BASE}/users/me",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
        )
        me = resp.json() or {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(me.get("account_id") or me.get("id") or ""),
            account_name=str(me.get("email") or f"{me.get('first_name', '')} {me.get('last_name', '')}".strip()),
            detail="zoom_v2=ok",
        )

    async def lookup_user(self, email_or_id: str) -> dict[str, Any]:
        resp = await self._client().request(
            "GET",
            f"{ZOOM_API_BASE}/users/{email_or_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
        )
        return dict(resp.json() or {})

    async def create_meeting(
        self,
        *,
        topic: str,
        start_time: datetime | None = None,
        duration_minutes: int = 30,
        timezone_name: str = "UTC",
        agenda: str = "",
    ) -> dict[str, Any]:
        start = start_time or (datetime.now(timezone.utc) + timedelta(minutes=10))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        body: dict[str, Any] = {
            "topic": topic,
            "type": 2,
            "start_time": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration": max(5, int(duration_minutes)),
            "timezone": timezone_name,
            "settings": {"join_before_host": False, "waiting_room": True},
        }
        if agenda:
            body["agenda"] = agenda[:2000]

        resp = await self._client().request(
            "POST",
            f"{ZOOM_API_BASE}/users/me/meetings",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
            json_body=body,
        )
        m = resp.json() or {}
        return {
            "meeting_id": str(m.get("id") or ""),
            "join_url": str(m.get("join_url") or ""),
            "start_url": str(m.get("start_url") or ""),
            "password": str(m.get("password") or ""),
            "topic": str(m.get("topic") or topic),
            "start_time": str(m.get("start_time") or start.isoformat()),
        }

    async def update_meeting(
        self,
        meeting_id: str,
        *,
        topic: str | None = None,
        start_time: datetime | None = None,
        duration_minutes: int | None = None,
        agenda: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if topic is not None:
            body["topic"] = topic
        if start_time is not None:
            st = start_time if start_time.tzinfo else start_time.replace(tzinfo=timezone.utc)
            body["start_time"] = st.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if duration_minutes is not None:
            body["duration"] = max(5, int(duration_minutes))
        if agenda is not None:
            body["agenda"] = agenda[:2000]
        await self._client().request(
            "PATCH",
            f"{ZOOM_API_BASE}/meetings/{meeting_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
            json_body=body,
            expected_statuses=(200, 204),
        )
        return {"updated": True, "meeting_id": str(meeting_id)}

    async def list_upcoming_meetings(self, *, page_size: int = 30) -> list[dict[str, Any]]:
        resp = await self._client().request(
            "GET",
            f"{ZOOM_API_BASE}/users/me/meetings",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
            params={"type": "upcoming", "page_size": min(max(1, page_size), 100)},
        )
        return list((resp.json() or {}).get("meetings") or [])

    async def get_meeting(self, meeting_id: str) -> dict[str, Any]:
        resp = await self._client().request(
            "GET",
            f"{ZOOM_API_BASE}/meetings/{meeting_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
        )
        return dict(resp.json() or {})

    async def delete_meeting(self, meeting_id: str) -> bool:
        await self._client().request(
            "DELETE",
            f"{ZOOM_API_BASE}/meetings/{meeting_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
            expected_statuses=(200, 204),
        )
        return True

    async def list_phone_call_logs(self, *, page_size: int = 30) -> list[dict[str, Any]]:
        granted = self.context.config.get("granted_scopes")
        ctx_scopes = list(self.context.scopes or ())
        effective_scopes = granted if isinstance(granted, list) else ctx_scopes
        if effective_scopes and not any("phone" in str(s).lower() for s in effective_scopes):
            raise CapabilityUnsupported(
                "Zoom Phone call-log read requires 'phone:read:list_call_logs' scope",
                provider=self.slug,
            )
        try:
            resp = await self._client().request(
                "GET",
                f"{ZOOM_API_BASE}/phone/call_logs",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=await self._headers(),
                params={"page_size": min(max(1, page_size), 100)},
            )
        except AuthExpired as exc:
            if exc.upstream_status == 403 or "scope" in exc.safe_message.lower():
                raise CapabilityUnsupported(
                    "Zoom Phone scope is not granted on this connection",
                    provider=self.slug,
                ) from exc
            raise
        return list((resp.json() or {}).get("call_logs") or [])

    async def send_chat_message(
        self, *, to_channel: str = "", to_contact: str = "", message: str
    ) -> str:
        if not (to_channel or to_contact):
            raise IntegrationV2Error("Zoom chat requires 'to_channel' or 'to_contact'", provider=self.slug)
        body: dict[str, Any] = {"message": message}
        if to_channel:
            body["to_channel"] = to_channel
        else:
            body["to_contact"] = to_contact

        resp = await self._client().request(
            "POST",
            f"{ZOOM_API_BASE}/chat/users/me/messages",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=await self._headers(),
            json_body=body,
        )
        return str((resp.json() or {}).get("id") or "")
