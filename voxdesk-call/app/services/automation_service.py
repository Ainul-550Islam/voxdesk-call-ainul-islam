"""Automation service (Batch 01 enterprise expansion).

A bounded event-reaction engine over the closed ``TriggerEvent`` vocabulary.
The guarantees:

* **Deterministic matching.** ``evaluate`` is a pure function of the event and
  payload: a given event selects the same automations every time.
* **Idempotent execution.** ``run_idempotency_key`` (from the domain) derives
  from tenant + automation + event + business event id, so a replayed webhook
  or a double-delivered event collapses to one automation run.
* **Cooldown and deduplication.** ``should_run`` refuses to fire when the last
  run for the same key is within the cooldown window, or when the max-per-event
  budget is exhausted.
* **No unbounded engine.** There is no dynamic event registration and no
  arbitrary action execution — actions reuse the workflow controlled-action
  dispatcher, so the two features share one definition of "safe to run".

Persistence honesty: the in-process registry is a request cache. When a
database session is present, runs and action receipts are written to PostgreSQL
before the request returns. A crash before that write is recovered by the job
worker, not by process memory.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.logging import log
from app.domain.automation_models import (
    AutomationDefinition,
    AutomationRun,
    AutomationStatus,
    TriggerEvent,
    run_idempotency_key,
)
from app.domain.workflow_models import WorkflowAction

#: tenant_id -> automation_id -> definition
_REGISTRY: dict[str, dict[str, AutomationDefinition]] = {}
#: tenant_id -> run_id -> run
_RUNS: dict[str, dict[str, AutomationRun]] = {}
#: tenant_id -> automation_id -> last completed run timestamp (cooldown)
_LAST_RUN_AT: dict[str, dict[str, str]] = {}


def _slot(store: dict, *keys: str) -> dict:
    node: dict = store
    for key in keys:
        node = node.setdefault(key, {})
    return node


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_automation(definition: AutomationDefinition) -> list[str]:
    return definition.validate()


def register_automation(tenant_id: str, definition: AutomationDefinition) -> AutomationDefinition:
    """Register (or update) an automation. Idempotent on definition id."""
    if definition.tenant_id != tenant_id:
        raise BadRequestError("automation does not belong to this tenant")
    problems = validate_automation(definition)
    if problems:
        raise BadRequestError("; ".join(problems))
    _slot(_REGISTRY, tenant_id)[definition.id] = definition
    return definition


def get_automation(tenant_id: str, automation_id: str) -> AutomationDefinition:
    definition = _slot(_REGISTRY, tenant_id).get(automation_id)
    if definition is None:
        raise NotFoundError("automation not found")
    return definition


def list_automations(tenant_id: str) -> list[AutomationDefinition]:
    return sorted(_slot(_REGISTRY, tenant_id).values(), key=lambda a: a.name)


def set_enabled(tenant_id: str, automation_id: str, enabled: bool) -> AutomationDefinition:
    definition = get_automation(tenant_id, automation_id)
    updated = replace(
        definition, status=AutomationStatus.ENABLED if enabled else AutomationStatus.DISABLED
    )
    _slot(_REGISTRY, tenant_id)[automation_id] = updated
    return updated


# ------------------------------------------------------------------ evaluate ---


def evaluate(
    tenant_id: str, event: TriggerEvent, payload: dict[str, Any]
) -> list[AutomationDefinition]:
    """Select the enabled automations whose filters match the event payload."""
    matches = [
        automation
        for automation in _slot(_REGISTRY, tenant_id).values()
        if automation.status is AutomationStatus.ENABLED
        and automation.event is event
        and automation.matches(payload)
    ]
    return sorted(matches, key=lambda a: a.name)


def deduplicate(tenant_id: str, automation: AutomationDefinition, business_event_id: str) -> str:
    """The stable deduplication key for one business event."""
    return run_idempotency_key(tenant_id, automation.id, automation.event, business_event_id)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def should_run(
    tenant_id: str,
    automation: AutomationDefinition,
    business_event_id: str,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Decide whether the automation may fire for this event right now.

    Returns ``(ok, reason)`` — ``ok=False`` with a reason when deduplication or
    cooldown blocks the run.
    """
    key = deduplicate(tenant_id, automation, business_event_id)
    moment = now or datetime.now(timezone.utc)

    # Deduplication: the same business event may only run max_per_event times.
    existing = [
        r
        for r in _RUNS.get(tenant_id, {}).values()
        if r.idempotency_key == key and r.status == "completed"
    ]
    if len(existing) >= automation.policy.max_per_event:
        return False, "max_per_event budget exhausted"

    # Cooldown: one automation may not fire again until the window elapses.
    last_run = _LAST_RUN_AT.get(tenant_id, {}).get(automation.id)
    if last_run is not None and automation.policy.cooldown_seconds > 0:
        elapsed = moment - _parse_iso(last_run)
        if elapsed < timedelta(seconds=automation.policy.cooldown_seconds):
            return False, "cooldown active"
    return True, ""


# ------------------------------------------------------------------ execute ---


async def _dispatch(
    tenant_id: str,
    action: WorkflowAction,
    payload: dict[str, Any],
    session: AsyncSession | None,
) -> tuple[str, str]:
    """Reuse the workflow controlled-action dispatcher (one definition of safe)."""
    from app.services import workflow_service

    return await workflow_service._execute_action(tenant_id, action, payload, session)


def _scope_ids(tenant_id: str, environment_id: str | None) -> tuple[uuid.UUID, uuid.UUID] | None:
    if not environment_id:
        return None
    try:
        return uuid.UUID(str(tenant_id)), uuid.UUID(str(environment_id))
    except ValueError:
        return None


async def _record_durable_run(
    session: AsyncSession | None,
    tenant_id: str,
    environment_id: str | None,
    run: AutomationRun,
) -> None:
    """Write the run when a real environment is known. Memory-only tests skip this."""
    parsed = _scope_ids(tenant_id, environment_id)
    if session is None or parsed is None:
        return
    from app.automation.durable_repository import upsert_run

    tenant_key, environment_key = parsed
    await upsert_run(
        session,
        run_id=run.id,
        tenant_id=tenant_key,
        environment_id=environment_key,
        automation_id=run.automation_id,
        idempotency_key=run.idempotency_key,
        event=run.event.value if hasattr(run.event, "value") else str(run.event),
        business_event_id=run.business_event_id,
        status=run.status,
        attempts=run.attempts,
        last_error=run.last_error,
    )


async def execute_automation(
    tenant_id: str,
    automation: AutomationDefinition,
    business_event_id: str,
    payload: dict[str, Any],
    *,
    session: AsyncSession | None = None,
) -> AutomationRun:
    """Fire one automation for one business event, with dedup + cooldown."""
    bound_environment = getattr(automation, "environment_id", None)
    if bound_environment is None and session is not None:
        from app.db.models import Automation as AutomationRow

        row = await session.get(AutomationRow, automation.id)
        if row is not None and str(row.tenant_id) == str(tenant_id):
            bound_environment = str(row.environment_id)
    claimed = [
        str(payload.get(key))
        for key in ("environment_id", "target_environment_id", "resource_environment_id")
        if payload.get(key)
    ]
    from app.environments.resource_policy import cross_environment_denied

    denied = any(cross_environment_denied(bound_environment, claim) for claim in claimed) or (
        bound_environment is None and len(set(claimed)) > 1
    )
    if denied:
        key = deduplicate(tenant_id, automation, business_event_id)
        run = AutomationRun(
            id=key,
            automation_id=automation.id,
            tenant_id=tenant_id,
            idempotency_key=key,
            event=automation.event,
            business_event_id=business_event_id,
            status="cancelled",
            last_error="cross_environment_denied",
            created_at=_now(),
        )
        _slot(_RUNS, tenant_id)[run.id] = run
        log.info(
            "automation.cross_environment_denied",
            tenant_id=tenant_id,
            automation_id=automation.id[:8],
        )
        return run

    scoped_payload = dict(payload)
    scoped_payload["tenant_id"] = str(tenant_id)
    if bound_environment is not None:
        # The worker's environment is the automation's, never a client claim.
        scoped_payload["environment_id"] = str(bound_environment)
        scoped_payload["target_environment_id"] = str(bound_environment)
        scoped_payload["resource_environment_id"] = str(bound_environment)
    organization_id = getattr(automation, "organization_id", None)
    if organization_id is None and session is not None:
        from app.db.models import Tenant

        try:
            tenant_key = uuid.UUID(str(tenant_id))
        except ValueError:
            tenant_key = None
        if tenant_key is not None:
            tenant = await session.get(Tenant, tenant_key)
            if tenant is not None and tenant.organization_id is not None:
                organization_id = str(tenant.organization_id)
    if organization_id is not None:
        scoped_payload["organization_id"] = str(organization_id)

    ok, reason = should_run(tenant_id, automation, business_event_id)
    key = deduplicate(tenant_id, automation, business_event_id)
    if not ok:
        run = AutomationRun(
            id=key,
            automation_id=automation.id,
            tenant_id=tenant_id,
            idempotency_key=key,
            event=automation.event,
            business_event_id=business_event_id,
            status="cancelled",
            last_error=reason,
            created_at=_now(),
        )
        _slot(_RUNS, tenant_id)[run.id] = run
        log.info(
            "automation.suppressed",
            tenant_id=tenant_id,
            automation_id=automation.id[:8],
            reason=reason,
        )
        return run

    run = AutomationRun(
        id=key,
        automation_id=automation.id,
        tenant_id=tenant_id,
        idempotency_key=key,
        event=automation.event,
        business_event_id=business_event_id,
        status="running",
        created_at=_now(),
    )
    _slot(_RUNS, tenant_id)[run.id] = run
    await _record_durable_run(session, tenant_id, bound_environment, run)

    results: dict[str, Any] = {}
    failed = False
    for index, action in enumerate(automation.actions):
        receipt = None
        created = True
        parsed = _scope_ids(tenant_id, bound_environment)
        if session is not None and parsed is not None:
            from app.automation.durable_repository import finish_action
            from app.automation.idempotency import begin_action

            tenant_key, environment_key = parsed
            organization_key = None
            if organization_id:
                try:
                    organization_key = uuid.UUID(str(organization_id))
                except ValueError:
                    organization_key = None
            receipt, created = await begin_action(
                session,
                tenant_id=tenant_key,
                environment_id=environment_key,
                organization_id=organization_key,
                automation_id=automation.id,
                event_id=business_event_id,
                action_id=f"{index}:{action.name}",
            )
            if not created:
                results[action.name] = {"status": "skipped", "detail": "already claimed"}
                continue
        status, detail = await _dispatch(tenant_id, action, scoped_payload, session)
        results[action.name] = {"status": status, "detail": detail}
        if receipt is not None:
            from app.automation.durable_repository import finish_action

            await finish_action(
                session,
                receipt,
                status="failed" if status in ("failed", "unsupported") else "succeeded",
            )
        if status in ("failed", "unsupported"):
            failed = True

    if failed and run.attempts + 1 < automation.policy.max_attempts:
        run = replace(
            run,
            status="pending",
            attempts=run.attempts + 1,
            last_error="one or more actions failed",
            next_attempt_at=(
                datetime.now(timezone.utc) + timedelta(seconds=automation.policy.backoff_seconds)
            ).isoformat(),
        )
    else:
        run = replace(
            run,
            status="completed" if not failed else "failed",
            attempts=run.attempts + 1,
            last_error="one or more actions failed" if failed else "",
            result_summary=results,
            finished_at=_now(),
        )

    _slot(_RUNS, tenant_id)[run.id] = run
    _LAST_RUN_AT.setdefault(tenant_id, {})[automation.id] = _now()
    await _record_durable_run(session, tenant_id, bound_environment, run)
    if session is not None:
        await session.commit()
    log.info(
        "automation.executed",
        tenant_id=tenant_id,
        automation_id=automation.id[:8],
        status=run.status,
    )
    return run


def run_history(tenant_id: str, automation_id: str = "") -> list[AutomationRun]:
    runs = _slot(_RUNS, tenant_id).values()
    if automation_id:
        runs = [r for r in runs if r.automation_id == automation_id]
    return sorted(runs, key=lambda r: r.created_at, reverse=True)


def failure_counts(tenant_id: str, automation_id: str) -> dict[str, int]:
    """Count failed runs (bounded, deterministic)."""
    runs = [r for r in _slot(_RUNS, tenant_id).values() if r.automation_id == automation_id]
    return {
        "failed": sum(1 for r in runs if r.status == "failed"),
        "suppressed": sum(1 for r in runs if r.status == "cancelled"),
        "completed": sum(1 for r in runs if r.status == "completed"),
    }
