"""Zoho CRM v6 REST API adapter (multi-DC, Contacts/Leads/Deals/Calls, <=100 batching, COQL & page_token sync)."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncIterator

from app.integrations_v2.core.errors import (
    AuthExpired,
    IntegrationV2Error,
    ProviderRejected,
    ValidationError,
)
from app.integrations_v2.core.http_client import Int2HttpClient, validate_vendor_url
from app.integrations_v2.core.pagination import PageBatch, iterate_cursor_pages
from app.integrations_v2.core.provider_base import (
    AuthKind,
    Capability,
    HealthStatus,
)
from app.integrations_v2.core.sync_engine import ExternalSyncRecord
from app.integrations_v2.crm import CrmContactRecord, CrmDealRecord, CrmProviderV2
from app.integrations_v2.settings import ZOHO_CRM_API_HOSTS

ZOHO_MAX_BATCH_SIZE = 100


class ZohoCrmProvider(CrmProviderV2):
    slug = "zoho_crm"
    display_name = "Zoho CRM"
    auth_kind = AuthKind.OAUTH2
    default_scopes = (
        "ZohoCRM.modules.ALL",
        "ZohoCRM.users.READ",
        "ZohoCRM.settings.fields.READ",
        "ZohoCRM.coql.READ",
    )
    docs_url = "https://www.zoho.com/crm/developer/docs/api/v6/"
    capabilities = frozenset(
        {
            Capability.CONTACTS_READ,
            Capability.CONTACTS_WRITE,
            Capability.LEAD_READ,
            Capability.LEAD_WRITE,
            Capability.DEAL_READ,
            Capability.DEAL_WRITE,
            Capability.CALL_LOG_WRITE,
            Capability.INCREMENTAL_SYNC,
            Capability.MID_CALL_TOOLS,
        }
    )

    def _client(self) -> Int2HttpClient:
        return self._http_client or Int2HttpClient()

    def _base_url(self) -> str:
        reg = (self.context.region or str(self.context.config.get("region") or "us")).lower()
        host = ZOHO_CRM_API_HOSTS.get(reg)
        if not host:
            raise IntegrationV2Error(f"Unsupported Zoho CRM region {reg!r}", provider=self.slug)
        url = f"https://{host}"
        validate_vendor_url(url, provider=self.slug)
        return url

    def _headers(self) -> dict[str, str]:
        token = str(self.context.credentials.get("access_token") or "").strip()
        if not token:
            raise AuthExpired("Zoho CRM access_token is missing", provider=self.slug)
        return {"Authorization": f"Zoho-oauthtoken {token}"}

    def _parse_contact(self, raw: dict[str, Any]) -> CrmContactRecord:
        acct = raw.get("Account_Name")
        company = acct.get("name", "") if isinstance(acct, dict) else str(acct or raw.get("Company") or "")
        return CrmContactRecord(
            external_id=str(raw.get("id") or ""),
            provider=self.slug,
            first_name=str(raw.get("First_Name") or ""),
            last_name=str(raw.get("Last_Name") or ""),
            email=str(raw.get("Email") or ""),
            phone=str(raw.get("Phone") or raw.get("Mobile") or ""),
            company=company,
            raw=raw,
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/crm/v6/users",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            params={"type": "CurrentUser"},
        )
        users = (resp.json() or {}).get("users") or []
        me = users[0] if users else {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(me.get("id") or ""),
            account_name=str(me.get("full_name") or me.get("email") or ""),
            detail=f"region={self.context.region}",
        )

    async def lookup_contact(
        self, *, email: str = "", phone: str = ""
    ) -> CrmContactRecord | None:
        if not (email or phone):
            return None
        params: dict[str, Any] = {"email": email} if email else {"phone": phone}
        resp = await self._client().request(
            "GET",
            f"{self._base_url()}/crm/v6/Contacts/search",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            params=params,
            expected_statuses=(200, 204),
        )
        if resp.status_code == 204:
            return None
        rows = (resp.json() or {}).get("data") or []
        return self._parse_contact(rows[0]) if rows else None

    async def upsert_contacts_batch(
        self,
        records: list[dict[str, Any]],
        *,
        duplicate_check_fields: list[str] | None = None,
    ) -> list[str]:
        """Bulk-safe Zoho CRM v6 upsert enforcing Zoho's documented <=100 record per-request limit."""
        if not records:
            return []
        if len(records) > ZOHO_MAX_BATCH_SIZE:
            raise ValidationError(
                f"Zoho CRM batch upsert supports at most {ZOHO_MAX_BATCH_SIZE} records per call",
                provider=self.slug,
            )
        ids: list[str] = []
        dup = duplicate_check_fields or ["Email"]
        for offset in range(0, len(records), ZOHO_MAX_BATCH_SIZE):
            chunk = records[offset : offset + ZOHO_MAX_BATCH_SIZE]
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/crm/v6/Contacts/upsert",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body={"data": chunk, "duplicate_check_fields": dup},
            )
            for item in (resp.json() or {}).get("data") or []:
                if item.get("status") == "error":
                    raise ProviderRejected(
                        f"Zoho CRM upsert rejected: {item.get('message')}", provider=self.slug
                    )
                ids.append(str((item.get("details") or {}).get("id") or ""))
        return ids

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
        record: dict[str, Any] = {
            "First_Name": first_name,
            "Last_Name": last_name or first_name or "Caller",
        }
        if email:
            record["Email"] = email
        if phone:
            record["Phone"] = phone
        if company:
            record["Department"] = company
        if custom_fields:
            record.update(custom_fields)

        dup_fields = ["Email"] if email else (["Phone"] if phone else ["Last_Name"])
        ids = await self.upsert_contacts_batch([record], duplicate_check_fields=dup_fields)
        return CrmContactRecord(
            external_id=ids[0] if ids else "",
            provider=self.slug,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            company=company,
        )

    async def coql_query(self, select_query: str) -> dict[str, Any]:
        """Execute a Zoho CRM v6 COQL query (``POST /crm/v6/coql``) for incremental sync."""
        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/crm/v6/coql",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={"select_query": select_query},
            expected_statuses=(200, 204),
        )
        if resp.status_code == 204:
            return {"data": [], "info": {"more_records": False}}
        return dict(resp.json() or {})

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
        rec: dict[str, Any] = {
            "Deal_Name": title,
            "Amount": amount,
            "Stage": stage or "Qualification",
        }
        if contact_id:
            rec["Contact_Name"] = {"id": contact_id}

        if deal_id:
            resp = await self._client().request(
                "PUT",
                f"{self._base_url()}/crm/v6/Deals/{deal_id}",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body={"data": [rec]},
            )
        else:
            resp = await self._client().request(
                "POST",
                f"{self._base_url()}/crm/v6/Deals",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(),
                json_body={"data": [rec]},
            )
        item = ((resp.json() or {}).get("data") or [{}])[0]
        ext_id = str((item.get("details") or {}).get("id") or deal_id)
        return CrmDealRecord(
            external_id=ext_id,
            provider=self.slug,
            title=title,
            amount=amount,
            currency=currency,
            stage=rec["Stage"],
            contact_id=contact_id,
        )

    async def log_call_activity(self, payload: dict[str, Any]) -> str:
        dur_sec = int(payload.get("duration_seconds") or 0)
        mins, secs = divmod(max(0, dur_sec), 60)
        desc = str(payload.get("summary") or "")
        if payload.get("recording_url"):
            desc = f"{desc}\nRecording: {payload['recording_url']}".strip()

        call_rec: dict[str, Any] = {
            "Subject": str(payload.get("subject") or "VoxDesk Voice Call"),
            "Call_Type": "Inbound" if payload.get("direction", "inbound") == "inbound" else "Outbound",
            "Call_Duration": f"{mins:02d}:{secs:02d}",
            "Call_Start_Time": str(payload.get("occurred_at") or ""),
            "Description": desc,
            "Call_Result": str(payload.get("disposition") or "Completed"),
        }
        if payload.get("external_contact_id"):
            call_rec["Who_Id"] = {"id": str(payload["external_contact_id"])}

        resp = await self._client().request(
            "POST",
            f"{self._base_url()}/crm/v6/Calls",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            json_body={"data": [call_rec]},
        )
        item = ((resp.json() or {}).get("data") or [{}])[0]
        return str((item.get("details") or {}).get("id") or "")

    def iter_updated_contacts(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        base = self._base_url()

        async def _fetch(cur: str | None) -> PageBatch[ExternalSyncRecord]:
            params: dict[str, Any] = {
                "fields": "id,First_Name,Last_Name,Email,Phone,Mobile,Modified_Time",
                "per_page": 200,
            }
            headers = self._headers()
            if cur and cur.startswith("pt:"):
                params["page_token"] = cur[3:]
            elif cur:
                headers["If-Modified-Since"] = cur

            resp = await self._client().request(
                "GET",
                f"{base}/crm/v6/Contacts",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=headers,
                params=params,
                expected_statuses=(200, 204, 304),
            )
            if resp.status_code in (204, 304):
                return PageBatch(items=[], next_cursor=cur, has_more=False)

            body = resp.json() or {}
            items: list[ExternalSyncRecord] = []
            latest_mod = cur or ""
            for raw in body.get("data") or []:
                mod = str(raw.get("Modified_Time") or "")
                dt = None
                if mod:
                    latest_mod = mod
                    try:
                        dt = datetime.fromisoformat(mod)
                    except ValueError:
                        dt = None
                items.append(
                    ExternalSyncRecord(
                        external_id=str(raw.get("id")),
                        entity_type="Contact",
                        raw_fields={
                            "first_name": raw.get("First_Name"),
                            "last_name": raw.get("Last_Name"),
                            "email": raw.get("Email"),
                            "phone": raw.get("Phone") or raw.get("Mobile"),
                            **raw,
                        },
                        external_updated_at=dt,
                    )
                )
            info = body.get("info") or {}
            more = bool(info.get("more_records"))
            next_pt = info.get("next_page_token")
            next_cur = f"pt:{next_pt}" if (more and next_pt) else (latest_mod or None)
            return PageBatch(items=items, next_cursor=next_cur, has_more=bool(more and next_pt))

        return iterate_cursor_pages(_fetch, initial_cursor=cursor)
