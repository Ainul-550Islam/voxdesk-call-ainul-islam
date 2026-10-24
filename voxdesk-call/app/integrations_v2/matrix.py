"""Capability matrix builder distinguishing NOT_CONFIGURED, PROTOCOL_TESTED, and LIVE_VERIFIED.

``LIVE_VERIFIED`` is reported ONLY when a real live-test result artifact (JUnit XML or
JSON evidence with a valid ISO timestamp and zero failures/skips for that provider)
exists in ``evidence_dir``. Without a live artifact, configured providers report
``PROTOCOL_TESTED`` and unconfigured providers report ``NOT_CONFIGURED``.
"""

from __future__ import annotations

import json
import os
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import IntegrationConnection
from app.integrations_v2.core.connection_store import list_tenant_connections
from app.integrations_v2.core.provider_base import VerificationStatus

LIVE_TEST_PROVIDER_PATTERNS: dict[str, tuple[str, ...]] = {
    "zendesk": ("test_live_zendesk",),
    "servicenow": ("test_live_servicenow",),
    "freshdesk": ("test_live_freshdesk",),
    "zoho_desk": ("test_live_zoho_desk", "test_live_zoho"),
    "zoho_crm": ("test_live_zoho_crm", "test_live_zoho"),
    "freshsales": ("test_live_freshsales",),
    "dynamics365": ("test_live_dynamics365",),
    "pipedrive": ("test_live_pipedrive",),
    "teams": ("test_live_teams",),
    "zoom": ("test_live_zoom",),
    "slack": ("test_live_slack",),
}


def default_live_evidence_dir() -> Path:
    configured = os.environ.get("INT2_LIVE_EVIDENCE_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path("evidence/integrations_v2_live")


def _valid_iso_timestamp(raw: str) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()
    try:
        normalized = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
        datetime.fromisoformat(normalized)
        return candidate
    except ValueError:
        return None


def load_live_evidence_timestamps(evidence_dir: Path | None = None) -> dict[str, str]:
    """Scan ``evidence_dir`` for JUnit XML or JSON live-test artifacts containing valid timestamps."""
    root_dir = evidence_dir if evidence_dir is not None else default_live_evidence_dir()
    verified: dict[str, str] = {}
    if not root_dir.exists() or not root_dir.is_dir():
        return verified

    for xml_path in sorted(root_dir.glob("*.xml")):
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError:
            continue
        root = tree.getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
        for suite in suites:
            ts = _valid_iso_timestamp(suite.attrib.get("timestamp", ""))
            if not ts:
                continue
            for case in suite.findall(".//testcase"):
                name = f"{case.attrib.get('classname', '')}.{case.attrib.get('name', '')}".lower()
                failed_or_skipped = (
                    case.find("failure") is not None
                    or case.find("error") is not None
                    or case.find("skipped") is not None
                )
                if failed_or_skipped:
                    continue
                for slug, tokens in LIVE_TEST_PROVIDER_PATTERNS.items():
                    if any(tok in name for tok in tokens):
                        verified[slug] = ts

    for json_path in sorted(root_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = data if isinstance(data, list) else data.get("results", [data])
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("provider") or "").strip().lower()
            ts = _valid_iso_timestamp(str(item.get("timestamp") or ""))
            if slug in LIVE_TEST_PROVIDER_PATTERNS and item.get("passed") is True and ts:
                verified[slug] = ts

    return verified


def resolve_provider_verification(
    slug: str,
    *,
    conn_row: IntegrationConnection | None,
    env_configured: bool,
    live_timestamps: dict[str, str] | None = None,
    evidence_dir: Path | None = None,
) -> tuple[VerificationStatus, str | None]:
    """Determine ``VerificationStatus`` honestly for a single provider."""
    evidence = (
        live_timestamps
        if live_timestamps is not None
        else load_live_evidence_timestamps(evidence_dir)
    )
    has_creds = env_configured or (
        conn_row is not None
        and conn_row.status == "active"
        and bool(conn_row.token_secret_ref)
    )
    live_ts = evidence.get(slug)
    if has_creds and live_ts:
        return VerificationStatus.LIVE_VERIFIED, live_ts
    if has_creds:
        return VerificationStatus.PROTOCOL_TESTED, None
    return VerificationStatus.NOT_CONFIGURED, None


async def build_capability_matrix(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    evidence_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build capability + verification matrix across all 11 Integrations v2 providers."""
    from app.integrations_v2.catalog import compute_catalog_status
    from app.integrations_v2.register_all import (
        PROVIDERS_V2,
        is_provider_env_configured,
    )

    by_provider: dict[str, IntegrationConnection] = {}
    if tenant_id is not None:
        rows = await list_tenant_connections(session, tenant_id=tenant_id)
        for row in rows:
            if row.provider not in by_provider or row.status == "active":
                by_provider[row.provider] = row

    live_map = load_live_evidence_timestamps(evidence_dir)
    matrix: list[dict[str, Any]] = []
    for slug, cls in PROVIDERS_V2.items():
        env_ok = is_provider_env_configured(slug)
        conn_row = by_provider.get(slug)
        ver, live_ts = resolve_provider_verification(
            slug,
            conn_row=conn_row,
            env_configured=env_ok,
            live_timestamps=live_map,
        )
        entry = cls.descriptor(env_configured=env_ok, verification=ver)
        entry["capabilities"] = {
            cap.value: ver.value for cap in sorted(cls.capabilities, key=lambda c: c.value)
        }
        entry["status"] = compute_catalog_status(conn_row, env_configured=env_ok)
        entry["connected"] = conn_row is not None and conn_row.status == "active"
        entry["tenant_connected"] = entry["connected"]
        entry["connection_status"] = (
            conn_row.status if conn_row is not None else "disconnected"
        )
        entry["last_health_at"] = (
            conn_row.last_health_at.isoformat()
            if (conn_row is not None and conn_row.last_health_at is not None)
            else None
        )
        entry["live_verified_at"] = live_ts
        entry["last_verified_at"] = live_ts
        entry["honesty_notes"] = cls.docs_url
        matrix.append(entry)
    return matrix
