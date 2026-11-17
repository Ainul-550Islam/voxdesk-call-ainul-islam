#!/usr/bin/env python3
"""Generate the published API reference from the FastAPI OpenAPI schema.

Only the buyer-facing surfaces listed in ``PUBLISHED_PREFIXES`` are treated as
published contracts. All of those operations must have an operation summary
and description; an omission fails the build rather than silently publishing
an empty endpoint page. Other API families are counted in the index but are not
represented as a buyer contract.

Usage::

    python scripts/build_api_reference.py
    python scripts/build_api_reference.py --check
    python scripts/build_api_reference.py --json build/api-reference-audit.json

Exit codes: 0 clean · 1 published operation is undocumented ·
2 the application's OpenAPI schema could not be built.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# An explicit list avoids accidentally treating similar prefixes (for example,
# /api/v1/publicity) as part of the published API.
PUBLISHED_PREFIXES: tuple[str, ...] = (
    "/api/v1/public",
    "/api/v1/public-keys",
    "/api/billing",
    "/api/whitelabel",
    "/api/agent-templates",
)

INTERNAL_PREFIXES: Mapping[str, str] = {
    "/api/v1/testing": "test harness, not shipped to buyers",
    "/api/v1/conductor": "internal reasoning surface, unstable by design",
    "/api/v1/evaluations": "internal evaluation runner",
    "/api/v1/integrations": "provider plumbing, documented per integration",
    "/api/v1/telephony": "superseded by /api/telephony; kept for compatibility",
    "/api/v1/agents": "superseded by /api/agents",
    "/api/v1/collab": "internal collaboration fan-out",
    "/api/v1/helpdesk": "pre-GA",
    "/api/v1/analytics": "internal dashboards API",
}
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def build_openapi() -> dict[str, Any]:
    """Return the same OpenAPI document exposed by the running FastAPI app."""
    from app.main import app

    return app.openapi()


def _is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def is_published(path: str) -> bool:
    """Whether a path belongs to an explicitly published API family."""
    return any(_is_under(path, prefix) for prefix in PUBLISHED_PREFIXES)


def _operations(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for path, path_item in (spec.get("paths") or {}).items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                operations.append(
                    {"method": method.upper(), "path": path, "op": operation}
                )
    return sorted(operations, key=lambda row: (row["path"], row["method"]))


def audit(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Report missing summaries/descriptions on the published contract."""
    operations = _operations(spec)
    published = [row for row in operations if is_published(row["path"])]
    undocumented: list[dict[str, str]] = []
    for row in published:
        operation = row["op"]
        missing: list[str] = []
        if not (operation.get("summary") or "").strip():
            missing.append("summary")
        if not (operation.get("description") or "").strip():
            missing.append("description")
        if missing:
            undocumented.append(
                {
                    "method": row["method"],
                    "path": row["path"],
                    "missing": ", ".join(missing),
                }
            )
    return {
        "total_operations": len(operations),
        "published_operations": len(published),
        "internal_operations": len(operations) - len(published),
        "undocumented": undocumented,
        "published_prefixes": list(PUBLISHED_PREFIXES),
        "internal_prefixes": dict(INTERNAL_PREFIXES),
        "ok": not undocumented,
    }


def _resolve_ref(spec: Mapping[str, Any], schema: Any) -> Any:
    """Resolve a local OpenAPI JSON pointer, preserving sibling annotations."""
    if not isinstance(schema, Mapping) or not isinstance(schema.get("$ref"), str):
        return schema
    ref = schema["$ref"]
    if not ref.startswith("#/components/"):
        return schema
    target: Any = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or part not in target:
            return schema
        target = target[part]
    if not isinstance(target, Mapping):
        return schema
    merged = dict(target)
    merged.update({key: value for key, value in schema.items() if key != "$ref"})
    return merged


def _schema_label(schema: Any, spec: Mapping[str, Any]) -> str:
    if not isinstance(schema, Mapping):
        return ""
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    resolved = _resolve_ref(spec, schema)
    if resolved is not schema:
        return _schema_label(resolved, spec)
    if schema.get("type") == "array":
        return f"array of {_schema_label(schema.get('items'), spec) or 'any'}"
    for key in ("anyOf", "oneOf", "allOf"):
        members = schema.get(key)
        if isinstance(members, list):
            return " | ".join(
                label for label in (_schema_label(item, spec) for item in members) if label
            )
    if "enum" in schema:
        values = ", ".join(f"`{value}`" for value in schema["enum"][:12])
        return f"{schema.get('type', 'enum')} — one of {values}"
    return str(schema.get("type") or schema.get("format") or "object")


def _model_example(schema: Any, spec: Mapping[str, Any]) -> Any:
    """Return one schema example, including one stored on a referenced model."""
    if not isinstance(schema, Mapping):
        return None
    for key in ("example", "examples"):
        value = schema.get(key)
        if value is not None:
            if key == "examples" and isinstance(value, list):
                return value[0] if value else None
            return value
    resolved = _resolve_ref(spec, schema)
    if resolved is not schema:
        return _model_example(resolved, spec)
    for key in ("anyOf", "oneOf", "allOf"):
        members = schema.get(key)
        if isinstance(members, list):
            for member in members:
                example = _model_example(member, spec)
                if example is not None:
                    return example
    return None


def _render_schema_properties(schema: Any, spec: Mapping[str, Any], lines: list[str]) -> None:
    resolved = _resolve_ref(spec, schema)
    if not isinstance(resolved, Mapping):
        return
    properties = resolved.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        return
    required = set(resolved.get("required") or [])
    lines.extend([
        "| Field | Type | Required | Description |",
        "| --- | --- | --- | --- |",
    ])
    for name, property_schema in properties.items():
        if not isinstance(property_schema, Mapping):
            property_schema = {}
        description = str(property_schema.get("description") or "").replace("|", "\\|").replace("\n", " ")
        kind = _schema_label(property_schema, spec).replace("|", "\\|")
        lines.append(
            f"| `{name}` | {kind} | {'yes' if name in required else 'no'} | {description} |"
        )
    lines.append("")


def _render_operation(row: Mapping[str, Any], spec: Mapping[str, Any], lines: list[str]) -> None:
    operation = row["op"]
    method = row["method"]
    path = row["path"]
    lines.extend([f"## {method} `{path}`", ""])
    summary = (operation.get("summary") or "").strip()
    if summary:
        lines.extend([f"**{summary}**", ""])
    description = (operation.get("description") or "").strip()
    if description:
        lines.extend([description, ""])
    if operation.get("tags"):
        lines.extend([f"*Tags:* {', '.join(operation['tags'])}", ""])
    if operation.get("deprecated"):
        lines.extend(["!!! warning \"Deprecated\"", "    This operation is deprecated.", ""])

    parameters = operation.get("parameters") or []
    if parameters:
        lines.extend([
            "### Parameters",
            "",
            "| Name | In | Type | Required | Description |",
            "| --- | --- | --- | --- | --- |",
        ])
        for parameter in parameters:
            if not isinstance(parameter, Mapping):
                continue
            schema = parameter.get("schema") or {}
            description = str(parameter.get("description") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{parameter.get('name', '')}` | {parameter.get('in', '')} | "
                f"{_schema_label(schema, spec)} | "
                f"{'yes' if parameter.get('required') else 'no'} | {description} |"
            )
        lines.append("")

    request_content = (operation.get("requestBody") or {}).get("content") or {}
    for media_type, media in request_content.items():
        if not isinstance(media, Mapping):
            continue
        schema = media.get("schema") or {}
        lines.extend([f"### Request body — `{media_type}`", ""])
        label = _schema_label(schema, spec)
        if label:
            lines.extend([f"Schema: `{label}`", ""])
        _render_schema_properties(schema, spec, lines)
        example = media.get("example")
        if example is None:
            examples = media.get("examples")
            if isinstance(examples, Mapping) and examples:
                first = next(iter(examples.values()))
                example = first.get("value") if isinstance(first, Mapping) else first
        if example is None:
            example = _model_example(schema, spec)
        if example is not None:
            lines.extend(["Example:", "", "```json", json.dumps(example, indent=2, ensure_ascii=False), "```", ""])

    responses = operation.get("responses") or {}
    if responses:
        lines.extend(["### Responses", "", "| Status | Schema | Description |", "| --- | --- | --- |"])
        for code, response in sorted(responses.items()):
            if not isinstance(response, Mapping):
                continue
            response_content = response.get("content") or {}
            if response_content:
                media = next(iter(response_content.values()))
                schema = media.get("schema") if isinstance(media, Mapping) else None
                label = _schema_label(schema, spec)
            else:
                label = "no body"
            description = str(response.get("description") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {code} | {label} | {description} |")
        lines.append("")

    security = operation.get("security")
    if security:
        schemes = sorted({name for entry in security for name in entry})
        lines.extend([f"*Auth:* {', '.join(schemes)}", ""])


def render_reference(spec: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, str]:
    """Return a generated Markdown file per published API surface and an index."""
    grouped: dict[str, list[dict[str, Any]]] = {prefix: [] for prefix in PUBLISHED_PREFIXES}
    internal: list[dict[str, Any]] = []
    for row in _operations(spec):
        matched = next((prefix for prefix in PUBLISHED_PREFIXES if _is_under(row["path"], prefix)), None)
        if matched is None:
            internal.append(row)
        else:
            grouped[matched].append(row)

    files: dict[str, str] = {}
    index = [
        "# API reference",
        "",
        f"This reference is generated from the application's OpenAPI schema: "
        f"**{report['published_operations']} published operations** across "
        f"{len(PUBLISHED_PREFIXES)} documented surfaces. Run `python "
        f"scripts/build_api_reference.py` to regenerate it. The build fails if "
        "a published operation lacks a summary or description.",
        "",
        "## Published surfaces",
        "",
        "| Surface | Operations |",
        "| --- | ---: |",
    ]
    for prefix, rows in grouped.items():
        slug = prefix.strip("/").replace("/", "-")
        filename = f"{slug}.md"
        files[filename] = "\n".join(_render_surface(prefix, rows, spec)).rstrip() + "\n"
        index.append(f"| [`{prefix}`]({filename}) | {len(rows)} |")

    index.extend([
        "",
        "## Internal API families (not published)",
        "",
        "These operation counts are not a promise of compatibility. Internal "
        "routes are intentionally outside the buyer-facing reference.",
        "",
        "| Prefix | Operations | Classification |",
        "| --- | ---: | --- |",
    ])
    classified: set[str] = set()
    for prefix, reason in INTERNAL_PREFIXES.items():
        count = sum(1 for row in internal if _is_under(row["path"], prefix))
        if count:
            index.append(f"| `{prefix}` | {count} | {reason} |")
            classified.add(prefix)
    unclassified = [
        row for row in internal
        if not any(_is_under(row["path"], prefix) for prefix in INTERNAL_PREFIXES)
    ]
    if unclassified:
        index.append(f"| *(other)* | {len(unclassified)} | not part of the published contract |")
    files["index.md"] = "\n".join(index) + "\n"
    return files


def _render_surface(prefix: str, rows: list[dict[str, Any]], spec: Mapping[str, Any]) -> list[str]:
    lines = [f"# `{prefix}`", "", f"{len(rows)} operations.", ""]
    for row in rows:
        _render_operation(row, spec, lines)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the published API reference from OpenAPI.")
    parser.add_argument("--check", action="store_true", help="audit only; do not write reference files")
    parser.add_argument("--out", default="docs-site/docs/api", help="output directory")
    parser.add_argument("--json", default="", help="also write the audit report as JSON")
    args = parser.parse_args(argv)

    try:
        spec = build_openapi()
    except Exception as exc:  # pragma: no cover - for a broken app import/schema only
        print(f"could not build the OpenAPI document: {exc}", file=sys.stderr)
        return 2

    report = audit(spec)
    print(
        f"api reference: {report['published_operations']} published, "
        f"{report['internal_operations']} internal, {report['total_operations']} total"
    )
    if report["undocumented"]:
        print(f"\n{len(report['undocumented'])} published operation(s) need documentation:")
        for row in report["undocumented"]:
            print(f"  {row['method']:6s} {row['path']} (missing {row['missing']})")

    if args.json:
        audit_path = Path(args.json)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"audit written to {audit_path}")

    if not report["ok"]:
        print("FAILED: the published API is not fully documented.", file=sys.stderr)
        return 1
    if args.check:
        print("OK: every published operation has a summary and description.")
        return 0

    output_dir = Path(args.out)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    files = render_reference(spec, report)
    for name, content in files.items():
        (output_dir / name).write_text(content)
    print(f"wrote {len(files)} reference files to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
