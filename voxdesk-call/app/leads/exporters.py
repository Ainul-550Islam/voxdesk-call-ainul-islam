"""Authorization-safe lead export. Bounded, and free of credentials."""

from __future__ import annotations

import csv
import io

from app.db.models import Lead

MAX_EXPORT_ROWS = 1_000
_BLOCKED = ("api_key", "token", "secret", "password", "authorization", "credential", "payload")
_COLUMNS = (
    "id", "name", "phone", "email", "company", "status", "score", "attempts",
    "campaign_id", "environment_id", "safe_custom_fields",
)


def safe_custom_fields(raw: dict | None) -> dict:
    clean: dict[str, str] = {}
    for key, value in (raw or {}).items():
        lowered = str(key).lower()
        if any(part in lowered for part in _BLOCKED):
            continue
        if isinstance(value, (dict, list)):
            continue
        text = "" if value is None else str(value)
        if len(text) > 200:
            text = text[:199] + "…"
        clean[str(key)[:64]] = text
    return clean


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def lead_row(lead: Lead) -> dict[str, str]:
    status = lead.status.value if hasattr(lead.status, "value") else str(lead.status)
    return {
        "id": str(lead.id),
        "name": _cell(lead.name or ""),
        "phone": _cell(lead.phone or ""),
        "email": _cell(lead.email or ""),
        "company": _cell(lead.company or ""),
        "status": status,
        "score": str(lead.score if lead.score is not None else ""),
        "attempts": str(lead.attempts or 0),
        "campaign_id": str(lead.campaign_id) if lead.campaign_id else "",
        "environment_id": str(lead.environment_id) if lead.environment_id else "",
        "safe_custom_fields": _cell(
            ";".join(f"{key}={value}" for key, value in safe_custom_fields(lead.custom_fields).items())
        ),
    }


def render_csv(leads: list[Lead]) -> str:
    """Render at most ``MAX_EXPORT_ROWS``. Callers must already have applied that cap."""
    bounded = leads[:MAX_EXPORT_ROWS]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for lead in bounded:
        writer.writerow(lead_row(lead))
    return buffer.getvalue()
