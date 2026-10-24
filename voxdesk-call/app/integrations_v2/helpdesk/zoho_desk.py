"""Zoho Desk REST API v1 (multi-DC + ``orgId`` header + ``/api/v1/calls``) adapter."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncIterator

from app.integrations_v2.core.errors import AuthExpired, IntegrationV2Error
from app.integrations_v2.core.http_client import Int2HttpClient, validate_vendor_url
from app.integrations_v2.core.pagination import PageBatch, iterate_offset_pages
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
from app.integrations_v2.settings import ZOHO_DESK_API_HOSTS

_ZD_STATUS_MAP: dict[str, TicketStatus] = {
    "open": TicketStatus.OPEN,
    "on hold": TicketStatus.PENDING,
    "escalated": TicketStatus.OPEN,
    "closed": TicketStatus.CLOSED,
}
_CANON_TO_ZDESK_STATUS: dict[TicketStatus, str] = {
    TicketStatus.NEW: "Open",
    TicketStatus.OPEN: "Open",
    TicketStatus.PENDING: "On Hold",
    TicketStatus.RESOLVED: "Closed",
    TicketStatus.CLOSED: "Closed",
}
_CANON_TO_ZDESK_PRIO: dict[TicketPriority, str] = {
    TicketPriority.LOW: "Low",
    TicketPriority.NORMAL: "Medium",
    TicketPriority.HIGH: "High",
    TicketPriority.URGENT: "Urgent",
}


class ZohoDeskProvider(HelpdeskProviderV2):
    slug = "zoho_desk"
    display_name = "Zoho Desk"
    auth_kind = AuthKind.OAUTH2
    default_scopes = (
        "Desk.tickets.ALL",
        "Desk.contacts.ALL",
        "Desk.tasks.ALL",
        "Desk.search.READ",
        "Desk.settings.READ",
    )
    required_config_fields = ("org_id", "department_id")
    docs_url = "https://desk.zoho.com/DeskAPIDocument"
    capabilities = frozenset(
        {
            Capability.TICKET_READ,
            Capability.TICKET_WRITE,
            Capability.TICKET_COMMENT,
            Capability.VOICE_CALL_LOG,
            Capability.CONTACT_READ,
            Capability.CONTACT_WRITE,
            Capability.INCREMENTAL_SYNC,
            Capability.MID_CALL_TOOLS,
        }
    )

    def _client(self) -> Int2HttpClient:
        return self._http_client or Int2HttpClient()

    def _base_url(self) -> str:
        reg = (self.context.region or str(self.context.config.get("region") or "us")).lower()
        host = ZOHO_DESK_API_HOSTS.get(reg)
        if not host:
            raise IntegrationV2Error(f"Unsupported Zoho Desk region {reg!r}", provider=self.slug)
        url = f"https://{host}"
        validate_vendor_url(url, provider=self.slug)
        return url

    def _headers(self, *, require_org: bool = True) -> dict[str, str]:
        token = str(self.context.credentials.get("access_token") or "").strip()
        if not token:
            raise AuthExpired("Zoho Desk OAuth access_token is missing", provider=self.slug)
        headers = {"Authorization": f"Zoho-oauthtoken {token}"}
        org_id = str(self.context.config.get("org_id") or self.context.external_account_id or "").strip()
        if org_id and org_id != "default":
            headers["orgId"] = org_id
        elif require_org:
            raise IntegrationV2Error("Zoho Desk requires 'org_id' in connection config", provider=self.slug)
        return headers

    def _parse_ticket(self, raw: dict[str, Any]) -> HelpdeskTicket:
        tid = str(raw.get("id") or "")
        st_str = str(raw.get("status") or "Open").lower()
        pr_str = str(raw.get("priority") or "Medium").lower()
        prio = (
            TicketPriority.URGENT
            if pr_str == "urgent"
            else TicketPriority.HIGH
            if pr_str == "high"
            else TicketPriority.LOW
            if pr_str == "low"
            else TicketPriority.NORMAL
        )
        return HelpdeskTicket(
            external_id=tid,
            provider=self.slug,
            subject=str(raw.get("subject") or ""),
            description=str(raw.get("description") or ""),
            status=_ZD_STATUS_MAP.get(st_str, TicketStatus.OPEN),
            priority=prio,
            requester_id=str(raw.get("contactId") or ""),
            url=str(raw.get("webUrl") or ""),
            updated_at=str(raw.get("modifiedTime") or ""),
            custom_fields=dict(raw.get("cf") or {}),
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v1/organizations",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(require_org=False),
        )
        orgs = (resp.json() or {}).get("data") or []
        first = orgs[0] if orgs else {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(first.get("id") or ""),
            account_name=str(first.get("companyName") or ""),
            detail=f"region={self.context.region}",
        )

    async def find_or_create_requester(
        self, *, name: str, email: str = "", phone: str = ""
    ) -> HelpdeskUser:
        base = self._base_url()
        if email or phone:
            params: dict[str, Any] = {"limit": 1}
            if email:
                params["email"] = email
            elif phone:
                params["phone"] = phone
            resp = await self._client().request(
                "GET",
                f"{base}/api/v1/contacts/search",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                params=params,
                expected_statuses=(200, 204),
            )
            if resp.status_code == 200:
                rows = (resp.json() or {}).get("data") or []
                if rows:
                    c = rows[0]
                    return HelpdeskUser(
                        external_id=str(c.get("id") or ""),
                        name=f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or name,
                        email=str(c.get("email") or email),
                        phone=str(c.get("phone") or phone),
                    )

        parts = (name or "VoxDesk Caller").strip().split(None, 1)
        body: dict[str, Any] = {
            "firstName": parts[0] if len(parts) > 1 else "",
            "lastName": parts[-1],
        }
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        resp = await self._client().request(
            "POST",
            f"{base}/api/v1/contacts",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body=body,
        )
        c = resp.json() or {}
        return HelpdeskUser(
            external_id=str(c.get("id") or ""),
            name=name,
            email=email,
            phone=phone,
        )

    async def create_ticket(self, data: TicketCreateInput) -> HelpdeskTicket:
        dept_id = str(self.context.config.get("department_id") or "").strip()
        if not dept_id:
            raise IntegrationV2Error("Zoho Desk requires 'department_id' in config", provider=self.slug)

        contact = await self.find_or_create_requester(
            name=data.requester_name or data.requester_email or data.requester_phone or "Caller",
            email=data.requester_email,
            phone=data.requester_phone,
        )
        body: dict[str, Any] = {
            "subject": data.subject,
            "description": data.description,
            "departmentId": dept_id,
            "contactId": contact.external_id,
            "status": _CANON_TO_ZDESK_STATUS.get(data.status, "Open"),
            "priority": _CANON_TO_ZDESK_PRIO.get(data.priority, "Medium"),
            "channel": "Phone",
        }
        if data.custom_fields:
            body["cf"] = dict(data.custom_fields)

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/v1/tickets",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body=body,
        )
        return self._parse_ticket(resp.json() or {})

    async def get_ticket(self, ticket_id: str) -> HelpdeskTicket:
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v1/tickets/{ticket_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
        )
        return self._parse_ticket(resp.json() or {})

    async def update_ticket(
        self, ticket_id: str, data: TicketUpdateInput
    ) -> HelpdeskTicket:
        patch: dict[str, Any] = {}
        if data.subject is not None:
            patch["subject"] = data.subject
        if data.status is not None:
            patch["status"] = _CANON_TO_ZDESK_STATUS[data.status]
        if data.priority is not None:
            patch["priority"] = _CANON_TO_ZDESK_PRIO[data.priority]
        if data.custom_fields is not None:
            patch["cf"] = dict(data.custom_fields)

        ticket = (
            await self.get_ticket(ticket_id)
            if not patch
            else self._parse_ticket(
                (
                    await self._client().request(
                        "PATCH",
                        f"{self._base_url()}/api/v1/tickets/{ticket_id}",
                        provider=self.slug,
                        tenant_id=self.context.tenant_id,
                        connection_id=self.context.connection_id,
                        headers=self._headers(),
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
            f"{self._base_url()}/api/v1/tickets/{ticket_id}/comments",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={"content": body, "isPublic": public},
        )
        c = resp.json() or {}
        return HelpdeskTicketComment(
            external_id=str(c.get("id") or ""),
            ticket_id=ticket_id,
            body=body,
            public=public,
        )

    async def search_tickets(
        self, *, email: str = "", phone: str = "", query: str = "", limit: int = 10
    ) -> list[HelpdeskTicket]:
        params: dict[str, Any] = {"limit": min(max(1, limit), 100)}
        if email:
            params["email"] = email
        if phone:
            params["phone"] = phone
        if query:
            params["subject"] = query
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v1/tickets/search",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            params=params,
            expected_statuses=(200, 204),
        )
        if resp.status_code == 204:
            return []
        rows = (resp.json() or {}).get("data") or []
        return [self._parse_ticket(r) for r in rows[:limit]]

    async def log_voice_call(self, payload: dict[str, Any]) -> str:
        dept_id = str(self.context.config.get("department_id") or "").strip()
        contact = await self.find_or_create_requester(
            name=str(payload.get("caller_phone") or payload.get("caller_email") or "Caller"),
            email=str(payload.get("caller_email") or ""),
            phone=str(payload.get("caller_phone") or ""),
        )
        desc = str(payload.get("summary") or "")
        if payload.get("recording_url"):
            desc = f"{desc}\nRecording: {payload['recording_url']}".strip()

        call_body: dict[str, Any] = {
            "contactId": contact.external_id,
            "departmentId": dept_id,
            "subject": str(payload.get("subject") or "VoxDesk Voice Call"),
            "description": desc,
            "direction": "inbound" if payload.get("direction", "inbound") == "inbound" else "outbound",
            "duration": int(payload.get("duration_seconds") or 0),
            "status": "Completed",
            "startTime": str(payload.get("occurred_at") or ""),
        }
        if payload.get("external_ticket_id"):
            call_body["ticketId"] = str(payload["external_ticket_id"])

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/v1/calls",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body=call_body,
        )
        return str((resp.json() or {}).get("id") or "")

    def iter_updated_tickets(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        base = self._base_url()
        start_from = int(cursor) if (cursor and cursor.isdigit()) else 0

        async def _fetch_offset(offset: int, limit: int) -> list[ExternalSyncRecord]:
            resp = await self._client().request(
                "GET",
                f"{base}/api/v1/tickets",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                params={"from": offset, "limit": limit, "sortBy": "modifiedTime"},
                expected_statuses=(200, 204),
            )
            if resp.status_code == 204:
                return []
            items: list[ExternalSyncRecord] = []
            for raw in (resp.json() or {}).get("data") or []:
                ts_str = str(raw.get("modifiedTime") or "")
                dt = None
                if ts_str:
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
            return items

        return iterate_offset_pages(_fetch_offset, page_size=50, start_offset=start_from)
