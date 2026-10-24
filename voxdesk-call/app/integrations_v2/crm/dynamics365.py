"""Microsoft Dynamics 365 Sales / Dataverse Web API v9.2 (OData v4.0, @odata.nextLink, alternate-key upsert) adapter."""

from __future__ import annotations

import re
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

_ENTITY_ID_RE = re.compile(r"\(([0-9a-fA-F-]{36})\)")


def _odata_escape(value: str) -> str:
    return value.replace("'", "''")


class Dynamics365Provider(CrmProviderV2):
    slug = "dynamics365"
    display_name = "Microsoft Dynamics 365"
    auth_kind = AuthKind.OAUTH2
    default_scopes = ("offline_access",)
    required_config_fields = ("resource_url",)
    docs_url = "https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/overview"
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

    def _org_url(self) -> str:
        raw = (
            self.context.instance_url
            or str(self.context.config.get("resource_url") or "")
        ).rstrip("/")
        if not raw:
            raise IntegrationV2Error(
                "Dynamics 365 connection requires 'resource_url' (https://<org>.crm.dynamics.com)",
                provider=self.slug,
            )
        validate_vendor_url(raw, provider=self.slug)
        return raw

    def _api_root(self) -> str:
        return f"{self._org_url()}/api/data/v9.2"

    def _headers(self, *, prefer_return: bool = False) -> dict[str, str]:
        token = str(self.context.credentials.get("access_token") or "").strip()
        if not token:
            raise AuthExpired("Dynamics 365 Entra OAuth access_token is missing", provider=self.slug)
        headers = {
            "Authorization": f"Bearer {token}",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Accept": "application/json",
        }
        if prefer_return:
            headers["Prefer"] = "return=representation"
        return headers

    def _extract_entity_id(self, resp, body: dict[str, Any], id_field: str) -> str:
        if body and body.get(id_field):
            return str(body[id_field])
        hdr = resp.headers.get("OData-EntityId") or resp.headers.get("odata-entityid") or ""
        m = _ENTITY_ID_RE.search(hdr)
        return m.group(1) if m else ""

    def _parse_contact(self, raw: dict[str, Any]) -> CrmContactRecord:
        return CrmContactRecord(
            external_id=str(raw.get("contactid") or ""),
            provider=self.slug,
            first_name=str(raw.get("firstname") or ""),
            last_name=str(raw.get("lastname") or ""),
            email=str(raw.get("emailaddress1") or ""),
            phone=str(raw.get("telephone1") or raw.get("mobilephone") or ""),
            company=str(raw.get("company") or ""),
            raw=raw,
        )

    async def health(self) -> HealthStatus:
        t0 = time.perf_counter()
        resp = await self._client().request(
            "GET",
            f"{self._api_root()}/WhoAmI()",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
        )
        data = resp.json() or {}
        return HealthStatus(
            ok=True,
            provider=self.slug,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            account_id=str(data.get("OrganizationId") or ""),
            account_name=str(data.get("UserId") or ""),
            detail="dataverse=v9.2",
        )

    async def lookup_contact(
        self, *, email: str = "", phone: str = ""
    ) -> CrmContactRecord | None:
        clauses: list[str] = []
        if email:
            clauses.append(f"emailaddress1 eq '{_odata_escape(email)}'")
        if phone:
            esc = _odata_escape(phone)
            clauses.append(f"(telephone1 eq '{esc}' or mobilephone eq '{esc}')")
        if not clauses:
            return None
        resp = await self._client().request(
            "GET",
            f"{self._api_root()}/contacts",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(),
            params={
                "$filter": " or ".join(clauses),
                "$select": "contactid,firstname,lastname,emailaddress1,telephone1,mobilephone",
                "$top": 1,
            },
        )
        rows = (resp.json() or {}).get("value") or []
        return self._parse_contact(rows[0]) if rows else None

    async def upsert_contact_by_alternate_key(
        self,
        *,
        key_field: str = "",
        key_name: str = "",
        key_value: str,
        fields: dict[str, Any] | None = None,
        first_name: str = "",
        last_name: str = "",
    ) -> CrmContactRecord:
        """Dataverse OData v4 alternate-key upsert: ``PATCH /contacts(key_field='key_value')``."""
        resolved_key = key_field or key_name
        if not resolved_key:
            raise IntegrationV2Error("Alternate key field name is required", provider=self.slug)
        body: dict[str, Any] = dict(fields or {})
        if first_name:
            body.setdefault("firstname", first_name)
        if last_name:
            body.setdefault("lastname", last_name)
        if resolved_key == "emailaddress1":
            body.setdefault("emailaddress1", key_value)
        escaped_val = _odata_escape(key_value)
        resp = await self._client().request(
            "PATCH",
            f"{self._api_root()}/contacts({resolved_key}='{escaped_val}')",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(prefer_return=True),
            json_body=body,
            expected_statuses=(200, 201, 204),
        )
        data = resp.json() if resp.content else {}
        cid = self._extract_entity_id(resp, data, "contactid")
        return CrmContactRecord(
            external_id=cid,
            provider=self.slug,
            first_name=str(body.get("firstname") or ""),
            last_name=str(body.get("lastname") or ""),
            email=str(body.get("emailaddress1") or ""),
            phone=str(body.get("telephone1") or ""),
        )

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
        body: dict[str, Any] = {
            "firstname": first_name,
            "lastname": last_name or first_name or "Caller",
        }
        if email:
            body["emailaddress1"] = email
        if phone:
            body["telephone1"] = phone
        if custom_fields:
            body.update(custom_fields)

        if existing and existing.external_id:
            resp = await self._client().request(
                "PATCH",
                f"{self._api_root()}/contacts({existing.external_id})",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(prefer_return=True),
                json_body=body,
            )
            data = resp.json() if resp.content else {}
            cid = self._extract_entity_id(resp, data, "contactid") or existing.external_id
        else:
            resp = await self._client().request(
                "POST",
                f"{self._api_root()}/contacts",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(prefer_return=True),
                json_body=body,
            )
            data = resp.json() if resp.content else {}
            cid = self._extract_entity_id(resp, data, "contactid")

        return CrmContactRecord(
            external_id=cid,
            provider=self.slug,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            company=company,
        )

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
        body: dict[str, Any] = {"name": title, "estimatedvalue": amount}
        if stage:
            body["stepname"] = stage
        if contact_id:
            body["parentcontactid@odata.bind"] = f"/contacts({contact_id})"

        if deal_id:
            resp = await self._client().request(
                "PATCH",
                f"{self._api_root()}/opportunities({deal_id})",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(prefer_return=True),
                json_body=body,
            )
            data = resp.json() if resp.content else {}
            oid = self._extract_entity_id(resp, data, "opportunityid") or deal_id
        else:
            resp = await self._client().request(
                "POST",
                f"{self._api_root()}/opportunities",
                provider=self.slug,
                tenant_id=self.context.tenant_id,
                connection_id=self.context.connection_id,
                headers=self._headers(prefer_return=True),
                json_body=body,
            )
            data = resp.json() if resp.content else {}
            oid = self._extract_entity_id(resp, data, "opportunityid")

        return CrmDealRecord(
            external_id=oid,
            provider=self.slug,
            title=title,
            amount=amount,
            currency=currency,
            stage=stage,
            contact_id=contact_id,
        )

    async def log_call_activity(self, payload: dict[str, Any]) -> str:
        desc = str(payload.get("summary") or "")
        if payload.get("recording_url"):
            desc = f"{desc}\nRecording: {payload['recording_url']}".strip()
        duration_mins = max(1, round(int(payload.get("duration_seconds") or 0) / 60))

        body: dict[str, Any] = {
            "subject": str(payload.get("subject") or "VoxDesk Voice Call"),
            "description": desc,
            "phonenumber": str(payload.get("caller_phone") or ""),
            "directioncode": payload.get("direction", "inbound") == "outbound",
            "actualdurationminutes": duration_mins,
        }
        ext_cid = str(payload.get("external_contact_id") or "").strip()
        if ext_cid:
            body["regardingobjectid_contact@odata.bind"] = f"/contacts({ext_cid})"

        resp = await self._client().request(
            "POST",
            f"{self._api_root()}/phonecalls",
            provider=self.slug,
            tenant_id=self.context.tenant_id,
            connection_id=self.context.connection_id,
            headers=self._headers(prefer_return=True),
            json_body=body,
        )
        data = resp.json() if resp.content else {}
        return self._extract_entity_id(resp, data, "activityid")

    def iter_updated_contacts(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        api_root = self._api_root()

        async def _fetch(cur: str | None) -> PageBatch[ExternalSyncRecord]:
            headers = dict(self._headers())
            headers["Prefer"] = "odata.maxpagesize=100"

            if cur and cur.startswith("https://"):
                validate_vendor_url(cur, provider=self.slug)
                resp = await self._client().request(
                    "GET",
                    cur,
                    provider=self.slug,
                    tenant_id=self.context.tenant_id,
                    connection_id=self.context.connection_id,
                    headers=headers,
                )
            else:
                params: dict[str, Any] = {
                    "$select": "contactid,firstname,lastname,emailaddress1,telephone1,mobilephone,modifiedon",
                    "$orderby": "modifiedon asc",
                }
                if cur:
                    params["$filter"] = f"modifiedon gt {cur}"
                resp = await self._client().request(
                    "GET",
                    f"{api_root}/contacts",
                    provider=self.slug,
                    tenant_id=self.context.tenant_id,
                    connection_id=self.context.connection_id,
                    headers=headers,
                    params=params,
                )

            body = resp.json() or {}
            next_link = str(body.get("@odata.nextLink") or "")
            items: list[ExternalSyncRecord] = []
            latest_mod = cur if (cur and not cur.startswith("https://")) else ""
            for raw in body.get("value") or []:
                mod = str(raw.get("modifiedon") or "")
                dt = None
                if mod:
                    latest_mod = mod
                    try:
                        dt = datetime.fromisoformat(mod.replace("Z", "+00:00"))
                    except ValueError:
                        dt = None
                items.append(
                    ExternalSyncRecord(
                        external_id=str(raw.get("contactid")),
                        entity_type="Contact",
                        raw_fields={
                            "first_name": raw.get("firstname"),
                            "last_name": raw.get("lastname"),
                            "email": raw.get("emailaddress1"),
                            "phone": raw.get("telephone1") or raw.get("mobilephone"),
                            **raw,
                        },
                        external_updated_at=dt,
                        external_etag=str(raw.get("@odata.etag") or ""),
                    )
                )
            has_more = bool(next_link)
            return PageBatch(
                items=items,
                next_cursor=next_link if has_more else (latest_mod or None),
                has_more=has_more,
            )

        return iterate_cursor_pages(_fetch, initial_cursor=cursor)
