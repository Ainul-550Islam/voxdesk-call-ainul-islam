"""Dynamic HTTP Tool Adapter for LLM function-calling (Sub-Phase 2D).

Bridges both:
1. Tenant-level `ApiTool` rows (`api_tools` table, executed via `app.api_tools.service.execute`), and
2. Agent-level `AgentTool` rows (`agent_tools` table / `RuntimeConfig.custom_tools`)
into OpenAI/Anthropic/Google-compatible function schemas and async runtime handlers
with SSRF protection (`validate_outbound_url`), secret resolution (`get_secret_store`),
template interpolation (`{{param}}`), timeout enforcement, and secret/PII redaction.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_tools import service as api_tool_service
from app.audit.redaction import configured_secret_values, redact_text
from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.db.models import ApiTool
from app.security.secret_store import SecretStoreError, get_secret_store

log = structlog.get_logger()

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
_SAFE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _resolve_secret(raw_value: str, tenant_id: uuid.UUID | None = None) -> str:
    val = (raw_value or "").strip()
    if not val:
        return ""
    if val.startswith("env:"):
        return os.environ.get(val[4:].strip(), "")
    if val.startswith("secret:") and tenant_id is not None:
        try:
            return get_secret_store().get(tenant_id, val[7:].strip())
        except SecretStoreError:
            return ""
    return val


def _interpolate_string(template: str, arguments: dict[str, Any]) -> str:
    def _repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = arguments.get(key)
        return "" if val is None else str(val)

    return _TEMPLATE_VAR_RE.sub(_repl, template)


def _interpolate_value(value: Any, arguments: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _interpolate_string(value, arguments)
    if isinstance(value, dict):
        return {k: _interpolate_value(v, arguments) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_value(v, arguments) for v in value]
    return value


def _scrub_value(value: Any) -> Any:
    secrets = configured_secret_values()
    if isinstance(value, str):
        return redact_text(value, known_secrets=secrets, redact_pii=True)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    return value


def build_http_tool_schemas(
    custom_tools: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    api_tools: list[ApiTool] | None = None,
) -> list[dict[str, Any]]:
    """Build LLM function tool declarations from `RuntimeConfig.custom_tools` and `ApiTool` rows."""
    schemas: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for tool_def in custom_tools or ():
        name = str(tool_def.get("name") or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        raw_schema = dict(tool_def.get("schema") or {})
        params = raw_schema.get("parameters") or {
            "type": "object",
            "properties": raw_schema.get("properties", {}),
            "required": raw_schema.get("required", []),
        }
        description = str(
            raw_schema.get("description")
            or tool_def.get("description")
            or f"Invoke external HTTP tool {name}."
        )
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": params,
                },
            }
        )

    for api_tool in api_tools or ():
        if not getattr(api_tool, "enabled", True):
            continue
        name = str(api_tool.name or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        params = dict(
            getattr(api_tool, "input_schema", None)
            or getattr(api_tool, "parameters_schema", None)
            or {"type": "object", "properties": {}}
        )
        if "type" not in params:
            params["type"] = "object"
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(api_tool.description or f"Invoke API tool {name}."),
                    "parameters": params,
                },
            }
        )

    return schemas


async def execute_agent_http_tool(
    *,
    tool_spec: dict[str, Any],
    arguments: dict[str, Any],
    session: AsyncSession | None = None,
    tenant_id: uuid.UUID | None = None,
    client: httpx.AsyncClient | None = None,
    require_https: bool = False,
) -> dict[str, Any]:
    """Execute a custom HTTP tool defined on an Agent (`AgentTool` / `RuntimeConfig.custom_tools`) or `ApiTool`."""
    name = str(tool_spec.get("name") or "http_tool").strip()
    raw_schema = dict(tool_spec.get("schema") or {})
    auth_binding = dict(tool_spec.get("auth_binding") or {})

    # Check if this maps to a persisted ApiTool row first when session + tenant_id are provided
    if session is not None and tenant_id is not None and not raw_schema.get("url") and not tool_spec.get("url"):
        api_row = (
            await session.execute(
                select(ApiTool).where(
                    ApiTool.tenant_id == tenant_id,
                    ApiTool.name == name,
                    ApiTool.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if api_row is not None:
            execution = await api_tool_service.execute(
                session,
                api_row,
                arguments or {},
                idempotency_key=f"{name}:{uuid.uuid4().hex[:12]}",
                client=client,
            )
            return {
                "ok": execution.ok,
                "tool": name,
                "status_code": execution.status_code,
                "result": execution.body,
                "error": execution.error_category,
            }

    url_template = str(
        tool_spec.get("url")
        or raw_schema.get("url")
        or raw_schema.get("endpoint")
        or ""
    ).strip()
    if not url_template:
        return {"ok": False, "tool": name, "error": "Tool endpoint URL is not configured."}

    method = str(
        tool_spec.get("method")
        or raw_schema.get("method")
        or "POST"
    ).strip().upper()
    if method not in _SAFE_METHODS:
        return {"ok": False, "tool": name, "error": f"Unsupported HTTP method: {method}"}

    timeout_seconds = float(
        tool_spec.get("timeout_seconds")
        or raw_schema.get("timeout_seconds")
        or 8.0
    )
    timeout_seconds = max(0.05, min(timeout_seconds, 30.0))

    rendered_url = _interpolate_string(url_template, arguments or {})
    try:
        validate_outbound_url(rendered_url, require_https=require_https)
    except OutboundUrlError as exc:
        log.warning("tool.http.ssrf_blocked", tool=name, url=rendered_url, reason=str(exc))
        return {"ok": False, "tool": name, "error": f"Blocked by SSRF policy: {exc}"}

    headers: dict[str, str] = {"Accept": "application/json"}
    raw_headers = dict(tool_spec.get("headers") or raw_schema.get("headers") or {})
    for hk, hv in raw_headers.items():
        headers[str(hk)] = _interpolate_string(
            _resolve_secret(str(hv), tenant_id), arguments or {}
        )

    # Auth binding resolution (bearer token, api_key header)
    auth_type = str(auth_binding.get("type") or "").strip().lower()
    if auth_type == "bearer":
        token = _resolve_secret(
            str(auth_binding.get("token") or auth_binding.get("secret") or ""), tenant_id
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_type in {"api_key", "header"}:
        header_name = str(auth_binding.get("header_name") or "X-API-Key")
        secret_val = _resolve_secret(
            str(auth_binding.get("api_key") or auth_binding.get("secret") or ""), tenant_id
        )
        if secret_val:
            headers[header_name] = secret_val

    body_template = tool_spec.get("body_template") or raw_schema.get("body_template")
    if body_template is not None:
        payload = _interpolate_value(body_template, arguments or {})
    else:
        payload = dict(arguments or {})

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    try:
        if method in {"GET", "DELETE"}:
            response = await http_client.request(
                method,
                rendered_url,
                params={k: str(v) for k, v in (payload if isinstance(payload, dict) else {}).items()},
                headers=headers,
                timeout=timeout_seconds,
            )
        else:
            response = await http_client.request(
                method,
                rendered_url,
                json=payload,
                headers=headers,
                timeout=timeout_seconds,
            )

        try:
            parsed_body = response.json()
        except ValueError:
            parsed_body = {"text": response.text[:2000]}

        scrubbed = _scrub_value(parsed_body if isinstance(parsed_body, dict) else {"data": parsed_body})
        ok = 200 <= response.status_code < 300
        return {
            "ok": ok,
            "tool": name,
            "status_code": response.status_code,
            "result": scrubbed,
            "error": None if ok else f"HTTP {response.status_code}",
        }
    except httpx.TimeoutException:
        return {
            "ok": False,
            "tool": name,
            "status_code": None,
            "error": f"Tool {name!r} timed out after {timeout_seconds}s",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "tool": name,
            "status_code": None,
            "error": f"HTTP transport error: {str(exc)[:160]}",
        }
    finally:
        if owns_client:
            await http_client.aclose()
