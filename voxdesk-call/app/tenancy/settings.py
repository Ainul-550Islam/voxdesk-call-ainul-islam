"""Tenant settings that are safe to return.

Secret and contact fields are named so a caller cannot ask for them by
accident. This module does not decrypt, copy, or log those values.
"""

from __future__ import annotations

from app.db.models import Tenant
from app.tenancy.exceptions import ValidationFailed

# Columns that must never appear in a settings response.
SECRET_FIELDS = frozenset(
    {
        "crm_api_key",
        "a2p_brand_sid",
        "a2p_campaign_sid",
    }
)

# Contact and routing values. Not secrets, but not "settings" either.
WITHHELD_FIELDS = SECRET_FIELDS | frozenset(
    {
        "twilio_number",
        "escalation_number",
        "notify_sms_number",
        "whatsapp_number",
        "outbound_caller_id",
        "crm_webhook_url",
        "google_calendar_id",
        "system_prompt_extra",
        "knowledge_base",
        "ivr_flow",
    }
)

_PUBLIC = (
    "name",
    "industry",
    "timezone",
    "language",
    "lifecycle_status",
    "plan",
    "sms_enabled",
    "whatsapp_enabled",
    "outbound_enabled",
    "record_calls",
    "ivr_enabled",
    "humanize",
)


def public_view(tenant: Tenant) -> dict:
    """Non-secret metadata. Absence of a secret field is the guarantee."""
    view = {name: getattr(tenant, name) for name in _PUBLIC}
    leaked = WITHHELD_FIELDS.intersection(view)
    if leaked:
        raise ValidationFailed("Settings view included a withheld field")
    return view


def assert_not_secret(field: str) -> None:
    name = (field or "").strip()
    lowered = name.lower()
    if name in WITHHELD_FIELDS or any(
        token in lowered for token in ("secret", "password", "token", "api_key")
    ):
        raise ValidationFailed("That setting is not readable")
