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


from datetime import datetime, timezone, timedelta

from app.agent.errors import ProviderError
from app.ai.models import GovernanceError
from app.core.rate_limit import rate_limit
from app.core.ssrf import OutboundUrlError
from app.db.models import Call, Tenant, Environment, DurableJob
from app.db.enterprise_models import BackfillJob
from app.db.telephony_models import PostCallStepRun
from app.jobs.queue import enqueue
from app.jobs.types import JobType, ExecutionResult, register_handler
from app.jobs.models import PermanentJobError
from app.telephony.call_state import TERMINAL_STATUSES, is_terminal
from app.telephony.transcription import finalize_stored_turns, TranscriptUnavailable


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


# Backfill is an admission manifest over the existing jobs/checkpoints, not a
# second queue. Each bounded child invokes the same governed extraction above.
MAX_BACKFILL_CALLS = 200
BACKFILL_PER_MINUTE = 30


async def select_backfill_calls(session, *, tenant_id, environment_id, call_ids=None, start_date=None, end_date=None):
    for value in (start_date, end_date):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Dates require an explicit timezone")
    if start_date and end_date and start_date >= end_date:
        raise ValueError("start_date must precede end_date")
    if call_ids is not None and (not call_ids or len(call_ids) > MAX_BACKFILL_CALLS or len(set(call_ids)) != len(call_ids)):
        raise ValueError("Provide 1–200 distinct call IDs")
    query = select(Call.id).where(Call.tenant_id == tenant_id, Call.environment_id == environment_id,
                                   Call.status.in_(TERMINAL_STATUSES))
    if call_ids is not None:
        query = query.where(Call.id.in_(call_ids))
    if start_date:
        query = query.where(Call.started_at >= start_date.astimezone(timezone.utc))
    if end_date:
        query = query.where(Call.started_at < end_date.astimezone(timezone.utc))
    ids = list((await session.scalars(query.order_by(Call.started_at, Call.id).limit(MAX_BACKFILL_CALLS + 1))).all())
    if call_ids is not None and set(ids) != set(call_ids):
        raise LookupError("One or more calls not found in eligible scope")
    if len(ids) > MAX_BACKFILL_CALLS:
        raise ValueError("Selection exceeds 200 calls; narrow the date range or supply call IDs")
    if not ids:
        raise ValueError("No eligible terminal calls selected")
    return ids


async def admit_backfill(session, *, tenant, schema, call_ids, actor_user_id, key_digest):
    if schema.tenant_id != tenant.id or not schema.is_active:
        raise ValueError("Schema is inactive or outside tenant scope")
    compile_fields(schema.fields)
    # Revalidate service callers; admission cannot smuggle unowned references.
    ids = await select_backfill_calls(session, tenant_id=tenant.id, environment_id=schema.environment_id, call_ids=call_ids)
    batch = BackfillJob(tenant_id=tenant.id, environment_id=schema.environment_id, schema_id=schema.id,
        schema_snapshot=snapshot(schema), call_ids=[str(value) for value in ids], job_ids=[],
        total_calls=len(ids), processed_calls=0, failed_calls=0, status="queued",
        created_by=actor_user_id, idempotency_key=key_digest)
    session.add(batch)
    await session.flush()
    jobs = []
    moment = datetime.now(timezone.utc)
    for index, call_id in enumerate(ids):
        job, _ = await enqueue(session, tenant_id=tenant.id, organization_id=tenant.organization_id,
            environment_id=schema.environment_id, job_type=JobType.ANALYSIS_BACKFILL,
            idempotency_key=f"analysis-backfill:{batch.id}:{call_id}",
            payload={"backfill_id": str(batch.id), "call_id": str(call_id)}, max_attempts=6,
            available_at=moment + timedelta(seconds=2 * index))
        jobs.append(str(job.id))
    batch.job_ids = jobs
    await session.flush()
    return batch


async def backfill_status(session, row):
    value = row.as_dict()
    value.update(environment_id=str(row.environment_id) if row.environment_id else None,
                 schema_version=(row.schema_snapshot or {}).get("version"), job_ids=list(row.job_ids or []),
                 idempotency_key="", completed_at=None, cancelled_calls=0)
    if not row.schema_snapshot or not row.job_ids:
        value.update(status="legacy_unverified", processed_calls=0, failed_calls=0)
        return value
    jobs = list((await session.scalars(select(DurableJob).where(
        DurableJob.id.in_([uuid.UUID(item) for item in row.job_ids]),
        DurableJob.tenant_id == row.tenant_id, DurableJob.environment_id == row.environment_id,
        DurableJob.job_type == JobType.ANALYSIS_BACKFILL).execution_options(populate_existing=True))).all())
    valid = len(jobs) == len(row.call_ids) == len(row.job_ids) and all(
        job.payload == {"backfill_id": str(row.id), "call_id": call_id}
        for call_id, job_id in zip(row.call_ids, row.job_ids)
        for job in jobs if str(job.id) == job_id)
    if not valid:
        value.update(status="inconsistent", processed_calls=0, failed_calls=0)
        return value
    done = sum(job.status == "succeeded" for job in jobs)
    failed = sum(job.status in {"dead_letter", "quarantined"} for job in jobs)
    cancelled = sum(job.status == "cancelled" for job in jobs)
    terminal = done + failed + cancelled == len(jobs)
    status = ("completed" if done == len(jobs) else "failed") if terminal else (
        "queued" if all(job.status == "queued" for job in jobs) else "running")
    if cancelled == len(jobs):
        status = "cancelled"
    value.update(status=status, total_calls=len(jobs), processed_calls=done, failed_calls=failed,
                 cancelled_calls=cancelled)
    if terminal and all(job.completed_at is not None for job in jobs):
        value["completed_at"] = max(job.completed_at for job in jobs).isoformat()
    return value


async def execute_backfill(job, session_factory, *, executor=None):
    from app.telephony.post_call import _WorkerAIContext
    payload = job.payload
    if not isinstance(payload, dict) or set(payload) != {"backfill_id", "call_id"}:
        raise PermanentJobError("analysis_payload_invalid", "Backfill references required")
    try:
        batch_id, call_id = uuid.UUID(payload["backfill_id"]), uuid.UUID(payload["call_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise PermanentJobError("analysis_payload_invalid", "Invalid references") from exc
    async with session_factory() as session:
        call = await session.scalar(select(Call).where(Call.id == call_id, Call.tenant_id == job.tenant_id,
            Call.environment_id == job.environment_id).with_for_update())
        batch = await session.scalar(select(BackfillJob).where(BackfillJob.id == batch_id,
            BackfillJob.tenant_id == job.tenant_id, BackfillJob.environment_id == job.environment_id))
        tenant = await session.scalar(select(Tenant).where(Tenant.id == job.tenant_id,
            Tenant.organization_id == job.organization_id))
        env = await session.scalar(select(Environment).where(Environment.id == job.environment_id,
            Environment.tenant_id == job.tenant_id))
        stored = await session.scalar(select(DurableJob).where(DurableJob.id == job.id,
            DurableJob.tenant_id == job.tenant_id, DurableJob.environment_id == job.environment_id,
            DurableJob.organization_id == job.organization_id, DurableJob.job_type == JobType.ANALYSIS_BACKFILL))
        if (call is None or batch is None or tenant is None or env is None or stored is None
                or not batch.schema_snapshot or stored.payload != payload
                or str(job.id) not in batch.job_ids or str(call_id) not in batch.call_ids
                or batch.job_ids[batch.call_ids.index(str(call_id))] != str(job.id)):
            raise PermanentJobError("analysis_scope_invalid", "Backfill not found in job scope")
        if tenant.lifecycle_status != "active" or env.status != "active" or not is_terminal(call.status):
            raise PermanentJobError("analysis_scope_inactive", "Backfill scope is inactive or call is not terminal")
        if stored.cancel_requested or stored.status == "cancelled":
            return ExecutionResult.permanent("analysis_cancelled")
        lease = stored.leased_until
        if lease is not None and lease.tzinfo is None:
            lease = lease.replace(tzinfo=timezone.utc)
        if (stored.status != "running" or stored.worker_id != job.worker_id
                or lease is None or lease <= datetime.now(timezone.utc)):
            raise PermanentJobError("analysis_not_claimed", "A current worker claim is required")
        step = f"backfill:{batch.id}"
        row = await session.scalar(select(PostCallStepRun).where(PostCallStepRun.call_id == call_id,
            PostCallStepRun.tenant_id == job.tenant_id, PostCallStepRun.environment_id == job.environment_id,
            PostCallStepRun.step == step, PostCallStepRun.pipeline_version == 1))
        if row is not None and row.status == "completed":
            return ExecutionResult.ok("Previously committed custom analysis")
        if row is None:
            row = PostCallStepRun(tenant_id=job.tenant_id, environment_id=job.environment_id,
                call_id=call_id, job_id=job.id, step=step, pipeline_version=1, attempts=0,
                status="running", output={}, telemetry=[])
            session.add(row)
        row.attempts += 1
        row.started_at, row.finished_at = datetime.now(timezone.utc), None
        row.status, row.error, row.retryable = "running", "", False
        await session.flush()
        try:
            allowed = await rate_limit(f"analysis:backfill:{job.tenant_id}:{job.environment_id}", BACKFILL_PER_MINUTE, 60)
        except RuntimeError:
            row.status, row.error = "not_configured", "redis_not_configured"
            allowed = False
        if not allowed and row.status == "running":
            row.status, row.error, row.retryable = "blocked", "analysis_rate_limited", True
        if allowed:
            try:
                text, evidence = await finalize_stored_turns(session, call, snapshot=row.output.get("transcript"))
                row.output = {"transcript": evidence}
                output = await extract(session, _WorkerAIContext(tenant, env.id), call, batch.schema_snapshot,
                                       text, executor=executor, environment_kind=env.kind)
                row.status, row.error = output.status, output.error
                row.telemetry = list(row.telemetry or []) + list(output.invocations)
                if output.status == "completed":
                    row.output = {**row.output, **output.value}
            except TranscriptUnavailable as exc:
                row.status, row.error = "blocked", exc.code
                row.retryable = exc.code == "no_transcript"
            except ProviderError as exc:
                row.status, row.error, row.retryable = "failed", exc.category[:64], bool(exc.retryable)
            except GovernanceError as exc:
                row.status, row.error = "blocked", str(exc.code)[:64]
            except OutboundUrlError:
                row.status, row.error = "blocked", "unsafe_provider_destination"
        row.finished_at = datetime.now(timezone.utc)
        await session.commit()
        if row.status == "completed":
            return ExecutionResult.ok("Pinned custom analysis persisted")
        if row.retryable:
            return ExecutionResult.transient(row.error)
        return ExecutionResult.permanent(row.error or "analysis_incomplete")


@register_handler(JobType.ANALYSIS_BACKFILL)
async def handle_analysis_backfill(job):
    from app.db.session import get_sessionmaker
    return await execute_backfill(job, get_sessionmaker())


async def preview_backfill(session, *, tenant, schema, call_ids):
    """Read-only heuristic quote from the existing routing and price tables.

    One execution, one output allowance; retries/fallback are excluded. This is
    neither an exact tokenizer count, an admission reservation nor a charge.
    """
    from sqlalchemy import func
    from app.db.models import Turn
    from app.ai import costs, gateway, routing
    ids = await select_backfill_calls(session, tenant_id=tenant.id, environment_id=schema.environment_id, call_ids=call_ids)
    env = await session.scalar(select(Environment).where(Environment.id == schema.environment_id,
                                                         Environment.tenant_id == tenant.id))
    if env is None or not schema.is_active:
        raise ValueError("Schema environment is unavailable or schema is inactive")
    choice = routing.select(tenant, await gateway.load_policy(session, tenant.id), environment_kind=env.kind)
    records = (await session.execute(select(Turn.call_id, func.count(Turn.id),
        func.coalesce(func.sum(func.length(Turn.text)), 0)).join(Call, Call.id == Turn.call_id).where(
            Call.id.in_(ids), Call.tenant_id == tenant.id, Call.environment_id == schema.environment_id
        ).group_by(Turn.call_id))).all()
    characters = {call_id: int(length) + int(count) * 16 for call_id, count, length in records}
    schema_chars = len(json.dumps(compile_fields(schema.fields)))
    estimated_tokens = sum(max(1, (characters.get(call_id, 0) + schema_chars + 220) // 4 + 2048) for call_id in ids)
    quote = costs.estimate(provider=choice.provider, tokens=estimated_tokens)
    return {"schema_id": str(schema.id), "schema_version": schema.version, "selected_calls": len(ids),
        "provider": choice.provider, "model": choice.model, "estimated_tokens": estimated_tokens,
        "estimate_method": "characters_divided_by_four_plus_2048_output_per_call",
        "missing_transcripts": len(set(ids) - set(characters)), "cost": quote,
        "includes_retries_or_fallback": False, "reservation_created": False,
        "admission_limit": MAX_BACKFILL_CALLS, "execution_limit_per_minute": BACKFILL_PER_MINUTE}
