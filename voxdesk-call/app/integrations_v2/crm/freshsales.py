"""Freshsales Suite CRM REST API (``/crm/sales/api/*``) adapter."""

from __future__ import annotations

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
from app.integrations_v2.crm import CrmContactRecord, CrmDealRecord, CrmProviderV2


class FreshsalesProvider(CrmProviderV2):
    slug = "freshsales"
    display_name = "Freshsales Suite"
    auth_kind = AuthKind.OAUTH2_OR_API_KEY
    default_scopes = (
        "freshsales.contacts.view",
        "freshsales.contacts.edit",
        "freshsales.deals.view",
        "freshsales.deals.edit",
        "freshsales.notes.create",
    )
    required_config_fields = ("domain",)
    docs_url = "https://developers.freshworks.com/crm/api/"
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
        if self.context.instance_url:
            base = self.context.instance_url.rstrip("/")
        else:
            dom = str(self.context.config.get("domain") or "").strip().lower()
            if not dom:
                raise IntegrationV2Error("Freshsales connection requires 'domain'", provider=self.slug)
            base = f"https://{dom}.myfreshworks.com"
        validate_vendor_url(base, provider=self.slug)
        return f"{base}/crm/sales/api"

    def _headers(self) -> dict[str, str]:
        creds = self.context.credentials
        oauth_tok = str(creds.get("access_token") or "").strip()
        if oauth_tok and self.context.auth_kind != "api_key":
            return {"Authorization": f"Bearer {oauth_tok}"}
        api_key = str(creds.get("api_key") or oauth_tok).strip()
        if api_key:
            return {"Authorization": f"Token token={api_key}"}
        raise AuthExpired("Freshsales credentials missing OAuth access_token or api_key", provider=self.slug)

    def _parse_contact(self, raw: dict[str, Any]) -> CrmContactRecord:
        return CrmContactRecord(
            external_id=str(raw.get("id") or ""),
            provider=self.slug,
            first_name=str(raw.get("first_name") or ""),
            last_name=str(raw.get("last_name") or ""),
            email=str(raw.get("email") or ""),
            phone=str(raw.get("mobile_number") or raw.get("work_number") or ""),
            company=str((raw.get("sales_account") or {}).get("name") or ""),
            raw=raw,
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/selector/owners",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
        )
        users = (resp.json() or {}).get("users") or []
        first = users[0] if users else {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(first.get("id") or ""),
            account_name=str(first.get("display_name") or first.get("email") or ""),
            detail="freshsales_suite=ok",
        )

    async def lookup_contact(
        self, *, email: str = "", phone: str = ""
    ) -> CrmContactRecord | None:
        if not (email or phone):
            return None
        q = email or phone
        attr = "email" if email else "mobile_number"
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/lookup",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            params={"q": q, "f": attr, "entities": "contact"},
        )
        contacts = ((resp.json() or {}).get("contacts") or {}).get("contacts") or []
        return self._parse_contact(contacts[0]) if contacts else None

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
        contact_obj: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name or first_name or "Caller",
        }
        if email:
            contact_obj["email"] = email
        if phone:
            contact_obj["mobile_number"] = phone
        if custom_fields:
            contact_obj["custom_field"] = dict(custom_fields)

        unique_id: dict[str, str] = (
            {"emails": email} if email else ({"mobile_number": phone} if phone else {})
        )
        if unique_id:
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/contacts/upsert",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body={"unique_identifier": unique_id, "contact": contact_obj},
            )
        else:
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/contacts",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body={"contact": contact_obj},
            )
        return self._parse_contact((resp.json() or {}).get("contact") or {})

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
        deal_obj: dict[str, Any] = {"name": title, "amount": amount}
        if contact_id and contact_id.isdigit():
            deal_obj["contacts_added_list"] = [int(contact_id)]
        if stage and stage.isdigit():
            deal_obj["deal_stage_id"] = int(stage)

        if deal_id:
            resp = await self._client().request(
                "PUT",
                f"{self._base_url()}/deals/{deal_id}",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body={"deal": deal_obj},
            )
        else:
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/deals",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body={"deal": deal_obj},
            )
        d = (resp.json() or {}).get("deal") or {}
        return CrmDealRecord(
            external_id=str(d.get("id") or deal_id),
            provider=self.slug,
            title=str(d.get("name") or title),
            amount=float(d.get("amount") or amount),
            currency=currency,
            stage=str(d.get("deal_stage_id") or stage),
            contact_id=contact_id,
        )

    async def log_call_activity(self, payload: dict[str, Any]) -> str:
        ext_cid = str(payload.get("external_contact_id") or "").strip()
        summary = str(payload.get("summary") or "VoxDesk voice call")
        if payload.get("recording_url"):
            summary = f"{summary}\nRecording: {payload['recording_url']}"

        if ext_cid and ext_cid.isdigit():
            body = {
                "phone_call": {
                    "call_direction": payload.get("direction", "inbound") == "outbound",
                    "targetable_type": "Contact",
                    "targetable": {"id": int(ext_cid)},
                    "call_duration": int(payload.get("duration_seconds") or 0),
                    "note": {"description": summary},
                }
            }
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/phone_calls",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body=body,
            )
            pc = (resp.json() or {}).get("phone_call") or {}
            return str(pc.get("id") or "")

        # Fallback: create a standalone note on contact lookup
        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/notes",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={
                "note": {
                    "description": summary,
                    "targetable_type": "Contact",
                    "targetable_id": int(ext_cid) if ext_cid.isdigit() else 1,
                }
            },
        )
        n = (resp.json() or {}).get("note") or {}
        return str(n.get("id") or "")

    def iter_updated_contacts(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        base = self._base_url()
        view_id = str(self.context.config.get("contacts_view_id") or "all")

        async def _fetch(cur: str | None) -> PageBatch[ExternalSyncRecord]:
            page = int(cur) if (cur and cur.isdigit()) else 1
            resp = await self._client().request(
                "GET",
                f"{base}/contacts/view/{view_id}",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                params={"page": page, "sort": "updated_at", "sort_type": "asc"},
            )
            body = resp.json() or {}
            rows = body.get("contacts") or []
            meta = body.get("meta") or {}
            total_pages = int(meta.get("total_pages") or 1)
            items: list[ExternalSyncRecord] = []
            for raw in rows:
                ts_str = str(raw.get("updated_at") or "")
                dt = None
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        dt = None
                items.append(
                    ExternalSyncRecord(
                        external_id=str(raw.get("id")),
                        entity_type="Contact",
                        raw_fields={
                            "first_name": raw.get("first_name"),
                            "last_name": raw.get("last_name"),
                            "email": raw.get("email"),
                            "phone": raw.get("mobile_number") or raw.get("work_number"),
                            **raw,
                        },
                        external_updated_at=dt,
                    )
                )
            has_more = page < total_pages and bool(rows)
            return PageBatch(
                items=items,
                next_cursor=str(page + 1) if has_more else str(page),
                has_more=has_more,
            )

        return iterate_cursor_pages(_fetch, initial_cursor=cursor)
