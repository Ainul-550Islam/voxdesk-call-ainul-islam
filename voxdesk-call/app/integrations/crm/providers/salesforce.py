"""
Salesforce CRM adapter (REST API v59.0 + OAuth2 Web-Server Flow with PKCE).

Implements:
- OAuth2 web-server authorization URL + S256 PKCE + code exchange + token refresh
- Automatic 401 -> refresh_access_token retry when refresh credentials are present
- Instance URL validation (HTTPS + Salesforce/Force domains via app.core.ssrf)
- Escaped SOQL queries (Contact, Lead, Account, Case, Opportunity, Task)
- Contact/Lead create, update, upsert, and get_contact
- Call logging as Salesforce Task (Subject, Description, CallDurationInSeconds, CallType, WhoId/WhatId)
- ContentNote / Note creation, Case creation, and OpportunityContactRole linking
- REQUEST_LIMIT_EXCEEDED / 429 rate-limit classification
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.integrations.crm.base import Capability, CrmProvider
from app.integrations.crm.errors import (
    CrmAuthError,
    CrmConfigurationError,
    CrmError,
    CrmNotFound,
    CrmRateLimited,
    CrmValidationError,
    safe_message,
)
from app.integrations.crm.models import (
    CrmResult,
    HealthResult,
    NormalizedActivity,
    NormalizedContact,
)

DEFAULT_API_VERSION = "v59.0"
DEFAULT_LOGIN_URL = "https://login.salesforce.com"

_ALLOWED_SF_HOST_SUFFIXES = (
    ".salesforce.com",
    ".my.salesforce.com",
    ".force.com",
    ".salesforce-setup.com",
    ".salesforce.test",
    ".test",
)

_SOBJECT_NAMES: dict[str, str] = {
    "contact": "Contact",
    "lead": "Lead",
    "account": "Account",
    "case": "Case",
    "opportunity": "Opportunity",
    "task": "Task",
    "note": "Note",
}

_SOQL_ESCAPE_MAP = str.maketrans(
    {
        "\\": "\\\\",
        "'": "\\'",
        '"': '\\"',
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\b": "\\b",
        "\f": "\\f",
    }
)


def escape_soql_literal(value: str) -> str:
    """Escape a string literal for safe inclusion inside single-quoted SOQL."""
    return (value or "").translate(_SOQL_ESCAPE_MAP)


def validate_salesforce_instance_url(url: str) -> str:
    """Validate that ``url`` is a safe HTTPS Salesforce/Force instance URL."""
    cleaned = (url or "").strip().rstrip("/")
    try:
        validate_outbound_url(cleaned, require_https=True)
    except OutboundUrlError as exc:
        raise CrmConfigurationError(str(exc), provider="salesforce") from exc

    parsed = urlsplit(cleaned)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or not any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in _ALLOWED_SF_HOST_SUFFIXES
    ):
        raise CrmConfigurationError(
            f"Salesforce instance_url host {host!r} is not an allowed Salesforce domain",
            provider="salesforce",
        )
    return cleaned


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using RFC 7636 S256."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str | None = None,
    scope: str = "api refresh_token openid",
    login_url: str = DEFAULT_LOGIN_URL,
) -> str:
    """Construct the Salesforce OAuth2 Web-Server authorization URL."""
    base = validate_salesforce_instance_url(login_url)
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{base}/services/oauth2/authorize?{urlencode(params)}"


class SalesforceProvider(CrmProvider):
    name = "salesforce"
    capabilities = frozenset(
        {
            Capability.UPSERT_CONTACT,
            Capability.CREATE_CONTACT,
            Capability.UPDATE_CONTACT,
            Capability.GET_CONTACT,
            Capability.CREATE_NOTE,
            Capability.CREATE_ACTIVITY,
            Capability.ADD_CUSTOM_FIELDS,
            Capability.HEALTH_CHECK,
        }
    )

    @property
    def _token(self) -> str:
        token = (self.context.credentials or {}).get("access_token") or ""
        if not token:
            raise CrmConfigurationError(
                "Salesforce access token is not configured", provider=self.name
            )
        return token

    @property
    def _api_version(self) -> str:
        raw = str((self.context.config or {}).get("api_version") or DEFAULT_API_VERSION).strip()
        if not raw.startswith("v"):
            raw = f"v{raw}"
        return raw

    def _base(self) -> str:
        raw = (
            (self.context.config or {}).get("instance_url")
            or (self.context.config or {}).get("base_url")
            or (self.context.credentials or {}).get("instance_url")
            or ""
        )
        if not raw:
            raise CrmConfigurationError(
                "Salesforce instance_url is not configured", provider=self.name
            )
        return validate_salesforce_instance_url(str(raw))

    def _service_base(self) -> str:
        return f"{self._base()}/services/data/{self._api_version}"

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _check_sf_error_payload(self, status: int, data: Any) -> None:
        if isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else {}
            err_code = str(first.get("errorCode") or "")
            msg = str(first.get("message") or err_code or "Salesforce error")
            if err_code == "REQUEST_LIMIT_EXCEEDED":
                raise CrmRateLimited(
                    safe_message(msg),
                    provider=self.name,
                )
            if status >= 400:
                raise CrmValidationError(
                    safe_message(f"{err_code}: {msg}"),
                    provider=self.name,
                )

    async def _sf_request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        try:
            status, data = await self.request(
                method,
                url,
                json_body=json_body,
                params=params,
                expected=(200, 201, 202, 204, 403),
            )
        except CrmAuthError:
            refreshed = await self._try_auto_refresh()
            if not refreshed:
                raise
            status, data = await self.request(
                method,
                url,
                json_body=json_body,
                params=params,
                expected=(200, 201, 202, 204, 403),
            )
        except CrmError as exc:
            if "REQUEST_LIMIT_EXCEEDED" in str(exc):
                raise CrmRateLimited(
                    exc.safe_message,
                    provider=self.name,
                ) from exc
            raise
        self._check_sf_error_payload(status, data)
        if status == 403:
            raise CrmAuthError(
                f"{self.name} rejected the credentials (HTTP 403)",
                provider=self.name,
            )
        return status, data

    async def _try_auto_refresh(self) -> bool:
        creds = self.context.credentials
        cfg = self.context.config or {}
        refresh_tok = creds.get("refresh_token")
        client_id = creds.get("client_id") or cfg.get("client_id")
        client_secret = creds.get("client_secret") or cfg.get("client_secret")
        if not (refresh_tok and client_id and client_secret):
            return False
        token_data = await self.refresh_access_token(
            refresh_token=str(refresh_tok),
            client_id=str(client_id),
            client_secret=str(client_secret),
            login_url=str(cfg.get("login_url") or self._base()),
        )
        new_token = token_data.get("access_token")
        if not new_token:
            return False
        creds["access_token"] = str(new_token)
        if token_data.get("instance_url"):
            creds["instance_url"] = str(token_data["instance_url"])
        return True

    # ------------------------------------------------------------ OAuth2 ---

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        code_verifier: str | None = None,
        login_url: str = DEFAULT_LOGIN_URL,
    ) -> dict[str, Any]:
        """Exchange an OAuth2 authorization code (and optional PKCE verifier) for tokens."""
        base = validate_salesforce_instance_url(login_url)
        form_params: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            form_params["code_verifier"] = code_verifier
        _, data = await self.request(
            "POST",
            f"{base}/services/oauth2/token",
            params=form_params,
            headers={"Authorization": ""},
        )
        if not isinstance(data, dict) or not data.get("access_token"):
            raise CrmAuthError(
                "Salesforce OAuth token exchange returned no access_token",
                provider=self.name,
            )
        if data.get("instance_url"):
            validate_salesforce_instance_url(str(data["instance_url"]))
        return data

    async def refresh_access_token(
        self,
        *,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        login_url: str | None = None,
    ) -> dict[str, Any]:
        """Refresh a Salesforce OAuth2 access token using a refresh_token."""
        creds = self.context.credentials or {}
        cfg = self.context.config or {}
        rtok = refresh_token or creds.get("refresh_token") or ""
        cid = client_id or creds.get("client_id") or cfg.get("client_id") or ""
        csec = client_secret or creds.get("client_secret") or cfg.get("client_secret") or ""
        if not rtok or not cid or not csec:
            raise CrmConfigurationError(
                "Salesforce refresh_token, client_id, and client_secret are required for token refresh",
                provider=self.name,
            )
        base = validate_salesforce_instance_url(
            login_url or str(cfg.get("login_url") or self._base())
        )
        _, data = await self.request(
            "POST",
            f"{base}/services/oauth2/token",
            params={
                "grant_type": "refresh_token",
                "refresh_token": str(rtok),
                "client_id": str(cid),
                "client_secret": str(csec),
            },
            headers={"Authorization": ""},
        )
        if not isinstance(data, dict) or not data.get("access_token"):
            raise CrmAuthError(
                "Salesforce token refresh returned no access_token",
                provider=self.name,
            )
        return data

    # ------------------------------------------------------------ mapping ---

    def contact_fields(self, contact: NormalizedContact) -> dict[str, Any]:
        """Map a ``NormalizedContact`` to Salesforce ``Contact`` sObject fields."""
        fields: dict[str, Any] = {
            "FirstName": contact.first_name or "Unknown",
            "LastName": contact.last_name or "Caller",
        }
        if contact.phone:
            fields["Phone"] = contact.phone
        if contact.email:
            fields["Email"] = contact.email.strip().lower()
        if contact.source:
            fields["LeadSource"] = contact.source
        if contact.notes:
            fields["Description"] = contact.notes
        for key, value in (contact.custom_fields or {}).items():
            fields[key] = value
        return fields

    def lead_fields(self, contact: NormalizedContact) -> dict[str, Any]:
        """Map a ``NormalizedContact`` to Salesforce ``Lead`` sObject fields."""
        fields = self.contact_fields(contact)
        fields["Company"] = contact.company or "Individual"
        return fields

    # --------------------------------------------------------- CrmProvider ---

    async def upsert_contact(self, contact: NormalizedContact) -> CrmResult:
        existing = await self.get_contact(phone=contact.phone, email=contact.email)
        if existing is not None:
            await self.update_contact(existing.external_id, contact)
            return CrmResult(
                external_id=existing.external_id,
                already_existed=True,
                details={"sobject": "Contact", "operation": "update"},
            )
        created = await self.create_contact(contact)
        return CrmResult(
            external_id=created.external_id,
            already_existed=False,
            details={"sobject": "Contact", "operation": "create"},
        )

    async def create_contact(self, contact: NormalizedContact) -> CrmResult:
        _, data = await self._sf_request(
            "POST",
            f"{self._service_base()}/sobjects/Contact",
            json_body=self.contact_fields(contact),
        )
        contact_id = (data or {}).get("id") if isinstance(data, dict) else None
        if not contact_id:
            raise CrmValidationError(
                "Salesforce accepted the Contact create but returned no id",
                provider=self.name,
            )
        return CrmResult(
            external_id=str(contact_id),
            already_existed=False,
            details={"sobject": "Contact"},
        )

    async def update_contact(
        self, external_id: str, contact: NormalizedContact
    ) -> CrmResult:
        if not external_id or not external_id.strip():
            raise CrmValidationError("external_id is required", provider=self.name)
        await self._sf_request(
            "PATCH",
            f"{self._service_base()}/sobjects/Contact/{external_id}",
            json_body=self.contact_fields(contact),
        )
        return CrmResult(
            external_id=external_id,
            already_existed=True,
            details={"sobject": "Contact"},
        )

    async def get_contact(
        self,
        *,
        external_id: str | None = None,
        phone: str | None = None,
        email: str | None = None,
    ) -> CrmResult | None:
        if external_id:
            try:
                _, data = await self._sf_request(
                    "GET",
                    f"{self._service_base()}/sobjects/Contact/{external_id}",
                )
            except CrmNotFound:
                return None
            rec_id = (data or {}).get("Id") or (data or {}).get("id")
            if not rec_id:
                return None
            return CrmResult(
                external_id=str(rec_id),
                already_existed=True,
                details={"sobject": "Contact"},
            )

        clauses: list[str] = []
        if email:
            clauses.append(f"Email = '{escape_soql_literal(email.strip().lower())}'")
        if phone:
            clauses.append(f"Phone = '{escape_soql_literal(phone.strip())}'")
        if not clauses:
            return None

        where_expr = " OR ".join(clauses)
        soql = f"SELECT Id, FirstName, LastName, Email, Phone FROM Contact WHERE {where_expr} LIMIT 1"
        records = await self.query_soql(soql)
        if not records:
            return None
        first_id = records[0].get("Id") or records[0].get("id")
        if not first_id:
            return None
        return CrmResult(
            external_id=str(first_id),
            already_existed=True,
            details={"sobject": "Contact"},
        )

    async def create_note(
        self, external_contact_id: str, activity: NormalizedActivity
    ) -> CrmResult:
        if not external_contact_id:
            raise CrmValidationError(
                "external_contact_id is required to create a Salesforce Note",
                provider=self.name,
            )
        payload = {
            "ParentId": external_contact_id,
            "Title": (activity.title or "VoxDesk Call Note")[:80],
            "Body": activity.body or activity.title or "Call note",
        }
        _, data = await self._sf_request(
            "POST",
            f"{self._service_base()}/sobjects/Note",
            json_body=payload,
        )
        note_id = (data or {}).get("id") if isinstance(data, dict) else None
        if not note_id:
            raise CrmValidationError(
                "Salesforce accepted the Note but returned no id",
                provider=self.name,
            )
        return CrmResult(
            external_id=str(note_id),
            details={"sobject": "Note", "parent_id": external_contact_id},
        )

    async def create_activity(
        self, external_contact_id: str, activity: NormalizedActivity
    ) -> CrmResult:
        """Log a call as a completed Salesforce ``Task`` sObject."""
        attrs = dict(activity.attributes or {})
        direction_raw = str(attrs.get("direction") or "inbound").lower()
        call_type = "Outbound" if direction_raw == "outbound" else "Inbound"

        payload: dict[str, Any] = {
            "Subject": (activity.title or "VoxDesk Voice Call")[:255],
            "Description": activity.body or "",
            "Status": "Completed",
            "Priority": "Normal",
            "TaskSubtype": "Call",
            "CallType": call_type,
        }
        if activity.duration_seconds is not None:
            payload["CallDurationInSeconds"] = max(0, int(round(activity.duration_seconds)))
        if attrs.get("disposition") or attrs.get("outcome"):
            payload["CallDisposition"] = str(
                attrs.get("disposition") or attrs.get("outcome")
            )[:255]

        # Salesforce WhoId accepts Contact (003...) or Lead (00Q...); WhatId accepts Account/Case/Opportunity
        if external_contact_id:
            if external_contact_id.startswith(("003", "00Q")) or not attrs.get("what_id"):
                payload["WhoId"] = external_contact_id
            else:
                payload["WhatId"] = external_contact_id
        if attrs.get("what_id"):
            payload["WhatId"] = str(attrs["what_id"])

        _, data = await self._sf_request(
            "POST",
            f"{self._service_base()}/sobjects/Task",
            json_body=payload,
        )
        task_id = (data or {}).get("id") if isinstance(data, dict) else None
        if not task_id:
            raise CrmValidationError(
                "Salesforce accepted the Task but returned no id",
                provider=self.name,
            )
        return CrmResult(
            external_id=str(task_id),
            details={"sobject": "Task", "who_id": payload.get("WhoId")},
        )

    async def add_custom_fields(
        self, external_contact_id: str, fields: dict[str, Any]
    ) -> CrmResult:
        if not external_contact_id:
            raise CrmValidationError(
                "external_contact_id is required", provider=self.name
            )
        if not fields:
            return CrmResult(
                external_id=external_contact_id,
                already_existed=True,
                details={"sobject": "Contact", "updated_fields": []},
            )
        sobject = "Lead" if external_contact_id.startswith("00Q") else "Contact"
        await self._sf_request(
            "PATCH",
            f"{self._service_base()}/sobjects/{sobject}/{external_contact_id}",
            json_body=dict(fields),
        )
        return CrmResult(
            external_id=external_contact_id,
            already_existed=True,
            details={"sobject": sobject, "updated_fields": sorted(fields.keys())},
        )

    async def health_check(self) -> HealthResult:
        started = time.perf_counter()
        try:
            await self._sf_request("GET", f"{self._service_base()}/limits")
        except CrmError as exc:
            return HealthResult(
                connected=False,
                provider=self.name,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                safe_message=exc.safe_message,
            )
        return HealthResult(
            connected=True,
            provider=self.name,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            safe_message="ok",
        )

    # ---------------------------------------- Salesforce-specific methods ---

    async def query_soql(self, soql: str) -> list[dict[str, Any]]:
        """Execute a SOQL query and return the ``records`` list."""
        _, data = await self._sf_request(
            "GET",
            f"{self._service_base()}/query",
            params={"q": soql},
        )
        if not isinstance(data, dict):
            return []
        records = data.get("records")
        return list(records) if isinstance(records, list) else []

    async def query_entities(
        self,
        entity_type: str,
        *,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query standard Salesforce sObjects with escaped SOQL search."""
        key = (entity_type or "").strip().lower()
        sobject = _SOBJECT_NAMES.get(key)
        if not sobject:
            raise CrmValidationError(
                f"Unsupported Salesforce entity type {entity_type!r}",
                provider=self.name,
            )

        field_map = {
            "Contact": "Id, FirstName, LastName, Name, Email, Phone, AccountId",
            "Lead": "Id, FirstName, LastName, Name, Company, Email, Phone, Status",
            "Account": "Id, Name, Phone, Type, Industry",
            "Case": "Id, CaseNumber, Subject, Status, Priority, ContactId, AccountId",
            "Opportunity": "Id, Name, StageName, Amount, CloseDate, AccountId",
            "Task": "Id, Subject, Status, Priority, WhoId, WhatId, CallDurationInSeconds, CallType",
        }
        select_cols = field_map.get(sobject, "Id, Name")
        where_clause = ""
        if search and search.strip():
            escaped = escape_soql_literal(search.strip())
            if sobject in ("Contact", "Lead"):
                where_clause = (
                    f" WHERE Name LIKE '%{escaped}%' "
                    f"OR Email LIKE '%{escaped}%' "
                    f"OR Phone LIKE '%{escaped}%'"
                )
            elif sobject == "Case":
                where_clause = f" WHERE Subject LIKE '%{escaped}%' OR CaseNumber LIKE '%{escaped}%'"
            elif sobject == "Task":
                where_clause = f" WHERE Subject LIKE '%{escaped}%'"
            else:
                where_clause = f" WHERE Name LIKE '%{escaped}%'"

        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, min(int(offset), 2000))
        soql = (
            f"SELECT {select_cols} FROM {sobject}{where_clause} "
            f"ORDER BY CreatedDate DESC LIMIT {bounded_limit} OFFSET {bounded_offset}"
        )
        return await self.query_soql(soql)

    async def get_sobject(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        key = (entity_type or "").strip().lower()
        sobject = _SOBJECT_NAMES.get(key, entity_type)
        _, data = await self._sf_request(
            "GET",
            f"{self._service_base()}/sobjects/{sobject}/{entity_id}",
        )
        if not isinstance(data, dict) or not (data.get("Id") or data.get("id")):
            raise CrmNotFound(
                f"{sobject} {entity_id} not found",
                provider=self.name,
                status_code=404,
            )
        return data

    async def describe_sobject(self, sobject: str) -> dict[str, Any]:
        key = (sobject or "").strip().lower()
        resolved = _SOBJECT_NAMES.get(key, sobject)
        _, data = await self._sf_request(
            "GET",
            f"{self._service_base()}/sobjects/{resolved}/describe",
        )
        return data if isinstance(data, dict) else {}

    async def get_limits(self) -> dict[str, Any]:
        _, data = await self._sf_request("GET", f"{self._service_base()}/limits")
        return data if isinstance(data, dict) else {}

    async def create_lead(self, contact: NormalizedContact) -> CrmResult:
        _, data = await self._sf_request(
            "POST",
            f"{self._service_base()}/sobjects/Lead",
            json_body=self.lead_fields(contact),
        )
        lead_id = (data or {}).get("id") if isinstance(data, dict) else None
        if not lead_id:
            raise CrmValidationError(
                "Salesforce accepted the Lead create but returned no id",
                provider=self.name,
            )
        return CrmResult(
            external_id=str(lead_id),
            already_existed=False,
            details={"sobject": "Lead"},
        )

    async def update_lead(
        self, external_id: str, contact: NormalizedContact
    ) -> CrmResult:
        await self._sf_request(
            "PATCH",
            f"{self._service_base()}/sobjects/Lead/{external_id}",
            json_body=self.lead_fields(contact),
        )
        return CrmResult(
            external_id=external_id,
            already_existed=True,
            details={"sobject": "Lead"},
        )

    async def create_case(
        self,
        *,
        subject: str,
        description: str = "",
        contact_id: str | None = None,
        account_id: str | None = None,
        status: str = "New",
        origin: str = "Phone",
        extra_fields: dict[str, Any] | None = None,
    ) -> CrmResult:
        payload: dict[str, Any] = {
            "Subject": (subject or "VoxDesk Support Case")[:255],
            "Description": description,
            "Status": status,
            "Origin": origin,
        }
        if contact_id:
            payload["ContactId"] = contact_id
        if account_id:
            payload["AccountId"] = account_id
        if extra_fields:
            payload.update(extra_fields)
        _, data = await self._sf_request(
            "POST",
            f"{self._service_base()}/sobjects/Case",
            json_body=payload,
        )
        case_id = (data or {}).get("id") if isinstance(data, dict) else None
        if not case_id:
            raise CrmValidationError(
                "Salesforce accepted the Case create but returned no id",
                provider=self.name,
            )
        return CrmResult(
            external_id=str(case_id),
            already_existed=False,
            details={"sobject": "Case"},
        )

    async def link_opportunity(
        self,
        *,
        opportunity_id: str,
        contact_id: str,
        role: str = "Decision Maker",
        is_primary: bool = True,
    ) -> CrmResult:
        payload = {
            "OpportunityId": opportunity_id,
            "ContactId": contact_id,
            "Role": role,
            "IsPrimary": is_primary,
        }
        _, data = await self._sf_request(
            "POST",
            f"{self._service_base()}/sobjects/OpportunityContactRole",
            json_body=payload,
        )
        ocr_id = (data or {}).get("id") if isinstance(data, dict) else None
        if not ocr_id:
            raise CrmValidationError(
                "Salesforce accepted the OpportunityContactRole but returned no id",
                provider=self.name,
            )
        return CrmResult(
            external_id=str(ocr_id),
            already_existed=False,
            details={
                "sobject": "OpportunityContactRole",
                "opportunity_id": opportunity_id,
                "contact_id": contact_id,
            },
        )

    async def writeback_sobject(
        self,
        *,
        entity_type: str,
        action: str,
        fields: dict[str, Any],
        entity_id: str | None = None,
    ) -> CrmResult:
        """Execute a direct sObject create/update/upsert/delete writeback."""
        key = (entity_type or "").strip().lower()
        sobject = _SOBJECT_NAMES.get(key)
        if not sobject:
            raise CrmValidationError(
                f"Unsupported Salesforce entity_type {entity_type!r}",
                provider=self.name,
            )
        act = (action or "").strip().lower()
        if act == "create":
            _, data = await self._sf_request(
                "POST",
                f"{self._service_base()}/sobjects/{sobject}",
                json_body=dict(fields or {}),
            )
            created_id = (data or {}).get("id") if isinstance(data, dict) else None
            if not created_id:
                raise CrmValidationError(
                    f"Salesforce {sobject} create returned no id",
                    provider=self.name,
                )
            return CrmResult(
                external_id=str(created_id),
                already_existed=False,
                details={"sobject": sobject, "action": "create"},
            )
        if act == "update":
            if not entity_id:
                raise CrmValidationError(
                    "entity_id is required for update", provider=self.name
                )
            await self._sf_request(
                "PATCH",
                f"{self._service_base()}/sobjects/{sobject}/{entity_id}",
                json_body=dict(fields or {}),
            )
            return CrmResult(
                external_id=entity_id,
                already_existed=True,
                details={"sobject": sobject, "action": "update"},
            )
        if act == "upsert":
            if entity_id:
                await self._sf_request(
                    "PATCH",
                    f"{self._service_base()}/sobjects/{sobject}/{entity_id}",
                    json_body=dict(fields or {}),
                )
                return CrmResult(
                    external_id=entity_id,
                    already_existed=True,
                    details={"sobject": sobject, "action": "upsert"},
                )
            _, data = await self._sf_request(
                "POST",
                f"{self._service_base()}/sobjects/{sobject}",
                json_body=dict(fields or {}),
            )
            created_id = (data or {}).get("id") if isinstance(data, dict) else None
            if not created_id:
                raise CrmValidationError(
                    f"Salesforce {sobject} upsert returned no id",
                    provider=self.name,
                )
            return CrmResult(
                external_id=str(created_id),
                already_existed=False,
                details={"sobject": sobject, "action": "upsert"},
            )
        if act == "delete":
            if not entity_id:
                raise CrmValidationError(
                    "entity_id is required for delete", provider=self.name
                )
            await self._sf_request(
                "DELETE",
                f"{self._service_base()}/sobjects/{sobject}/{entity_id}",
            )
            return CrmResult(
                external_id=entity_id,
                already_existed=True,
                details={"sobject": sobject, "action": "delete"},
            )
        raise CrmValidationError(f"Unsupported action {action!r}", provider=self.name)
