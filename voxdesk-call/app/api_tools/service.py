"""API-tool validation and execution over the existing HTTP stack."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from jsonschema import ValidationError as SchemaValidationError
from jsonschema import validate
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.core.ssrf import OutboundUrlError, validate_outbound_url, validate_resolved_outbound_url
from app.core.tracing import span
from app.db.models import ApiTool, AuditAction, PrivacyPolicy, ToolExecution
from app.security.privacy import PrivacyPolicy as PrivacyConfig, transform
from app.security.secret_store import SecretStoreError, get_secret_store

ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


class ApiToolError(ValueError):
    """A stored API-tool definition or execution violated a safety limit."""


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    status_code: int
    data: object = None
    error_category: str | None = None
    attempts: int = 1
    duration_ms: float = 0.0


def _protect_result(value: object, policy: PrivacyConfig) -> object:
    if isinstance(value, str):
        return transform(value, policy)
    if isinstance(value, list):
        return [_protect_result(item, policy) for item in value]
    if isinstance(value, dict):
        return {str(key): _protect_result(item, policy) for key, item in value.items()}
    return value


async def _privacy_policy(session: AsyncSession, tenant_id: uuid.UUID) -> PrivacyConfig:
    row = (
        await session.execute(select(PrivacyPolicy).where(PrivacyPolicy.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        return PrivacyConfig(mode="mask")
    return PrivacyConfig(mode=row.mode, rules=row.rules or {})


def validate_definition(
    *,
    method: str,
    url_template: str,
    parameter_schema: dict,
    body_schema: dict,
    result_schema: dict,
) -> None:
    method = method.upper()
    if method not in ALLOWED_METHODS:
        raise ApiToolError("method must be GET, POST, PUT, PATCH, or DELETE")
    if not isinstance(url_template, str) or not url_template.startswith("https://"):
        raise ApiToolError("API tool URLs must use https")
    # Validate a host-shaped URL before allowing placeholders. Substitution
    # values are encoded as path/query data and cannot replace the scheme/host.
    sample = url_template
    for key in ("id", "resource", "path", "query"):
        sample = sample.replace("{" + key + "}", "sample")
    try:
        validate_outbound_url(sample, require_https=True)
    except OutboundUrlError as exc:
        raise ApiToolError(str(exc)) from exc
    for schema in (parameter_schema, body_schema, result_schema):
        if not isinstance(schema, dict):
            raise ApiToolError("schemas must be JSON objects")


def _render(template: str, arguments: dict) -> str:
    out, index = "", 0
    # Placeholders are deliberately limited to simple names. A value is quoted
    # into the URL, so arguments never alter its authority component.
    import re

    pattern = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
    for match in pattern.finditer(template):
        out += template[index : match.start()] + quote(str(arguments[match.group(1)]), safe="")
        index = match.end()
    out += template[index:]
    return out


def _auth_headers(tenant_id: uuid.UUID, tool: ApiTool) -> dict[str, str]:
    if not tool.auth_ref:
        return {}
    try:
        raw = get_secret_store().get(str(tenant_id), f"api_tool:{tool.id}", tool.auth_ref)
    except SecretStoreError as exc:
        raise ApiToolError("API tool authentication reference is unavailable") from exc
    if raw.startswith("Bearer "):
        return {"Authorization": raw}
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        config = {"scheme": "Bearer", "token": raw}
    token = config.get("token")
    if not isinstance(token, str) or not token:
        raise ApiToolError("API tool authentication secret is invalid")
    scheme = str(config.get("scheme", "Bearer"))
    if scheme.lower() not in {"bearer", "basic", "token"}:
        raise ApiToolError("API tool authentication scheme is not allowed")
    return {"Authorization": f"{scheme} {token}"}


async def execute(
    session: AsyncSession,
    tool: ApiTool,
    arguments: dict,
    *,
    idempotency_key: str,
    client: httpx.AsyncClient | None = None,
) -> ToolResult:
    from app.auth.identity.events import emit as audit_event

    if not tool.enabled:
        raise ApiToolError("API tool is disabled")
    if not idempotency_key or len(idempotency_key) > 180:
        raise ApiToolError("a bounded Idempotency-Key is required")
    try:
        validate(arguments, tool.parameter_schema or {})
        if tool.body_schema:
            validate(arguments.get("body", {}), tool.body_schema)
    except SchemaValidationError as exc:
        raise ApiToolError("tool arguments do not match the configured schema") from exc
    try:
        url = _render(tool.url_template, arguments)
    except KeyError as exc:
        raise ApiToolError(f"missing API tool argument: {exc.args[0]}") from exc
    try:
        await validate_resolved_outbound_url(url, require_https=True)
    except OutboundUrlError as exc:
        raise ApiToolError("rendered API tool URL is not allowed") from exc
    row = ToolExecution(
        tenant_id=tool.tenant_id,
        environment_id=tool.environment_id,
        tool_type="api",
        tool_id=tool.id,
        idempotency_key=idempotency_key,
        status="started",
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = (
            await session.execute(
                select(ToolExecution).where(
                    ToolExecution.tenant_id == tool.tenant_id,
                    ToolExecution.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one()
        return ToolResult(
            existing.status == "succeeded",
            200 if existing.status == "succeeded" else 409,
            existing.result_metadata,
            existing.error_category,
        )
    headers = {"Accept": "application/json", **_auth_headers(tool.tenant_id, tool)}
    body = arguments.get("body")
    params = arguments.get("query") if isinstance(arguments.get("query"), dict) else None
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=tool.timeout_seconds, follow_redirects=False)
    started = time.perf_counter()
    attempts = 0
    try:
        for attempts in range(1, max(1, min(tool.retry_max_attempts, 5)) + 1):
            try:
                with span(
                    "voxdesk.api_tool.execute",
                    tenant_id=str(tool.tenant_id),
                    tool_id=str(tool.id),
                    method=tool.method,
                ):
                    response = await http.request(
                        tool.method,
                        url,
                        params=params,
                        json=body if tool.method in {"POST", "PUT", "PATCH"} else None,
                        headers=headers,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempts >= tool.retry_max_attempts:
                    row.status, row.error_category = "failed", type(exc).__name__
                    return ToolResult(
                        False,
                        504,
                        None,
                        type(exc).__name__,
                        attempts,
                        (time.perf_counter() - started) * 1000,
                    )
                await asyncio.sleep(min(2 ** (attempts - 1), 4))
                continue
            if response.status_code in {408, 429} or response.status_code >= 500:
                if tool.method == "POST" and not idempotency_key:
                    break
                if attempts < tool.retry_max_attempts:
                    await asyncio.sleep(min(2 ** (attempts - 1), 4))
                    continue
            break
        if response.status_code >= 400:
            row.status, row.error_category = "failed", f"http_{response.status_code}"
            await audit_event(
                session,
                AuditAction.INTEGRATION_TESTED,
                tenant_id=tool.tenant_id,
                detail={
                    "tool_id": str(tool.id),
                    "outcome": "failed",
                    "status": response.status_code,
                },
            )
            return ToolResult(
                False,
                response.status_code,
                None,
                row.error_category,
                attempts,
                (time.perf_counter() - started) * 1000,
            )
        try:
            data = response.json()
        except ValueError:
            data = response.text[:10000]
        if tool.result_schema:
            try:
                validate(data, tool.result_schema)
            except SchemaValidationError:
                row.status, row.error_category = "failed", "result_schema_mismatch"
                return ToolResult(
                    False,
                    response.status_code,
                    None,
                    "result_schema_mismatch",
                    attempts,
                    (time.perf_counter() - started) * 1000,
                )
        safe_data = _protect_result(data, await _privacy_policy(session, tool.tenant_id))
        row.status, row.result_metadata, row.completed_at = (
            "succeeded",
            {"status_code": response.status_code, "data": safe_data},
            __import__("datetime").datetime.utcnow(),
        )
        await audit_event(
            session,
            AuditAction.INTEGRATION_TESTED,
            tenant_id=tool.tenant_id,
            detail={"tool_id": str(tool.id), "outcome": "success", "status": response.status_code},
        )
        return ToolResult(
            True,
            response.status_code,
            safe_data,
            attempts=attempts,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    finally:
        if owns_client:
            await http.aclose()
        await session.flush()
        log.info(
            "api_tool.executed",
            tenant_id=str(tool.tenant_id),
            tool_id=str(tool.id),
            attempts=attempts,
        )
