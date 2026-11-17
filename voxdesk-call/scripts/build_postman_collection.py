#!/usr/bin/env python3
"""Generate a Postman v2.1 collection from the published OpenAPI operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_api_reference import (  # noqa: E402
    PUBLISHED_PREFIXES,
    _model_example,
    _operations,
    _resolve_ref,
    build_openapi,
    is_published,
)

DEFAULT_OUTPUT = ROOT / "docs-site" / "docs" / "postman" / "voxdesk-published-api.postman_collection.json"


def _schema_sample(schema: Any, spec: Mapping[str, Any], name: str = "value", depth: int = 0) -> Any:
    """Build a copyable placeholder object from a JSON Schema node."""
    if depth > 8 or not isinstance(schema, Mapping):
        return f"{{{{{name}}}}}"
    example = _model_example(schema, spec)
    if example is not None:
        return example
    resolved = _resolve_ref(spec, schema)
    if resolved is not schema:
        return _schema_sample(resolved, spec, name, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for variant in variants:
                if isinstance(variant, Mapping) and variant.get("type") != "null":
                    return _schema_sample(variant, spec, name, depth + 1)
    kind = schema.get("type")
    if kind == "object" or isinstance(schema.get("properties"), Mapping):
        props = schema.get("properties") or {}
        required = set(schema.get("required") or props.keys())
        return {
            field: _schema_sample(props[field], spec, str(field), depth + 1)
            for field in props
            if field in required
        }
    if kind == "array":
        minimum = int(schema.get("minItems") or 0)
        count = max(1, min(minimum, 3))
        return [_schema_sample(schema.get("items") or {}, spec, name, depth + 1) for _ in range(count)]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    if kind == "boolean":
        return True
    if kind == "integer":
        return schema.get("minimum", 1 if schema.get("exclusiveMinimum") else 0)
    if kind == "number":
        return schema.get("minimum", 1 if schema.get("exclusiveMinimum") else 0)
    if kind == "string":
        if schema.get("format") == "date":
            return "2026-11-08"
        if schema.get("format") == "date-time":
            return "2026-11-08T12:00:00Z"
        if schema.get("format") == "email":
            return "operator@example.com"
        if schema.get("format") == "uri":
            return "https://example.com"
        return f"{{{{{name}}}}}"
    return f"{{{{{name}}}}}"


def _parameter_example(parameter: Mapping[str, Any]) -> Any:
    for key in ("example", "examples"):
        value = parameter.get(key)
        if value is not None:
            if key == "examples" and isinstance(value, Mapping) and value:
                first = next(iter(value.values()))
                return first.get("value") if isinstance(first, Mapping) else first
            if key == "examples" and isinstance(value, list) and value:
                return value[0]
            return value
    schema = parameter.get("schema") or {}
    for key in ("example", "examples", "default"):
        value = schema.get(key)
        if value is not None:
            if key == "examples" and isinstance(value, list) and value:
                return value[0]
            return value
    return "replace-me"


def _request_url(path: str, parameters: list[Mapping[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    segments: list[str] = []
    cursor = 0
    while cursor < len(path):
        if path[cursor] == "{":
            close = path.find("}", cursor + 1)
            if close > cursor:
                name = path[cursor + 1 : close]
                segments.append(f"{{{{{name}}}}}")
                cursor = close + 1
                continue
        segments.append(path[cursor])
        cursor += 1
    raw_path = "".join(segments)
    query = []
    for parameter in parameters:
        if parameter.get("in") != "query":
            continue
        query.append(
            {
                "key": parameter.get("name", ""),
                "value": str(_parameter_example(parameter)),
                "description": parameter.get("description", ""),
                "disabled": not bool(parameter.get("required")),
            }
        )
    return "{{baseUrl}}" + raw_path, query


def _security_auth(operation: Mapping[str, Any]) -> dict[str, Any] | None:
    security = operation.get("security") or []
    names = {name for alternative in security for name in alternative}
    if any("bearer" in name.lower() or "httpbearer" in name.lower() for name in names):
        return {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{accessToken}}", "type": "string"}],
        }
    return None


def _item(row: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    operation = row["op"]
    parameters = [p for p in operation.get("parameters", []) if isinstance(p, Mapping)]
    raw_url, query = _request_url(row["path"], parameters)
    headers: list[dict[str, str]] = [{"key": "Accept", "value": "application/json"}]
    body_content = (operation.get("requestBody") or {}).get("content") or {}
    body_media_type = next(iter(body_content), None)
    if body_media_type and not body_media_type.startswith("multipart/form-data"):
        headers.append({"key": "Content-Type", "value": body_media_type.split(";", 1)[0]})
    for parameter in parameters:
        if parameter.get("in") != "header":
            continue
        headers.append(
            {
                "key": str(parameter.get("name", "")),
                "value": str(_parameter_example(parameter)),
                "description": str(parameter.get("description", "")),
            }
        )

    url: dict[str, Any] = {"raw": raw_url}
    if query:
        url["query"] = query
    request: dict[str, Any] = {
        "method": row["method"],
        "header": headers,
        "url": url,
    }
    auth = _security_auth(operation)
    if auth:
        request["auth"] = auth
    if body_media_type:
        media = body_content[body_media_type]
        schema = media.get("schema") if isinstance(media, Mapping) else None
        if body_media_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            resolved = _resolve_ref(spec, schema or {})
            properties = resolved.get("properties", {}) if isinstance(resolved, Mapping) else {}
            form_fields = []
            for name, field_schema in properties.items():
                if not isinstance(field_schema, Mapping):
                    field_schema = {}
                description = str(field_schema.get("description") or "")
                binary = field_schema.get("format") == "binary" or field_schema.get("contentMediaType") == "application/octet-stream"
                if body_media_type.startswith("multipart/form-data") and binary:
                    form_fields.append({"key": str(name), "type": "file", "src": None, "description": description or "Select a file."})
                    continue
                value = _schema_sample(field_schema, spec, str(name))
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                form_fields.append({"key": str(name), "type": "text", "value": str(value), "description": description})
            mode = "formdata" if body_media_type.startswith("multipart/form-data") else "urlencoded"
            request["body"] = {"mode": mode, mode: form_fields}
        else:
            body_example = None
            if isinstance(media, Mapping):
                body_example = media.get("example")
                if body_example is None:
                    examples = media.get("examples")
                    if isinstance(examples, Mapping) and examples:
                        first = next(iter(examples.values()))
                        body_example = first.get("value") if isinstance(first, Mapping) else first
            if body_example is None:
                body_example = _schema_sample(schema or {}, spec)
            if isinstance(body_example, str):
                raw_body = body_example
            else:
                raw_body = json.dumps(body_example, indent=2, ensure_ascii=False)
            request["body"] = {"mode": "raw", "raw": raw_body, "options": {"raw": {"language": "json"}}}

    description = (operation.get("description") or "").strip()
    summary = (operation.get("summary") or "").strip()
    notes = [summary, description] if description else [summary]
    if body_media_type:
        notes.append("Review example values and replace placeholders before sending.")
    request["description"] = "\n\n".join(note for note in notes if note)
    name = f"{row['method']} {row['path']} — {summary}" if summary else f"{row['method']} {row['path']}"
    return {"name": name, "request": request, "response": []}


def build_collection(spec: Mapping[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {prefix: [] for prefix in PUBLISHED_PREFIXES}
    variables: dict[str, str] = {"baseUrl": "http://localhost:8000", "accessToken": ""}
    operations = [row for row in _operations(spec) if is_published(row["path"])]
    for row in operations:
        prefix = next(prefix for prefix in PUBLISHED_PREFIXES if row["path"] == prefix or row["path"].startswith(prefix + "/"))
        grouped[prefix].append(_item(row, spec))
        for parameter in row["op"].get("parameters", []):
            if not isinstance(parameter, Mapping) or parameter.get("in") != "path":
                continue
            name = str(parameter.get("name", ""))
            if name:
                value = _parameter_example(parameter)
                variables.setdefault(name, str(value))

    items = []
    for prefix, requests in grouped.items():
        items.append({"name": prefix, "item": requests})
    return {
        "info": {
            "_postman_id": str(uuid5(NAMESPACE_URL, "urn:voxdesk:collections:published-api")),
            "name": "VoxDesk Published API",
            "description": (
                f"Generated from the FastAPI OpenAPI schema: {len(operations)} published operations. "
                "Internal endpoints are excluded. Set baseUrl and accessToken before use; "
                "public widget/demo endpoints have their own key and abuse-gate requirements."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": key, "value": value, "type": "string"}
            for key, value in sorted(variables.items(), key=lambda pair: (pair[0] != "baseUrl", pair[0] != "accessToken", pair[0]))
        ],
        "item": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Postman JSON output path")
    parser.add_argument("--check", action="store_true", help="fail if the generated file is stale")
    args = parser.parse_args()
    try:
        spec = build_openapi()
    except Exception as exc:
        print(f"could not build the OpenAPI document: {exc}", file=sys.stderr)
        return 2
    content = json.dumps(build_collection(spec), indent=2, ensure_ascii=False) + "\n"
    output = Path(args.out)
    if not output.is_absolute():
        output = ROOT / output
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != content:
            print(f"Postman collection is stale: {output}", file=sys.stderr)
            return 1
        print(f"Postman collection is current: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    count = sum(len(folder["item"]) for folder in build_collection(spec)["item"])
    print(f"wrote {count} requests to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
