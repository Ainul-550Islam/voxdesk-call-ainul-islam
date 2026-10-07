"""Tenant-scoped QA export. No secrets, no recording URLs, no other tenant."""

from __future__ import annotations

import csv
import io
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.exceptions import ExportNotAuthorized
from app.qa.repository import evidence_for_review, findings_for_review, list_reviews, review_items

_SECRET_KEYS = {"token", "secret", "password", "api_key", "recording_url"}


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if key not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


async def rows_for(session: AsyncSession, tenant_id: uuid.UUID, *, actor_tenant_id: uuid.UUID) -> list[dict]:
    if actor_tenant_id != tenant_id:
        raise ExportNotAuthorized("Export tenant does not match the caller")
    reviews = await list_reviews(session, tenant_id, limit=100, offset=0)
    payload = []
    for review in reviews:
        if review.tenant_id != tenant_id:
            raise ExportNotAuthorized("Export crossed a tenant boundary")
        items = await review_items(session, tenant_id, review.id)
        evidence = await evidence_for_review(session, tenant_id, review.id)
        findings = await findings_for_review(session, tenant_id, review.id)
        payload.append(
            _clean(
                {
                    "review_id": str(review.id),
                    "call_id": str(review.call_id),
                    "scorecard_id": str(review.scorecard_id),
                    "scorecard_version": review.scorecard_version,
                    "status": review.status,
                    "overall_score": review.overall_score,
                    "passed": review.passed,
                    "items": [item.as_dict() for item in items],
                    "evidence": [row.as_dict() for row in evidence],
                    "findings": [row.as_dict() for row in findings],
                }
            )
        )
    return payload


def to_json(rows: list[dict]) -> str:
    return json.dumps({"reviews": rows}, default=str)


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "review_id",
            "call_id",
            "scorecard_id",
            "scorecard_version",
            "status",
            "overall_score",
            "passed",
            "item_count",
            "evidence_count",
            "finding_count",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "review_id": row["review_id"],
                "call_id": row["call_id"],
                "scorecard_id": row["scorecard_id"],
                "scorecard_version": row["scorecard_version"],
                "status": row["status"],
                "overall_score": row["overall_score"],
                "passed": row["passed"],
                "item_count": len(row["items"]),
                "evidence_count": len(row["evidence"]),
                "finding_count": len(row["findings"]),
            }
        )
    return buffer.getvalue()


def chunks(text: str, size: int = 1024):
    for index in range(0, max(len(text), 1), size):
        yield text[index : index + size]
