"""Lead enrichment. No configured provider means NOT_CONFIGURED, never invented fields."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Lead
from app.leads.models import LeadEnrichmentRecord

NOT_CONFIGURED = "NOT_CONFIGURED"


def configured_provider() -> str | None:
    name = getattr(settings, "lead_enrichment_provider", "") or ""
    text = str(name).strip()
    return text or None


async def request_enrichment(session: AsyncSession, lead: Lead) -> dict:
    provider = configured_provider()
    if provider is None:
        row = LeadEnrichmentRecord(
            lead_id=lead.id,
            tenant_id=lead.tenant_id,
            environment_id=lead.environment_id,
            status=NOT_CONFIGURED,
            provider="",
            detail="No lead enrichment provider is configured",
        )
        session.add(row)
        await session.flush()
        return {
            "status": NOT_CONFIGURED,
            "provider": None,
            "fields": {},
            "record_id": str(row.id),
        }
    # A named provider still does not synthesize contact data. Calling a
    # provider that is not wired would be a fake result, so this stays explicit.
    row = LeadEnrichmentRecord(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        status=NOT_CONFIGURED,
        provider=provider[:64],
        detail="Provider name is set but no enrichment client is wired",
    )
    session.add(row)
    await session.flush()
    return {
        "status": NOT_CONFIGURED,
        "provider": provider,
        "fields": {},
        "record_id": str(row.id),
    }
