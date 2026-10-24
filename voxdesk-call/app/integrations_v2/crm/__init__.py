"""Enterprise CRM v2 providers (Zoho CRM v6, Freshsales Suite, Dynamics 365 Dataverse, Pipedrive v2)."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.integrations_v2.core.pagination import PageBatch
from app.integrations_v2.core.provider_base import ProviderV2
from app.integrations_v2.core.sync_engine import ExternalSyncRecord


@dataclass(frozen=True)
class CrmContactRecord:
    external_id: str
    provider: str
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    company: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "provider": self.provider,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "phone": self.phone,
            "company": self.company,
        }


@dataclass(frozen=True)
class CrmDealRecord:
    external_id: str
    provider: str
    title: str
    amount: float = 0.0
    currency: str = "USD"
    stage: str = ""
    contact_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "provider": self.provider,
            "title": self.title,
            "amount": self.amount,
            "currency": self.currency,
            "stage": self.stage,
            "contact_id": self.contact_id,
        }


class CrmProviderV2(ProviderV2, abc.ABC):
    """Unified async interface implemented by Zoho CRM, Freshsales, Dynamics 365, and Pipedrive v2."""

    category = "crm"

    @abc.abstractmethod
    async def lookup_contact(
        self, *, email: str = "", phone: str = ""
    ) -> CrmContactRecord | None:
        """Locate an external CRM contact/lead by email or E.164 phone."""

    @abc.abstractmethod
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
        """Create or update an external CRM contact."""

    @abc.abstractmethod
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
        """Create or update a deal/opportunity linked to ``contact_id``."""

    @abc.abstractmethod
    async def log_call_activity(self, payload: dict[str, Any]) -> str:
        """Log a completed voice call activity/note and return the vendor activity ID."""

    @abc.abstractmethod
    def iter_updated_contacts(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        """Yield cursor-checkpointed pages of updated contacts for incremental sync."""
