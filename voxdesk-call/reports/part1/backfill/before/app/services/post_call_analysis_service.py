"""Versioned custom analysis on the existing schema/result tables.

Mutations flush only; callers own audit and transaction boundaries. A pipeline
pins complete definitions before inference, so later edits cannot change a retry.
Legacy results retain unknown version/provenance rather than invented evidence.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
import uuid

from jsonschema import Draft202012Validator
from sqlalchemy import select

from app.ai import post_call_llm
from app.db.enterprise_models import AnalysisSchema, AnalysisResult


def compile_fields(fields):
    if not isinstance(fields, list) or not 1 <= len(fields) <= 100:
        raise ValueError("Provide between 1 and 100 fields")
    properties, required = {}, []
    for field in fields:
        name = field.get("name", "")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,79}", name) or name in properties:
            raise ValueError("Field names must be valid and unique")
        kind = field.get("type")
        types = {"text": "string", "boolean": "boolean", "number": "number", "custom": "object", "list": "array", "enum": "string"}
        if kind not in types:
            raise ValueError("Unsupported field type")
        prop = {"type": types[kind], "description": field.get("description", "")}
        if kind == "enum":
            values = field.get("enum_values")
            if not values or len(values) > 50 or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values):
                raise ValueError("Enums require unique string values")
            prop["enum"] = values
        if kind == "list":
            item_type = field.get("items_type", "text")
            if item_type not in ("text", "number", "boolean"):
                raise ValueError("List items must be text, number or boolean")
            prop["items"] = {"type": types[item_type]}
            prop["maxItems"] = 100
        examples = field.get("examples") or []
        if not isinstance(examples, list) or len(examples) > 5:
            raise ValueError("At most five examples per field")
        validator = Draft202012Validator(prop)
        for example in examples:
            if not validator.is_valid(example):
                raise ValueError("Example does not match its field type")
        if field.get("default") is not None and not validator.is_valid(field["default"]):
            raise ValueError("Default does not match its field type")
        if examples:
            prop["examples"] = examples
        properties[name] = deepcopy(prop)
        if field.get("required"):
            required.append(name)
    schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    post_call_llm.validate_schema(schema)
    return schema


def snapshot(row):
    return {"schema_id": str(row.id), "version": row.version, "fields": deepcopy(row.fields),
            "name": row.name, "description": row.description}


async def create_schema(session, *, tenant_id, environment_id, name, description, fields):
    compile_fields(fields)
    row = AnalysisSchema(tenant_id=tenant_id, environment_id=environment_id, name=name,
                         description=description, fields=deepcopy(fields), version=1, revisions={}, is_active=True)
    session.add(row)
    await session.flush()
    row.revisions = {"1": snapshot(row)}
    await session.flush()
    return row


async def update_schema(session, row, changes):
    fields = changes.get("fields", row.fields)
    compile_fields(fields)
    history = deepcopy(row.revisions or {})
    history.setdefault(str(row.version), snapshot(row))
    definition_changed = any(key in changes and changes[key] != getattr(row, key)
                             for key in ("fields", "name", "description"))
    for key in ("fields", "name", "description", "is_active"):
        if key in changes:
            setattr(row, key, deepcopy(changes[key]))
    if definition_changed:
        row.version += 1
    history[str(row.version)] = snapshot(row)
    row.revisions = history
    await session.flush()
    return row


async def pin_schemas(session, call):
    rows = (await session.scalars(select(AnalysisSchema).where(
        AnalysisSchema.tenant_id == call.tenant_id, AnalysisSchema.environment_id == call.environment_id,
        AnalysisSchema.is_active.is_(True)).order_by(AnalysisSchema.id).limit(21))).all()
    if len(rows) > 20:
        raise ValueError("At most 20 active custom schemas per pipeline are supported")
    definitions = [snapshot(row) for row in rows]
    for definition in definitions:
        compile_fields(definition["fields"])
    # Bound persisted manifests independently of gateway prompt bounds.
    if len(json.dumps(definitions).encode()) > 262144:
        raise ValueError("Analysis manifest too large")
    return definitions


async def extract(session, ctx, call, definition, transcript, *, executor=None, environment_kind="production"):
    schema_id = uuid.UUID(definition["schema_id"])
    existing = await session.scalar(select(AnalysisResult).where(
        AnalysisResult.tenant_id == call.tenant_id, AnalysisResult.environment_id == call.environment_id,
        AnalysisResult.call_id == call.id, AnalysisResult.schema_id == schema_id,
        AnalysisResult.schema_version == definition["version"]))
    if existing is not None:
        if existing.provenance != "provider" or existing.status != "completed" or existing.schema_snapshot != definition:
            raise ValueError("Existing result does not match the pinned definition")
        return post_call_llm.AnalysisOutput("completed", {"analysis_result_id": str(existing.id)})
    output = await post_call_llm.extract_fields(session, ctx, compile_fields(definition["fields"]), transcript,
        call_id=call.id, executor=executor, environment_kind=environment_kind)
    if output.status != "completed":
        return output
    row = AnalysisResult(tenant_id=call.tenant_id, environment_id=call.environment_id, call_id=call.id,
        schema_id=schema_id, schema_version=definition["version"], schema_snapshot=deepcopy(definition),
        provenance="provider", status="completed", result=output.value)
    session.add(row)
    await session.flush()
    return post_call_llm.AnalysisOutput("completed", {"analysis_result_id": str(row.id)}, invocations=output.invocations)
