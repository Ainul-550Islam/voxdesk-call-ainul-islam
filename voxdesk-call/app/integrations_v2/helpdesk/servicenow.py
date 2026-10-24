"""ServiceNow Table API (``incident``, ``sys_user``, ``interaction``) adapter."""

from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
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

# ServiceNow Incident state codes: 1=New, 2=In Progress, 3=On Hold, 6=Resolved, 7=Closed
_SN_STATE_TO_CANON: dict[str, TicketStatus] = {
    "1": TicketStatus.NEW,
    "2": TicketStatus.OPEN,
    "3": TicketStatus.PENDING,
    "6": TicketStatus.RESOLVED,
    "7": TicketStatus.CLOSED,
}

_CANON_TO_SN_STATE: dict[TicketStatus, str] = {
    TicketStatus.NEW: "1",
    TicketStatus.OPEN: "2",
    TicketStatus.PENDING: "3",
    TicketStatus.RESOLVED: "6",
    TicketStatus.CLOSED: "7",
}

# ServiceNow impact + urgency -> priority: 1=Critical, 2=High, 3=Moderate, 4=Low
_CANON_TO_SN_IMPACT_URGENCY: dict[TicketPriority, tuple[str, str]] = {
    TicketPriority.URGENT: ("1", "1"),
    TicketPriority.HIGH: ("2", "2"),
    TicketPriority.NORMAL: ("2", "3"),
    TicketPriority.LOW: ("3", "3"),
}

_SN_PRIORITY_TO_CANON: dict[str, TicketPriority] = {
    "1": TicketPriority.URGENT,
    "2": TicketPriority.HIGH,
    "3": TicketPriority.NORMAL,
    "4": TicketPriority.LOW,
    "5": TicketPriority.LOW,
}


class ServiceNowProvider(HelpdeskProviderV2):
    slug = "servicenow"
    display_name = "ServiceNow ITSM"
    auth_kind = AuthKind.OAUTH2_OR_API_KEY
    default_scopes = ("useraccount",)
    required_config_fields = ("instance_url",)
    docs_url = "https://developer.servicenow.com/dev.do#!/reference/api/latest/rest/c_TableAPI"
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
        base = (
            self.context.instance_url
            or str(self.context.config.get("instance_url") or "")
        ).rstrip("/")
        if not base:
            raise IntegrationV2Error(
                "ServiceNow connection requires 'instance_url' (https://<instance>.service-now.com)",
                provider=self.slug,
            )
        validate_vendor_url(base, provider=self.slug)
        return base

    def _auth_headers(self) -> dict[str, str]:
        creds = self.context.credentials
        token = str(creds.get("access_token") or "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}
        username = str(creds.get("username") or "").strip()
        password = str(creds.get("password") or creds.get("api_key") or "").strip()
        if username and password:
            b64 = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {b64}"}
        raise AuthExpired("ServiceNow credentials missing OAuth access_token or integration user credentials", provider=self.slug)

    def _val(self, field_val: Any) -> str:
        if isinstance(field_val, dict):
            return str(field_val.get("value") or field_val.get("display_value") or "")
        return str(field_val or "")

    def _parse_incident(self, raw: dict[str, Any]) -> HelpdeskTicket:
        sys_id = self._val(raw.get("sys_id"))
        state_code = self._val(raw.get("state")) or "1"
        prio_code = self._val(raw.get("priority")) or "3"
        return HelpdeskTicket(
            external_id=sys_id,
            provider=self.slug,
            subject=self._val(raw.get("short_description")),
            description=self._val(raw.get("description")),
            status=_SN_STATE_TO_CANON.get(state_code, TicketStatus.OPEN),
            priority=_SN_PRIORITY_TO_CANON.get(prio_code, TicketPriority.NORMAL),
            requester_id=self._val(raw.get("caller_id")),
            url=f"{self._base_url()}/nav_to.do?uri=incident.do?sys_id={sys_id}" if sys_id else "",
            updated_at=self._val(raw.get("sys_updated_on")),
            custom_fields={"number": self._val(raw.get("number"))},
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/now/table/sys_user",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params={"sysparm_limit": 1, "sysparm_fields": "sys_id,user_name,email"},
        )
        rows = (resp.json() or {}).get("result") or []
        first = rows[0] if rows else {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(first.get("sys_id") or self._base_url()),
            account_name=str(first.get("user_name") or self._base_url()),
            detail="table_api=reachable",
        )

    async def find_or_create_requester(
        self, *, name: str, email: str = "", phone: str = ""
    ) -> HelpdeskUser:
        base = self._base_url()
        query_parts = []
        if email:
            query_parts.append(f"email={email}")
        elif phone:
            query_parts.append(f"phone={phone}^ORmobile_phone={phone}")
        if query_parts:
            resp = await self._client().request(
                "GET",
                f"{base}/api/now/table/sys_user",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._auth_headers(),
                params={
                    "sysparm_query": "^".join(query_parts),
                    "sysparm_limit": 1,
                    "sysparm_fields": "sys_id,name,email,phone",
                },
            )
            rows = (resp.json() or {}).get("result") or []
            if rows:
                u = rows[0]
                return HelpdeskUser(
                    external_id=str(u.get("sys_id") or ""),
                    name=str(u.get("name") or name),
                    email=str(u.get("email") or email),
                    phone=str(u.get("phone") or phone),
                )

        parts = (name or "VoxDesk Caller").strip().split(None, 1)
        body: dict[str, Any] = {
            "first_name": parts[0],
            "last_name": parts[1] if len(parts) > 1 else "Caller",
        }
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        resp = await self._client().request(
            "POST",
            f"{base}/api/now/table/sys_user",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params={"sysparm_exclude_reference_link": "true"},
            json_body=body,
        )
        u = (resp.json() or {}).get("result") or {}
        return HelpdeskUser(
            external_id=str(u.get("sys_id") or ""),
            name=name,
            email=email,
            phone=phone,
        )

    async def create_ticket(self, data: TicketCreateInput) -> HelpdeskTicket:
        impact, urgency = _CANON_TO_SN_IMPACT_URGENCY.get(data.priority, ("2", "3"))
        body: dict[str, Any] = {
            "short_description": data.subject,
            "description": data.description,
            "state": _CANON_TO_SN_STATE.get(data.status, "1"),
            "impact": impact,
            "urgency": urgency,
            "contact_type": "phone",
        }
        if data.custom_fields:
            body.update(data.custom_fields)
        if data.requester_email or data.requester_phone:
            caller = await self.find_or_create_requester(
                name=data.requester_name or data.requester_email or data.requester_phone,
                email=data.requester_email,
                phone=data.requester_phone,
            )
            if caller.external_id:
                body["caller_id"] = caller.external_id

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/now/table/incident",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params={"sysparm_exclude_reference_link": "true"},
            json_body=body,
        )
        return self._parse_incident((resp.json() or {}).get("result") or {})

    async def get_ticket(self, ticket_id: str) -> HelpdeskTicket:
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/now/table/incident/{ticket_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params={"sysparm_exclude_reference_link": "true"},
        )
        return self._parse_incident((resp.json() or {}).get("result") or {})

    async def update_ticket(
        self, ticket_id: str, data: TicketUpdateInput
    ) -> HelpdeskTicket:
        patch: dict[str, Any] = {}
        if data.subject is not None:
            patch["short_description"] = data.subject
        if data.status is not None:
            patch["state"] = _CANON_TO_SN_STATE[data.status]
        if data.priority is not None:
            imp, urg = _CANON_TO_SN_IMPACT_URGENCY[data.priority]
            patch["impact"] = imp
            patch["urgency"] = urg
        if data.comment_body is not None:
            target_col = "comments" if data.comment_public else "work_notes"
            patch[target_col] = data.comment_body
        if data.custom_fields:
            patch.update(data.custom_fields)

        resp = await self._client().request(
            "PATCH",
            f"{self._base_url()}/api/now/table/incident/{ticket_id}",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params={"sysparm_exclude_reference_link": "true"},
            json_body=patch,
        )
        return self._parse_incident((resp.json() or {}).get("result") or {})

    async def add_comment(
        self, ticket_id: str, *, body: str, public: bool = False
    ) -> HelpdeskTicketComment:
        updated = await self.update_ticket(
            ticket_id, TicketUpdateInput(comment_body=body, comment_public=public)
        )
        return HelpdeskTicketComment(
            external_id=f"{updated.external_id}:note",
            ticket_id=updated.external_id,
            body=body,
            public=public,
        )

    async def search_tickets(
        self, *, email: str = "", phone: str = "", query: str = "", limit: int = 10
    ) -> list[HelpdeskTicket]:
        clauses: list[str] = []
        if email:
            clauses.append(f"caller_id.email={email}")
        if phone:
            clauses.append(f"caller_id.phone={phone}")
        if query:
            clauses.append(f"short_descriptionLIKE{query}")
        sysparm_query = "^".join(clauses) + "^ORDERBYDESCsys_updated_on" if clauses else "ORDERBYDESCsys_updated_on"
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/now/table/incident",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._auth_headers(),
            params={
                "sysparm_query": sysparm_query,
                "sysparm_limit": min(max(1, limit), 100),
                "sysparm_exclude_reference_link": "true",
            },
        )
        rows = (resp.json() or {}).get("result") or []
        return [self._parse_incident(r) for r in rows[:limit]]

    async def log_voice_call(self, payload: dict[str, Any]) -> str:
        existing_tid = str(payload.get("external_ticket_id") or "").strip()
        duration = int(payload.get("duration_seconds") or 0)
        summary = str(payload.get("summary") or "")
        rec_url = str(payload.get("recording_url") or "")
        note_lines = [
            f"[VoxDesk Voice Call {payload.get('call_id', '')}]",
            f"Direction: {payload.get('direction', 'inbound')} | Duration: {duration}s | Disposition: {payload.get('disposition', 'completed')}",
            f"Summary: {summary}",
        ]
        if rec_url:
            note_lines.append(f"Signed Recording URL: {rec_url}")
        note_text = "\n".join(note_lines)

        if existing_tid:
            await self.add_comment(existing_tid, body=note_text, public=False)
            return existing_tid

        created = await self.create_ticket(
            TicketCreateInput(
                subject=str(payload.get("subject") or f"Voice Call ({payload.get('caller_phone') or 'Inbound'})"),
                description=note_text,
                requester_email=str(payload.get("caller_email") or ""),
                requester_phone=str(payload.get("caller_phone") or ""),
            )
        )
        return created.external_id

    def iter_updated_tickets(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        base = self._base_url()
        start_offset = int(cursor) if (cursor and cursor.isdigit()) else 0
        watermark = cursor if (cursor and not cursor.isdigit()) else ""

        async def _fetch_offset(offset: int, limit: int) -> list[ExternalSyncRecord]:
            q = f"sys_updated_on>{watermark}^ORDERBYsys_updated_on" if watermark else "ORDERBYsys_updated_on"
            resp = await self._client().request(
                "GET",
                f"{base}/api/now/table/incident",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._auth_headers(),
                params={
                    "sysparm_query": q,
                    "sysparm_offset": offset,
                    "sysparm_limit": limit,
                    "sysparm_exclude_reference_link": "true",
                },
            )
            items: list[ExternalSyncRecord] = []
            for raw in (resp.json() or {}).get("result") or []:
                ts_raw = self._val(raw.get("sys_updated_on"))
                dt = None
                if ts_raw:
                    try:
                        dt = datetime.fromisoformat(ts_raw.replace(" ", "T")).replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        dt = None
                items.append(
                    ExternalSyncRecord(
                        external_id=self._val(raw.get("sys_id")),
                        entity_type="Ticket",
                        raw_fields=raw,
                        external_updated_at=dt,
                    )
                )
            return items

        return iterate_offset_pages(_fetch_offset, page_size=100, start_offset=start_offset)
