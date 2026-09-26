"""Bounded, idempotent lead import. The file is rejected before it is parsed if it is too large."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DurableJob
from app.jobs.repository import create_job
from app.leads.exceptions import ImportTooLarge
from app.tenancy.isolation import ValidationFailed

MAX_BYTES = 256 * 1024
MAX_ROWS = 500
MAX_FIELD = 2_000
_JOB_TYPE = "lead_import"


def bounded_body(body: bytes) -> bytes:
    if not body:
        raise ValidationFailed("Import body is empty")
    if len(body) > MAX_BYTES:
        raise ImportTooLarge(f"Import is larger than {MAX_BYTES} bytes")
    return body


def parse_rows(body: bytes, content_type: str) -> list[dict]:
    """Parse only after the byte cap. Still refuses more than ``MAX_ROWS``."""
    payload = bounded_body(body)
    kind = (content_type or "").lower()
    if "json" in kind or payload[:1] in (b"{", b"["):
        rows = _parse_json(payload)
    else:
        rows = _parse_csv(payload)
    if len(rows) > MAX_ROWS:
        raise ImportTooLarge(f"Import has more than {MAX_ROWS} rows")
    return rows


def _clip(value: object) -> str:
    text = "" if value is None else str(value)
    if len(text) > MAX_FIELD:
        raise ValidationFailed("A field exceeds the per-value bound")
    return text.strip()


def _parse_csv(payload: bytes) -> list[dict]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailed("Import must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "phone" not in {name.strip().lower() for name in reader.fieldnames}:
        raise ValidationFailed("CSV import requires a phone column")
    rows = []
    for raw in reader:
        if raw is None:
            continue
        item = {(key or "").strip().lower(): _clip(value) for key, value in raw.items()}
        if not any(item.values()):
            continue
        rows.append(item)
        if len(rows) > MAX_ROWS:
            raise ImportTooLarge(f"Import has more than {MAX_ROWS} rows")
    return rows


def _parse_json(payload: bytes) -> list[dict]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailed("JSON import could not be parsed") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("leads", [])
    if not isinstance(parsed, list):
        raise ValidationFailed("JSON import must be a list of leads")
    rows = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValidationFailed("Each JSON lead must be an object")
        rows.append({str(key).lower(): _clip(value) for key, value in item.items()})
        if len(rows) > MAX_ROWS:
            raise ImportTooLarge(f"Import has more than {MAX_ROWS} rows")
    return rows


def idempotency_key(tenant_id: uuid.UUID, supplied: str | None, body: bytes) -> str:
    if supplied and supplied.strip():
        digest = supplied.strip()[:80]
    else:
        digest = hashlib.sha256(body).hexdigest()[:32]
    return f"lead_import:{tenant_id}:{digest}"[:128]


async def begin_import_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    organization_id: uuid.UUID | None,
    key: str,
) -> tuple[DurableJob, bool]:
    return await create_job(
        session,
        tenant_id=tenant_id,
        organization_id=organization_id,
        environment_id=environment_id,
        job_type=_JOB_TYPE,
        idempotency_key=key,
        payload={"status": "accepted"},
        max_attempts=1,
    )


def finish_job(job: DurableJob, result: dict) -> None:
    job.status = "completed"
    job.completed_at = datetime.utcnow()
    job.payload = {"status": "completed", "result": result}
