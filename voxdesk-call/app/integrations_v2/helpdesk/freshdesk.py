"""Freshdesk REST API v2 (``/api/v2/tickets``, ``/api/v2/contacts``, ``/notes``) adapter."""

from __future__ import annotations

import base64
import time
from datetime import datetime
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

# Freshdesk numeric status: 2=Open, 3=Pending, 4=Resolved, 5=Closed
_FD_STATUS_TO_CANON: dict[int, TicketStatus] = {
    2: TicketStatus.OPEN,
    3: TicketStatus.PENDING,
    4: TicketStatus.RESOLVED,
    5: TicketStatus.CLOSED,
}
_CANON_TO_FD_STATUS: dict[TicketStatus, int] = {
    TicketStatus.NEW: 2,
    TicketStatus.OPEN: 2,
    TicketStatus.PENDING: 3,
    TicketStatus.RESOLVED: 4,
    TicketStatus.CLOSED: 5,
}

# Freshdesk numeric priority: 1=Low, 2=Medium, 3=High, 4=Urgent
_FD_PRIO_TO_CANON: dict[int, TicketPriority] = {
    1: TicketPriority.LOW,
    2: TicketPriority.NORMAL,
    3: TicketPriority.HIGH,
    4: TicketPriority.URGENT,
}
_CANON_TO_FD_PRIO: dict[TicketPriority, int] = {
    TicketPriority.LOW: 1,
    TicketPriority.NORMAL: 2,
    TicketPriority.HIGH: 3,
    TicketPriority.URGENT: 4,
}


class FreshdeskProvider(HelpdeskProviderV2):
    slug = "freshdesk"
    display_name = "Freshdesk"
    auth_kind = AuthKind.API_KEY
    required_config_fields = ("domain",)
    docs_url = "https://developers.freshdesk.com/api/"
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
            dom = str(self.context.config.get("domain") or "").strip().lower()
            if not dom:
                raise IntegrationV2Error("Freshdesk connection requires 'domain'", provider=self.slug)
            base = f"https://{dom}.freshdesk.com"
        validate_vendor_url(base, provider=self.slug)
        return base

    def _auth_headers(self) -> dict[str, str]:
        api_key = str(
            self.context.credentials.get("api_key")
            or self.context.credentials.get("access_token")
            or ""
        ).strip()
        if not api_key:
            raise AuthExpired("Freshdesk API key is missing", provider=self.slug)
        token = base64.b64encode(f"{api_key}:X".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def _parse_ticket(self, raw: dict[str, Any]) -> HelpdeskTicket:
        tid = str(raw.get("id") or "")
        st = _FD_STATUS_TO_CANON.get(int(raw.get("status") or 2), TicketStatus.OPEN)
        pr = _FD_PRIO_TO_CANON.get(int(raw.get("priority") or 2), TicketPriority.NORMAL)
        return HelpdeskTicket(
            external_id=tid,
            provider=self.slug,
            subject=str(raw.get("subject") or ""),
            description=str(raw.get("description_text") or raw.get("description") or ""),
            status=st,
            priority=pr,
            requester_id=str(raw.get("requester_id") or ""),
            url=f"{self._base_url()}/a/tickets/{tid}" if tid else "",
            updated_at=str(raw.get("updated_at") or ""),
            tags=[str(t) for t in (raw.get("tags") or [])],
            custom_fields=dict(raw.get("custom_fields") or {}),
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v2/agents/me",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
        )
        agent = resp.json() or {}
        contact = agent.get("contact") or {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(agent.get("id") or ""),
            account_name=str(contact.get("name") or contact.get("email") or ""),
            detail="freshdesk_v2=ok",
        )

    async def find_or_create_requester(
        self, *, name: str, email: str = "", phone: str = ""
    ) -> HelpdeskUser:
        base = self._base_url()
        params: dict[str, Any] = {}
        if email:
            params["email"] = email
        elif phone:
            params["phone"] = phone
        if params:
            resp = await self._client().request(
                "GET",
                f"{base}/api/v2/contacts",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._auth_headers(),
                params=params,
            )
            contacts = resp.json() or []
            if isinstance(contacts, list) and contacts:
                c = contacts[0]
                return HelpdeskUser(
                    external_id=str(c.get("id") or ""),
                    name=str(c.get("name") or name),
                    email=str(c.get("email") or email),
                    phone=str(c.get("phone") or phone),
                )

        body: dict[str, Any] = {"name": name or email or phone or "VoxDesk Caller"}
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        resp = await self._client().request(
            "POST",
            f"{base}/api/v2/contacts",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            json_body=body,
        )
        c = resp.json() or {}
        return HelpdeskUser(
            external_id=str(c.get("id") or ""),
            name=str(c.get("name") or name),
            email=str(c.get("email") or email),
            phone=str(c.get("phone") or phone),
        )

    async def create_ticket(self, data: TicketCreateInput) -> HelpdeskTicket:
        body: dict[str, Any] = {
            "subject": data.subject,
            "description": data.description,
            "status": _CANON_TO_FD_STATUS.get(data.status, 2),
            "priority": _CANON_TO_FD_PRIO.get(data.priority, 2),
            "source": 3,  # Freshdesk source 3 = Phone
        }
        if data.requester_email:
            body["email"] = data.requester_email
        if data.requester_phone:
            body["phone"] = data.requester_phone
        if data.requester_name:
            body["name"] = data.requester_name
        if not (data.requester_email or data.requester_phone):
            body["email"] = "caller@voxdesk.invalid"
        if data.tags:
            body["tags"] = list(data.tags)
        if data.custom_fields:
            body["custom_fields"] = dict(data.custom_fields)

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/v2/tickets",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            json_body=body,
        )
        return self._parse_ticket(resp.json() or {})

    async def get_ticket(self, ticket_id: str) -> HelpdeskTicket:
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v2/tickets/{ticket_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
        )
        return self._parse_ticket(resp.json() or {})

    async def update_ticket(
        self, ticket_id: str, data: TicketUpdateInput
    ) -> HelpdeskTicket:
        patch: dict[str, Any] = {}
        if data.subject is not None:
            patch["subject"] = data.subject
        if data.status is not None:
            patch["status"] = _CANON_TO_FD_STATUS[data.status]
        if data.priority is not None:
            patch["priority"] = _CANON_TO_FD_PRIO[data.priority]
        if data.tags is not None:
            patch["tags"] = list(data.tags)
        if data.custom_fields is not None:
            patch["custom_fields"] = dict(data.custom_fields)

        ticket = (
            await self.get_ticket(ticket_id)
            if not patch
            else self._parse_ticket(
                (
                    await self._client().request(
                        "PUT",
                        f"{self._base_url()}/api/v2/tickets/{ticket_id}",
                        provider=self.slug,
                        tenant_id=self.context.tenant_id,
                        connection_id=self.context.connection_id,
                        headers=self._auth_headers(),
                        json_body=patch,
                    )
                ).json()
                or {}
            )
        )
        if data.comment_body:
            await self.add_comment(
                ticket_id, body=data.comment_body, public=data.comment_public
            )
        return ticket

    async def add_comment(
        self, ticket_id: str, *, body: str, public: bool = False
    ) -> HelpdeskTicketComment:
        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/v2/tickets/{ticket_id}/notes",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            json_body={"body": body, "private": not public},
        )
        note = resp.json() or {}
        return HelpdeskTicketComment(
            external_id=str(note.get("id") or ""),
            ticket_id=ticket_id,
            body=body,
            public=public,
        )

    async def search_tickets(
        self, *, email: str = "", phone: str = "", query: str = "", limit: int = 10
    ) -> list[HelpdeskTicket]:
        params: dict[str, Any] = {"per_page": min(max(1, limit), 100)}
        if email:
            params["email"] = email
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v2/tickets",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params=params,
        )
        rows = resp.json() or []
        tickets = [self._parse_ticket(r) for r in rows if isinstance(r, dict)]
        if query:
            q_low = query.lower()
            tickets = [t for t in tickets if q_low in t.subject.lower() or q_low in t.description.lower()]
        return tickets[:limit]

    async def log_voice_call(self, payload: dict[str, Any]) -> str:
        existing_tid = str(payload.get("external_ticket_id") or "").strip()
        rec_url = str(payload.get("recording_url") or "")
        body_lines = [
            f"VoxDesk Call ({payload.get('direction', 'inbound')}) — {payload.get('duration_seconds', 0)}s",
            f"Summary: {payload.get('summary', '')}",
        ]
        if rec_url:
            body_lines.append(f"Recording: {rec_url}")
        note_body = "\n".join(body_lines)

        if existing_tid:
            await self.add_comment(existing_tid, body=note_body, public=False)
            return existing_tid

        created = await self.create_ticket(
            TicketCreateInput(
                subject=str(payload.get("subject") or f"Phone Call ({payload.get('caller_phone') or 'Inbound'})"),
                description=note_body,
                requester_email=str(payload.get("caller_email") or ""),
                requester_phone=str(payload.get("caller_phone") or ""),
                tags=["voxdesk", "phone"],
            )
        )
        return created.external_id

    def iter_updated_tickets(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        base = self._base_url()
        updated_since = cursor or "1970-01-01T00:00:00Z"

        async def _fetch(cur: str | None) -> PageBatch[ExternalSyncRecord]:
            page_num = 1
            since_ts = updated_since
            if cur and "|" in cur:
                since_ts, page_str = cur.split("|", 1)
                page_num = int(page_str)
            elif cur:
                since_ts = cur

            resp = await self._client().request(
                "GET",
                f"{base}/api/v2/tickets",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._auth_headers(),
                params={
                    "updated_since": since_ts,
                    "order_by": "updated_at",
                    "order_type": "asc",
                    "per_page": 100,
                    "page": page_num,
                },
            )
            rows = resp.json() or []
            items: list[ExternalSyncRecord] = []
            latest_ts = since_ts
            for raw in rows:
                ts_str = str(raw.get("updated_at") or "")
                dt = None
                if ts_str:
                    latest_ts = ts_str
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        dt = None
                items.append(
                    ExternalSyncRecord(
                        external_id=str(raw.get("id")),
                        entity_type="Ticket",
                        raw_fields=raw,
                        external_updated_at=dt,
                    )
                )
            has_more = len(rows) >= 100 or "link" in {k.lower() for k in resp.headers.keys()}
            next_cur = f"{since_ts}|{page_num + 1}" if has_more else latest_ts
            return PageBatch(items=items, next_cursor=next_cur, has_more=has_more)

        return iterate_cursor_pages(_fetch, initial_cursor=cursor)
