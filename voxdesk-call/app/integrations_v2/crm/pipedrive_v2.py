"""Pipedrive API v2 (``/api/v2/persons``, ``/api/v2/deals``, ``/api/v2/activities``) adapter."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncIterator

from app.integrations_v2.core.errors import AuthExpired
from app.integrations_v2.core.http_client import Int2HttpClient, validate_vendor_url
from app.integrations_v2.core.pagination import PageBatch, iterate_cursor_pages
from app.integrations_v2.core.provider_base import (
    AuthKind,
    Capability,
    HealthStatus,
)
from app.integrations_v2.core.sync_engine import ExternalSyncRecord
from app.integrations_v2.crm import CrmContactRecord, CrmDealRecord, CrmProviderV2


class PipedriveV2Provider(CrmProviderV2):
    slug = "pipedrive"
    display_name = "Pipedrive CRM (v2)"
    auth_kind = AuthKind.OAUTH2_OR_API_KEY
    default_scopes = ("contacts:full", "deals:full", "activities:full", "users:read")
    docs_url = "https://developers.pipedrive.com/docs/api/v1"
    capabilities = frozenset(
        {
            Capability.CONTACT_READ,
            Capability.CONTACT_WRITE,
            Capability.DEAL_READ,
            Capability.DEAL_WRITE,
            Capability.CALL_ACTIVITY_WRITE,
            Capability.INCREMENTAL_SYNC,
            Capability.MID_CALL_TOOLS,
        }
    )

    def _client(self) -> Int2HttpClient:
        return self._http_client or Int2HttpClient()

    def _base_url(self) -> str:
        api_dom = str(self.context.credentials.get("api_domain") or self.context.instance_url or "").rstrip("/")
        base = api_dom if api_dom else "https://api.pipedrive.com"
        validate_vendor_url(base, provider=self.slug)
        return base

    def _auth_headers_and_params(self) -> tuple[dict[str, str], dict[str, Any]]:
        creds = self.context.credentials
        oauth_tok = str(creds.get("access_token") or "").strip()
        if oauth_tok and self.context.auth_kind != "api_key":
            return {"Authorization": f"Bearer {oauth_tok}"}, {}
        api_token = str(creds.get("api_token") or creds.get("api_key") or "").strip()
        if api_token:
            return {"x-api-token": api_token}, {}
        raise AuthExpired("Pipedrive credentials missing OAuth access_token or api_token", provider=self.slug)

    def _parse_person(self, raw: dict[str, Any]) -> CrmContactRecord:
        emails = raw.get("emails") or raw.get("email") or []
        phones = raw.get("phones") or raw.get("phone") or []
        email_val = (
            emails[0].get("value", "")
            if isinstance(emails, list) and emails and isinstance(emails[0], dict)
            else str(emails[0] if isinstance(emails, list) and emails else "")
        )
        phone_val = (
            phones[0].get("value", "")
            if isinstance(phones, list) and phones and isinstance(phones[0], dict)
            else str(phones[0] if isinstance(phones, list) and phones else "")
        )
        return CrmContactRecord(
            external_id=str(raw.get("id") or ""),
            provider=self.slug,
            first_name=str(raw.get("first_name") or ""),
            last_name=str(raw.get("last_name") or ""),
            email=email_val,
            phone=phone_val,
            company=str(raw.get("org_name") or ""),
            raw=raw,
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        headers, params = self._auth_headers_and_params()
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/v1/users/me",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=headers,
            params=params,
        )
        me = (resp.json() or {}).get("data") or {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(me.get("company_id") or me.get("id") or ""),
            account_name=str(me.get("company_name") or me.get("name") or ""),
            detail="pipedrive_api_v2=ok",
        )

    async def lookup_contact(
        self, *, email: str = "", phone: str = ""
    ) -> CrmContactRecord | None:
        term = email or phone
        if not term:
            return None
        headers, params = self._auth_headers_and_params()
        params.update(
            {
                "term": term,
                "fields": "email" if email else "phone",
                "exact_match": "true",
                "limit": 1,
            }
        )
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/api/v2/persons/search",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=headers,
            params=params,
        )
        items = ((resp.json() or {}).get("data") or {}).get("items") or []
        if not items:
            return None
        person = items[0].get("item") or items[0]
        return self._parse_person(person)

    async def upsert_contact(
        self,
        *,
        first_name: str,
        last_name: str,
        email: str = "",
        phone: str = "",
        company: str = "",
        custom_fields: dict[str, Any] | None = None,
    ) -> CrmContactRecord:
        existing = await self.lookup_contact(email=email, phone=phone)
        headers, params = self._auth_headers_and_params()

        body: dict[str, Any] = {
            "name": f"{first_name} {last_name}".strip() or email or phone or "Caller",
            "first_name": first_name,
            "last_name": last_name,
        }
        if email:
            body["emails"] = [{"value": email, "primary": True, "label": "work"}]
        if phone:
            body["phones"] = [{"value": phone, "primary": True, "label": "mobile"}]
        if custom_fields:
            body["custom_fields"] = dict(custom_fields)

        if existing and existing.external_id:
            resp = await self._client().request(
                "PATCH",
                f"{self._base_url()}/api/v2/persons/{existing.external_id}",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=headers,
                params=params,
                json_body=body,
            )
        else:
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/api/v2/persons",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=headers,
                params=params,
                json_body=body,
            )
        return self._parse_person((resp.json() or {}).get("data") or {})

    async def create_or_update_deal(
        self,
        *,
        title: str,
        amount: float = 0.0,
        currency: str = "USD",
        stage: str = "",
        contact_id: str = "",
        deal_id: str = "",
    ) -> CrmDealRecord:
        headers, params = self._auth_headers_and_params()
        body: dict[str, Any] = {"title": title, "value": amount, "currency": currency}
        if contact_id and contact_id.isdigit():
            body["person_id"] = int(contact_id)
        if stage and stage.isdigit():
            body["stage_id"] = int(stage)

        if deal_id:
            resp = await self._client().request(
                "PATCH",
                f"{self._base_url()}/api/v2/deals/{deal_id}",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=headers,
                params=params,
                json_body=body,
            )
        else:
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/api/v2/deals",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=headers,
                params=params,
                json_body=body,
            )
        d = (resp.json() or {}).get("data") or {}
        return CrmDealRecord(
            external_id=str(d.get("id") or deal_id),
            provider=self.slug,
            title=str(d.get("title") or title),
            amount=float(d.get("value") or amount),
            currency=str(d.get("currency") or currency),
            stage=str(d.get("stage_id") or stage),
            contact_id=contact_id,
        )

    async def log_call_activity(self, payload: dict[str, Any]) -> str:
        headers, params = self._auth_headers_and_params()
        dur_sec = int(payload.get("duration_seconds") or 0)
        hours, rem = divmod(max(0, dur_sec), 3600)
        mins, _ = divmod(rem, 60)
        note_html = str(payload.get("summary") or "")
        if payload.get("recording_url"):
            note_html = f"{note_html}<br/>Recording: {payload['recording_url']}"

        body: dict[str, Any] = {
            "subject": str(payload.get("subject") or "VoxDesk Call"),
            "type": "call",
            "done": True,
            "duration": f"{hours:02d}:{max(1, mins) if dur_sec > 0 and hours == 0 else mins:02d}",
            "note": note_html,
        }
        ext_cid = str(payload.get("external_contact_id") or "").strip()
        if ext_cid and ext_cid.isdigit():
            body["person_id"] = int(ext_cid)

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/api/v2/activities",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=headers,
            params=params,
            json_body=body,
        )
        data = (resp.json() or {}).get("data") or {}
        return str(data.get("id") or "")

    def iter_updated_contacts(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        base = self._base_url()

        async def _fetch(cur: str | None) -> PageBatch[ExternalSyncRecord]:
            headers, params = self._auth_headers_and_params()
            params.update({"limit": 100, "sort_by": "update_time", "sort_direction": "asc"})
            if cur:
                if cur.startswith("cur:"):
                    params["cursor"] = cur[4:]
                else:
                    params["updated_since"] = cur

            resp = await self._client().request(
                "GET",
                f"{base}/api/v2/persons",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=headers,
                params=params,
            )
            body = resp.json() or {}
            items: list[ExternalSyncRecord] = []
            latest_ts = cur if (cur and not cur.startswith("cur:")) else ""
            for raw in body.get("data") or []:
                parsed_p = self._parse_person(raw)
                upd = str(raw.get("update_time") or "")
                dt = None
                if upd:
                    latest_ts = upd
                    try:
                        dt = datetime.fromisoformat(upd.replace("Z", "+00:00"))
                    except ValueError:
                        dt = None
                items.append(
                    ExternalSyncRecord(
                        external_id=parsed_p.external_id,
                        entity_type="Contact",
                        raw_fields={
                            "first_name": parsed_p.first_name,
                            "last_name": parsed_p.last_name,
                            "email": parsed_p.email,
                            "phone": parsed_p.phone,
                            "company": parsed_p.company,
                        },
                        external_updated_at=dt,
                    )
                )
            add_data = body.get("additional_data") or {}
            next_c = add_data.get("next_cursor")
            has_more = bool(next_c)
            return PageBatch(
                items=items,
                next_cursor=f"cur:{next_c}" if has_more else (latest_ts or None),
                has_more=has_more,
            )

        return iterate_cursor_pages(_fetch, initial_cursor=cursor)
