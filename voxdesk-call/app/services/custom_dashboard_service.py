"""Custom Analytics Dashboard Service (Part 6 / Gate G7).

Provides tenant-scoped CRUD for saved custom dashboards, widget validation,
server-side metric/dimension aggregation, shareable links, and CSV export.
Persists dashboards in ``CallPolicy(policy_type='custom_dashboard')`` so no
schema migration is required beyond ``0049_runtime_schema_alignment``.
"""

from __future__ import annotations

import csv
import io
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.db.enterprise_models import CallPolicy
from app.services import analytics_service

CUSTOM_DASHBOARD_POLICY_TYPE = "custom_dashboard"
MAX_DASHBOARDS_PER_TENANT = 50
MAX_WIDGETS_PER_DASHBOARD = 24
MAX_GROUPS_PER_QUERY = 200
MAX_EXPORT_ROWS = 1000

ALLOWED_CHART_TYPES = frozenset(
    {"kpi", "line", "bar", "pie", "table", "area"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_widget(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BadRequestError(f"Widget at index {index} must be an object.")
    title = str(raw.get("title") or "").strip()
    if not title or len(title) > 120:
        raise BadRequestError(
            f"Widget at index {index} must have a title between 1 and 120 characters."
        )
    metric = str(raw.get("metric") or "").strip().lower()
    if metric not in analytics_service.ALLOWED_METRICS:
        raise BadRequestError(
            f"Unsupported widget metric {metric!r}; allowed: {sorted(analytics_service.ALLOWED_METRICS)}"
        )
    dimension = str(raw.get("dimension") or "day").strip().lower()
    if dimension not in analytics_service.ALLOWED_GROUP_DIMENSIONS:
        raise BadRequestError(
            f"Unsupported widget dimension {dimension!r}; allowed: {sorted(analytics_service.ALLOWED_GROUP_DIMENSIONS)}"
        )
    chart_type = str(raw.get("chart_type") or "bar").strip().lower()
    if chart_type not in ALLOWED_CHART_TYPES:
        raise BadRequestError(
            f"Unsupported widget chart_type {chart_type!r}; allowed: {sorted(ALLOWED_CHART_TYPES)}"
        )
    raw_filters = raw.get("filters") or {}
    if not isinstance(raw_filters, dict):
        raise BadRequestError(f"Widget at index {index} filters must be an object.")
    if len(raw_filters) > 12:
        raise BadRequestError(f"Widget at index {index} exceeds maximum of 12 filter keys.")

    widget_id = str(raw.get("id") or f"w-{uuid.uuid4().hex[:10]}")
    return {
        "id": widget_id,
        "title": title,
        "metric": metric,
        "dimension": dimension,
        "chart_type": chart_type,
        "filters": dict(raw_filters),
    }


def _validate_widgets(widgets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items = list(widgets or [])
    if len(items) > MAX_WIDGETS_PER_DASHBOARD:
        raise BadRequestError(
            f"Dashboard exceeds maximum of {MAX_WIDGETS_PER_DASHBOARD} widgets (got {len(items)})."
        )
    return [_validate_widget(w, idx) for idx, w in enumerate(items)]


def _serialize_dashboard(row: CallPolicy) -> dict[str, Any]:
    cfg = dict(row.config or {})
    share_token = cfg.get("share_token")
    share_enabled = bool(cfg.get("share_enabled", False)) and bool(share_token)
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "name": str(cfg.get("name") or "Untitled Dashboard"),
        "description": str(cfg.get("description") or ""),
        "widgets": list(cfg.get("widgets") or []),
        "global_filters": dict(cfg.get("global_filters") or {}),
        "share_enabled": share_enabled,
        "share_token": share_token if share_enabled else None,
        "share_url": (
            f"/api/v1/analytics/dashboards/shared/{share_token}"
            if share_enabled
            else None
        ),
        "created_by": cfg.get("created_by"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _get_dashboard_row(
    db: AsyncSession, tenant_id: uuid.UUID, dashboard_id: uuid.UUID
) -> CallPolicy:
    row = (
        await db.execute(
            select(CallPolicy).where(
                CallPolicy.id == dashboard_id,
                CallPolicy.tenant_id == tenant_id,
                CallPolicy.policy_type == CUSTOM_DASHBOARD_POLICY_TYPE,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Custom dashboard '{dashboard_id}' not found.")
    return row


async def create_dashboard(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    name: str,
    description: str = "",
    widgets: list[dict[str, Any]] | None = None,
    global_filters: dict[str, Any] | None = None,
    share_enabled: bool = False,
    actor_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name or len(clean_name) > 160:
        raise BadRequestError("Dashboard name must be between 1 and 160 characters.")

    existing_count = (
        await db.execute(
            select(func.count(CallPolicy.id)).where(
                CallPolicy.tenant_id == tenant_id,
                CallPolicy.policy_type == CUSTOM_DASHBOARD_POLICY_TYPE,
            )
        )
    ).scalar_one() or 0
    if int(existing_count) >= MAX_DASHBOARDS_PER_TENANT:
        raise BadRequestError(
            f"Tenant has reached the maximum of {MAX_DASHBOARDS_PER_TENANT} custom dashboards."
        )

    validated_widgets = _validate_widgets(widgets)
    share_token = secrets.token_urlsafe(24) if share_enabled else None
    dash_id = uuid.uuid4()
    now = _now()

    row = CallPolicy(
        id=dash_id,
        tenant_id=tenant_id,
        agent_id=f"dashboard:{dash_id}",
        policy_type=CUSTOM_DASHBOARD_POLICY_TYPE,
        config={
            "name": clean_name,
            "description": str(description or "").strip(),
            "widgets": validated_widgets,
            "global_filters": dict(global_filters or {}),
            "share_enabled": bool(share_enabled),
            "share_token": share_token,
            "created_by": str(actor_id) if actor_id else None,
        },
        is_enabled=True,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    return _serialize_dashboard(row)


async def list_dashboards(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit or 50), MAX_DASHBOARDS_PER_TENANT))
    bounded_offset = max(0, int(offset or 0))
    scope = (
        CallPolicy.tenant_id == tenant_id,
        CallPolicy.policy_type == CUSTOM_DASHBOARD_POLICY_TYPE,
    )
    total = (
        await db.execute(select(func.count(CallPolicy.id)).where(*scope))
    ).scalar_one() or 0
    rows = list(
        (
            await db.execute(
                select(CallPolicy)
                .where(*scope)
                .order_by(CallPolicy.created_at.desc())
                .offset(bounded_offset)
                .limit(bounded_limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "dashboards": [_serialize_dashboard(r) for r in rows],
        "total": int(total),
        "limit": bounded_limit,
        "offset": bounded_offset,
    }


async def get_dashboard(
    db: AsyncSession, tenant_id: uuid.UUID, dashboard_id: uuid.UUID
) -> dict[str, Any]:
    row = await _get_dashboard_row(db, tenant_id, dashboard_id)
    return _serialize_dashboard(row)


async def update_dashboard(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    widgets: list[dict[str, Any]] | None = None,
    global_filters: dict[str, Any] | None = None,
    share_enabled: bool | None = None,
) -> dict[str, Any]:
    row = await _get_dashboard_row(db, tenant_id, dashboard_id)
    cfg = dict(row.config or {})

    if name is not None:
        clean_name = str(name).strip()
        if not clean_name or len(clean_name) > 160:
            raise BadRequestError("Dashboard name must be between 1 and 160 characters.")
        cfg["name"] = clean_name
    if description is not None:
        cfg["description"] = str(description).strip()
    if widgets is not None:
        cfg["widgets"] = _validate_widgets(widgets)
    if global_filters is not None:
        cfg["global_filters"] = dict(global_filters)
    if share_enabled is not None:
        cfg["share_enabled"] = bool(share_enabled)
        if share_enabled and not cfg.get("share_token"):
            cfg["share_token"] = secrets.token_urlsafe(24)

    row.config = cfg
    row.updated_at = _now()
    await db.flush()
    return _serialize_dashboard(row)


async def delete_dashboard(
    db: AsyncSession, tenant_id: uuid.UUID, dashboard_id: uuid.UUID
) -> dict[str, Any]:
    row = await _get_dashboard_row(db, tenant_id, dashboard_id)
    await db.delete(row)
    await db.flush()
    return {"id": str(dashboard_id), "deleted": True}


async def create_or_rotate_share_link(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    *,
    enabled: bool = True,
    rotate: bool = False,
) -> dict[str, Any]:
    row = await _get_dashboard_row(db, tenant_id, dashboard_id)
    cfg = dict(row.config or {})
    if enabled:
        if rotate or not cfg.get("share_token"):
            cfg["share_token"] = secrets.token_urlsafe(24)
        cfg["share_enabled"] = True
    else:
        cfg["share_enabled"] = False
    row.config = cfg
    row.updated_at = _now()
    await db.flush()
    serialized = _serialize_dashboard(row)
    return {
        "dashboard_id": serialized["id"],
        "share_enabled": serialized["share_enabled"],
        "share_token": serialized["share_token"],
        "share_url": serialized["share_url"],
    }


def _extract_metric_value(bucket: dict[str, Any], metric: str) -> Any:
    if metric in bucket:
        return bucket[metric]
    return 0.0


async def evaluate_dashboard_widgets(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    override_filters: dict[str, Any] | None = None,
    max_groups: int = 100,
) -> dict[str, Any]:
    bounded_groups = max(1, min(int(max_groups or 100), MAX_GROUPS_PER_QUERY))
    dashboard = await get_dashboard(db, tenant_id, dashboard_id)
    base_filters = {
        **dict(dashboard.get("global_filters") or {}),
        **dict(override_filters or {}),
    }

    overall = await analytics_service.compute_metrics_and_breakdown(
        db,
        tenant_id,
        start=start,
        end=end,
        group_by="none",
        filters=base_filters,
        max_groups=bounded_groups,
    )

    evaluated_widgets: list[dict[str, Any]] = []
    for w in dashboard.get("widgets") or []:
        merged_filters = {**base_filters, **dict(w.get("filters") or {})}
        dim = str(w.get("dimension") or "day")
        metric = str(w.get("metric") or "total_calls")
        res = await analytics_service.compute_metrics_and_breakdown(
            db,
            tenant_id,
            start=start,
            end=end,
            group_by=dim,
            filters=merged_filters,
            max_groups=bounded_groups,
        )
        summary_val = _extract_metric_value(res["summary"], metric)
        series = [
            {
                "key": g["key"],
                "label": g["label"],
                "value": _extract_metric_value(g, metric),
                "total_calls": g["total_calls"],
                "success_rate": g["success_rate"],
                "avg_duration_seconds": g["avg_duration_seconds"],
                "p95_duration_seconds": g["p95_duration_seconds"],
                "avg_cost_per_call_usd": g["avg_cost_per_call_usd"],
                "latency_p50_ms": g["latency_p50_ms"],
                "latency_p95_ms": g["latency_p95_ms"],
                "transfer_rate": g["transfer_rate"],
                "voicemail_rate": g["voicemail_rate"],
            }
            for g in res["groups"]
        ]
        evaluated_widgets.append(
            {
                **w,
                "value": summary_val,
                "summary": res["summary"],
                "series": series,
            }
        )

    return {
        "dashboard": dashboard,
        "summary": overall["summary"],
        "widgets": evaluated_widgets,
    }


async def get_shared_dashboard(
    db: AsyncSession,
    share_token: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    token_clean = str(share_token or "").strip()
    if not token_clean:
        raise NotFoundError("Shared dashboard not found.")

    rows = list(
        (
            await db.execute(
                select(CallPolicy).where(
                    CallPolicy.policy_type == CUSTOM_DASHBOARD_POLICY_TYPE,
                    CallPolicy.is_enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    matched: CallPolicy | None = None
    for row in rows:
        cfg = row.config if isinstance(row.config, dict) else {}
        if cfg.get("share_enabled") and cfg.get("share_token") == token_clean:
            matched = row
            break

    if matched is None:
        raise NotFoundError("Shared dashboard not found or sharing is disabled.")

    return await evaluate_dashboard_widgets(
        db, matched.tenant_id, matched.id, start=start, end=end
    )


async def export_dashboard_csv(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    dashboard_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    override_filters: dict[str, Any] | None = None,
) -> str:
    evaluated = await evaluate_dashboard_widgets(
        db,
        tenant_id,
        dashboard_id,
        start=start,
        end=end,
        override_filters=override_filters,
        max_groups=MAX_GROUPS_PER_QUERY,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "widget_id",
            "widget_title",
            "metric",
            "dimension",
            "group_key",
            "group_label",
            "value",
            "total_calls",
            "success_rate",
            "avg_duration_seconds",
            "p95_duration_seconds",
            "avg_cost_per_call_usd",
            "latency_p50_ms",
            "latency_p95_ms",
            "transfer_rate",
            "voicemail_rate",
        ]
    )

    row_count = 0
    for w in evaluated["widgets"]:
        series = w.get("series") or []
        if not series:
            s = w.get("summary") or {}
            writer.writerow(
                [
                    w["id"],
                    w["title"],
                    w["metric"],
                    w["dimension"],
                    "summary",
                    "Summary",
                    w.get("value"),
                    s.get("total_calls", 0),
                    s.get("success_rate", 0.0),
                    s.get("avg_duration_seconds", 0.0),
                    s.get("p95_duration_seconds", 0.0),
                    s.get("avg_cost_per_call_usd", 0.0),
                    s.get("latency_p50_ms", 0.0),
                    s.get("latency_p95_ms", 0.0),
                    s.get("transfer_rate", 0.0),
                    s.get("voicemail_rate", 0.0),
                ]
            )
            row_count += 1
        else:
            for item in series:
                if row_count >= MAX_EXPORT_ROWS:
                    break
                writer.writerow(
                    [
                        w["id"],
                        w["title"],
                        w["metric"],
                        w["dimension"],
                        item["key"],
                        item["label"],
                        item["value"],
                        item["total_calls"],
                        item["success_rate"],
                        item["avg_duration_seconds"],
                        item["p95_duration_seconds"],
                        item["avg_cost_per_call_usd"],
                        item["latency_p50_ms"],
                        item["latency_p95_ms"],
                        item["transfer_rate"],
                        item["voicemail_rate"],
                    ]
                )
                row_count += 1
        if row_count >= MAX_EXPORT_ROWS:
            break

    return buf.getvalue()
