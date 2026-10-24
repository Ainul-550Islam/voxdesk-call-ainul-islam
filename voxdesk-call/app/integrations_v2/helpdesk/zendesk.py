"""Zendesk Support REST API v2 + Talk Partner Edition voice comment adapter."""

from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.integrations_v2.core.errors import AuthExpired, IntegrationV2Error
from app.integrations_v2.core.http_client import Int2HttpClient, validate_vendor_url
from app.integrations_v2.core.pagination import PageBatch, iterate_cursor_pages
from app.integrations_v2.core.provider_base import (
    AuthKind,
    Capability,
    HealthStatus,
)
from app.integrations_v2.core.sync_engine import ExternalSyncRecord
from app.integrations_v2.helpdesk.models import (
    HelpdeskProviderV2,
    HelpdeskTicket,
    HelpdeskTicketComment,
    HelpdeskUser,
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
    TicketUpdateInput,
)

_ZD_STATUS_TO_CANON: dict[str, TicketStatus] = {
    "new": TicketStatus.NEW,
    "open": TicketStatus.OPEN,
    "pending": TicketStatus.PENDING,
    "hold": TicketStatus.PENDING,
    "solved": TicketStatus.RESOLVED,
    "closed": TicketStatus.CLOSED,
}

_CANON_TO_ZD_STATUS: dict[TicketStatus, str] = {
    TicketStatus.NEW: "new",
    TicketStatus.OPEN: "open",
    TicketStatus.PENDING: "pending",
    TicketStatus.RESOLVED: "solved",
    TicketStatus.CLOSED: "closed",
}


class ZendeskProvider(HelpdeskProviderV2):
    slug = "zendesk"
    display_name = "Zendesk Support"
    auth_kind = AuthKind.OAUTH2_OR_API_KEY
    default_scopes = ("read", "write", "tickets:read", "tickets:write", "users:read", "users:write")
    required_config_fields = ("subdomain",)
    docs_url = "https://developer.zendesk.com/api-reference/ticketing/introduction/"
    capabilities = frozenset(
        {
            Capability.TICKET_READ,
            Capability.TICKET_WRITE,
            Capability.TICKET_COMMENT,
            Capability.VOICE_CALL_LOG,
            Capability.CONTACT_READ,
            Capability.CONTACT_WRITE,
            Capability.INCREMENTAL_SYNC,
            Capability.WEBHOOK_INBOUND,
            Capability.MID_CALL_TOOLS,
        }
    )

    def _client(self) -> Int2HttpClient:
        return self._http_client or Int2HttpClient()

    def _base_url(self) -> str:
        if self.context.instance_url:
            base = self.context.instance_url.rstrip("/")
        else:
            sub = str(self.context.config.get("subdomain") or "").strip().lower()
            if not sub:
                raise IntegrationV2Error("Zendesk connection missing 'subdomain'", provider=self.slug)
            base = f"https://{sub}.zendesk.com"
        validate_vendor_url(base, provider=self.slug)
        return base

    def _auth_headers(self) -> dict[str, str]:
        creds = self.context.credentials
        token = str(creds.get("access_token") or "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}
        email = str(creds.get("email") or self.context.config.get("email") or "").strip()
        api_token = str(creds.get("api_token") or creds.get("api_key") or "").strip()
        if email and api_token:
            raw = f"{email}/token:{api_token}".encode("utf-8")
            encoded = base64.b64encode(raw).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        raise AuthExpired("Zendesk credentials missing OAuth access_token or email+api_token", provider=self.slug)

    def _parse_ticket(self, raw: dict[str, Any]) -> HelpdeskTicket:
        tid = str(raw.get("id") or "")
        prio_raw = str(raw.get("priority") or "normal").lower()
        prio = (
            TicketPriority(prio_raw)
            if prio_raw in {"low", "normal", "high", "urgent"}
            else TicketPriority.NORMAL
        )
        status = _ZD_STATUS_TO_CANON.get(
            str(raw.get("status") or "open").lower(), TicketStatus.OPEN
        )
        custom_map: dict[str, Any] = {}
        for cf in raw.get("custom_fields") or []:
            if isinstance(cf, dict) and cf.get("id") is not None:
                custom_map[str(cf["id"])] = cf.get("value")
        return HelpdeskTicket(
            external_id=tid,
            provider=self.slug,
            subject=str(raw.get("subject") or ""),
            description=str(raw.get("description") or ""),
            status=status,
            priority=prio,
            requester_id=str(raw.get("requester_id") or ""),
            url=f"{self._base_url()}/agent/tickets/{tid}" if tid else "",
            updated_at=str(raw.get("updated_at") or ""),
            tags=[str(t) for t in (raw.get("tags") or [])],
            custom_fields=custom_map,
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v2/users/me.json",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
        )
        user = (resp.json() or {}).get("user") or {}
        if not user.get("id"):
            raise AuthExpired("Zendesk /users/me returned anonymous user", provider=self.slug)
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(user.get("id")),
            account_name=str(user.get("name") or user.get("email") or ""),
            detail=f"role={user.get('role', 'agent')}",
        )

    async def find_or_create_requester(
        self, *, name: str, email: str = "", phone: str = ""
    ) -> HelpdeskUser:
        base = self._base_url()
        query = email or phone or name
        if query:
            resp = await self._client().request(
                "GET",
                f"{base}/api/v2/users/search.json",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._auth_headers(),
                params={"query": query},
            )
            users = (resp.json() or {}).get("users") or []
            if users:
                u = users[0]
                return HelpdeskUser(
                    external_id=str(u["id"]),
                    name=str(u.get("name") or name),
                    email=str(u.get("email") or email),
                    phone=str(u.get("phone") or phone),
                )

        user_payload: dict[str, Any] = {"name": name or email or phone or "VoxDesk Caller"}
        if email:
            user_payload["email"] = email
        if phone:
            user_payload["phone"] = phone
        resp = await self._client().request(
            "POST",
            f"{base}/api/v2/users/create_or_update.json",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            json_body={"user": user_payload},
        )
        u = (resp.json() or {}).get("user") or {}
        return HelpdeskUser(
            external_id=str(u.get("id") or ""),
            name=str(u.get("name") or name),
            email=str(u.get("email") or email),
            phone=str(u.get("phone") or phone),
        )

    async def create_ticket(self, data: TicketCreateInput) -> HelpdeskTicket:
        ticket_obj: dict[str, Any] = {
            "subject": data.subject,
            "comment": {"body": data.description, "public": False},
            "priority": data.priority.value,
            "status": _CANON_TO_ZD_STATUS.get(data.status, "open"),
        }
        if data.tags:
            ticket_obj["tags"] = list(data.tags)
        if data.custom_fields:
            ticket_obj["custom_fields"] = [
                {"id": int(k) if str(k).isdigit() else k, "value": v}
                for k, v in data.custom_fields.items()
            ]
        if data.requester_email or data.requester_phone:
            requester = await self.find_or_create_requester(
                name=data.requester_name or data.requester_email or data.requester_phone,
                email=data.requester_email,
                phone=data.requester_phone,
            )
            if requester.external_id:
                ticket_obj["requester_id"] = int(requester.external_id) if requester.external_id.isdigit() else requester.external_id

        headers = self._auth_headers()
        if data.idempotency_key:
            headers["Idempotency-Key"] = data.idempotency_key[:64]

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/v2/tickets.json",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=headers,
            json_body={"ticket": ticket_obj},
        )
        return self._parse_ticket((resp.json() or {}).get("ticket") or {})

    async def get_ticket(self, ticket_id: str) -> HelpdeskTicket:
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v2/tickets/{ticket_id}.json",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
        )
        return self._parse_ticket((resp.json() or {}).get("ticket") or {})

    async def update_ticket(
        self, ticket_id: str, data: TicketUpdateInput
    ) -> HelpdeskTicket:
        patch: dict[str, Any] = {}
        if data.subject is not None:
            patch["subject"] = data.subject
        if data.status is not None:
            patch["status"] = _CANON_TO_ZD_STATUS[data.status]
        if data.priority is not None:
            patch["priority"] = data.priority.value
        if data.comment_body is not None:
            patch["comment"] = {"body": data.comment_body, "public": data.comment_public}
        if data.tags is not None:
            patch["tags"] = list(data.tags)
        if data.custom_fields is not None:
            patch["custom_fields"] = [
                {"id": int(k) if str(k).isdigit() else k, "value": v}
                for k, v in data.custom_fields.items()
            ]
        resp = await self._client().request(
            "PUT",
            f"{self._base_url()}/api/v2/tickets/{ticket_id}.json",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            json_body={"ticket": patch},
        )
        return self._parse_ticket((resp.json() or {}).get("ticket") or {})

    async def add_comment(
        self, ticket_id: str, *, body: str, public: bool = False
    ) -> HelpdeskTicketComment:
        updated = await self.update_ticket(
            ticket_id, TicketUpdateInput(comment_body=body, comment_public=public)
        )
        return HelpdeskTicketComment(
            external_id=f"{updated.external_id}:comment",
            ticket_id=updated.external_id,
            body=body,
            public=public,
        )

    async def search_tickets(
        self, *, email: str = "", phone: str = "", query: str = "", limit: int = 10
    ) -> list[HelpdeskTicket]:
        terms = ["type:ticket"]
        if email:
            terms.append(f"requester:{email}")
        if phone:
            terms.append(phone)
        if query:
            terms.append(query)
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v2/search.json",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params={"query": " ".join(terms), "per_page": min(max(1, limit), 100)},
        )
        results = (resp.json() or {}).get("results") or []
        return [self._parse_ticket(item) for item in results[:limit]]

    async def log_voice_call(self, payload: dict[str, Any]) -> str:
        """Log completed VoxDesk call via Zendesk ``voice_comment`` on an existing or new ticket."""
        existing_tid = str(payload.get("external_ticket_id") or "").strip()
        duration = int(payload.get("duration_seconds") or 0)
        summary = str(payload.get("summary") or "VoxDesk AI voice call completed.")
        rec_url = str(payload.get("recording_url") or "")
        caller_phone = str(payload.get("caller_phone") or "")
        occurred_at = str(payload.get("occurred_at") or datetime.now(timezone.utc).isoformat())

        voice_comment: dict[str, Any] = {
            "from": caller_phone or "unknown",
            "to": "voxdesk",
            "call_duration": duration,
            "started_at": occurred_at,
            "body": summary,
            "public": False,
        }
        if rec_url:
            voice_comment["recording_url"] = rec_url

        headers = self._auth_headers()
        if payload.get("idempotency_key"):
            headers["Idempotency-Key"] = str(payload["idempotency_key"])[:64]

        if existing_tid:
            resp = await self._client().request(
                "PUT",
                f"{self._base_url()}/api/v2/tickets/{existing_tid}.json",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=headers,
                json_body={"ticket": {"voice_comment": voice_comment}},
            )
            t = (resp.json() or {}).get("ticket") or {}
            return str(t.get("id") or existing_tid)

        ticket_payload: dict[str, Any] = {
            "subject": str(payload.get("subject") or f"Voice Call ({caller_phone or 'Inbound'})"),
            "voice_comment": voice_comment,
            "tags": ["voxdesk", "voice-call"],
        }
        if payload.get("caller_email") or caller_phone:
            req = await self.find_or_create_requester(
                name=caller_phone or str(payload.get("caller_email")),
                email=str(payload.get("caller_email") or ""),
                phone=caller_phone,
            )
            if req.external_id and req.external_id.isdigit():
                ticket_payload["requester_id"] = int(req.external_id)

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/v2/tickets.json",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=headers,
            json_body={"ticket": ticket_payload},
        )
        t = (resp.json() or {}).get("ticket") or {}
        return str(t.get("id") or "")

    def iter_updated_tickets(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        base = self._base_url()

        async def _fetch(cur: str | None) -> PageBatch[ExternalSyncRecord]:
            params: dict[str, Any] = (
                {"cursor": cur} if cur else {"start_time": 0}
            )
            resp = await self._client().request(
                "GET",
                f"{base}/api/v2/incremental/tickets/cursor.json",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._auth_headers(),
                params=params,
            )
            body = resp.json() or {}
            items: list[ExternalSyncRecord] = []
            for raw in body.get("tickets") or []:
                ts_str = str(raw.get("updated_at") or "")
                ext_dt = None
                if ts_str:
                    try:
                        ext_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        ext_dt = None
                items.append(
                    ExternalSyncRecord(
                        external_id=str(raw.get("id")),
                        entity_type="Ticket",
                        raw_fields=raw,
                        external_updated_at=ext_dt,
                    )
                )
            after_cursor = str(body.get("after_cursor") or "") or None
            end_of_stream = bool(body.get("end_of_stream", True))
            return PageBatch(
                items=items,
                next_cursor=after_cursor,
                has_more=not end_of_stream and bool(after_cursor),
            )

        return iterate_cursor_pages(_fetch, initial_cursor=cursor)
