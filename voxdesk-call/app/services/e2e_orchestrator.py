"""Read-only, deterministic inspection of an agent-to-call lifecycle.

This is an evidence inspector, not a call launcher. It never invokes a carrier,
provider, workflow action, CRM mutation, or billing mutation. A missing call is
reported as NOT_RUN rather than synthesized into a successful run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enterprise_models import SalesforceConnection
from app.db.models import (
    Agent,
    AgentVersion,
    Call,
    CalendarIntegration,
    CrmIntegration,
    Environment,
)
from app.db.telephony_models import TelephonyCallSession
from app.schemas.parity import E2EInspectionResponse, E2EInspectionStep


class E2EResourceNotFound(Exception):
    """A requested agent or call is absent from the caller's tenant/scope."""


async def _tenant_crm_ready(session: AsyncSession, tenant_id: UUID) -> tuple[bool, bool]:
    """Return `(any_configured, any_recently_connected)` without reading secrets out."""
    crm_rows = (
        await session.execute(
            select(CrmIntegration).where(CrmIntegration.tenant_id == tenant_id)
        )
    ).scalars().all()
    calendar_rows = (
        await session.execute(
            select(CalendarIntegration).where(CalendarIntegration.tenant_id == tenant_id)
        )
    ).scalars().all()
    salesforce = (
        await session.execute(
            select(SalesforceConnection).where(SalesforceConnection.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    rows: list[Any] = [*crm_rows, *calendar_rows]
    configured = any(
        bool((getattr(row, "credentials_encrypted", "") or "").strip())
        for row in rows
    )
    now = datetime.now(timezone.utc)

    def has_recent_success(row: Any) -> bool:
        checked_at = getattr(row, "last_health_check_at", None)
        if checked_at is None:
            return False
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        else:
            checked_at = checked_at.astimezone(timezone.utc)
        age = now - checked_at
        return (
            bool(getattr(row, "is_enabled", False))
            and bool((getattr(row, "credentials_encrypted", "") or "").strip())
            and bool((getattr(row, "credentials_key_id", "") or "").strip())
            and getattr(row, "last_health_ok", None) is True
            and timedelta(0) <= age <= timedelta(minutes=15)
        )

    connected = any(has_recent_success(row) for row in rows)
    if salesforce is not None:
        sf_configured = bool(
            (salesforce.access_token_encrypted or "").strip()
            and (salesforce.refresh_token_encrypted or "").strip()
        )
        configured = configured or sf_configured
        # SalesforceConnection does not persist a provider health-check result;
        # an active record therefore never satisfies `connected` by itself.
    return configured, connected


def _resource_scope_clause(model: Any, environment_id: UUID | None) -> Any:
    if environment_id is None:
        return model.environment_id.is_(None)
    return or_(model.environment_id.is_(None), model.environment_id == environment_id)


def _published_version_matches_environment(
    version: AgentVersion,
    *,
    environment_id: UUID | None,
    environment_kind: str | None,
) -> bool:
    """Require the version's persisted environment binding to match the scope.

    Older snapshots can lack an environment UUID while retaining the named
    publish environment. They are accepted only when that name matches the
    resolved environment kind; missing or unverifiable environment state fails
    closed rather than accepting an arbitrary snapshot.
    """
    published_environment_id = version.published_environment_id
    if published_environment_id is not None:
        return environment_id is not None and published_environment_id == environment_id
    if environment_kind is None:
        return False
    published_environment = str(version.published_environment or "production").strip().lower()
    return published_environment == environment_kind.strip().lower()


async def inspect_agent_call_flow(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    environment_id: UUID | None,
    agent_id: UUID,
    call_id: UUID | None = None,
) -> E2EInspectionResponse:
    """Inspect persisted tenant-scoped agent/call records; do not execute them."""
    environment = None
    if environment_id is not None:
        environment = await session.get(Environment, environment_id)
        if (
            environment is None
            or environment.tenant_id != tenant_id
            or environment.status != "active"
        ):
            raise E2EResourceNotFound("Environment not found")
    environment_kind = str(environment.kind) if environment is not None else None

    agent = (
        await session.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.tenant_id == tenant_id,
                _resource_scope_clause(Agent, environment_id),
                Agent.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise E2EResourceNotFound("Agent not found")

    version = None
    if agent.published_version_id is not None:
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == agent.published_version_id,
                AgentVersion.agent_id == agent.id,
                AgentVersion.tenant_id == tenant_id,
            )
        )

    call = None
    legacy_call = None
    if call_id is not None:
        call = (
            await session.execute(
                select(TelephonyCallSession).where(
                    TelephonyCallSession.id == call_id,
                    TelephonyCallSession.tenant_id == tenant_id,
                    _resource_scope_clause(TelephonyCallSession, environment_id),
                )
            )
        ).scalar_one_or_none()
        if call is None:
            raise E2EResourceNotFound("Call not found")
        if call.legacy_call_id is not None:
            legacy_call = (
                await session.execute(
                    select(Call).where(
                        Call.id == call.legacy_call_id,
                        Call.tenant_id == tenant_id,
                        _resource_scope_clause(Call, environment_id),
                    )
                )
            ).scalar_one_or_none()

    crm_configured, crm_connected = await _tenant_crm_ready(session, tenant_id)
    resource_environment_ids = [agent.environment_id]
    if version is not None:
        resource_environment_ids.append(version.published_environment_id)
    if call is not None:
        resource_environment_ids.append(call.environment_id)
    if legacy_call is not None:
        resource_environment_ids.append(legacy_call.environment_id)
    strict_environment_match = (
        environment_id is not None
        and all(resource_environment_id == environment_id for resource_environment_id in resource_environment_ids)
    )
    if strict_environment_match:
        environment_detail = f"All inspected agent, published-version, and any supplied call records match authenticated environment {environment_id}."
    elif environment_id is not None:
        environment_detail = f"Reads are tenant-scoped and filtered to environment {environment_id}, but one or more accepted legacy environment-neutral records prevent proving a strict environment-only lifecycle."
    else:
        environment_detail = "No environment is selected in the authenticated context; the inspection cannot establish an environment-specific lifecycle."

    current_version_is_valid = bool(
        version is not None
        and str(version.status).lower() == "published"
        and agent.status == "published"
        and _published_version_matches_environment(
            version,
            environment_id=environment_id,
            environment_kind=environment_kind,
        )
    )
    if current_version_is_valid:
        version_detail = (
            f"Agent.published_version_id resolves to tenant/agent-scoped immutable version "
            f"{version.version_number} (status=published)."
        )
        version_step_status = "PASS"
    elif agent.published_version_id is None:
        version_detail = "The agent has no persisted published-version pointer; no latest-version fallback was attempted."
        version_step_status = "NOT_RUN"
    else:
        version_detail = "The persisted published-version pointer is absent, mismatched, superseded, or not in the selected environment."
        version_step_status = "FAIL"

    steps: list[E2EInspectionStep] = [
        E2EInspectionStep(
            key="agent_record",
            label="Tenant-scoped agent record",
            status="PASS",
            detail=f"Agent `{agent.id}` exists in the caller's tenant and selected environment scope.",
        ),
        E2EInspectionStep(
            key="published_version",
            label="Current published immutable version",
            status=version_step_status,
            detail=version_detail,
        ),
        E2EInspectionStep(
            key="environment_boundary",
            label="Environment boundary",
            status="PASS" if strict_environment_match else "PARTIAL",
            detail=environment_detail,
        ),
    ]

    call_status: str | None = None
    is_simulation: bool | None = None
    call_agent_version_number: int | None = None
    call_agent_version_id: UUID | None = None
    if call is None:
        steps.extend(
            [
                E2EInspectionStep(
                    key="telephony_call",
                    label="Persisted telephony call session",
                    status="NOT_RUN",
                    detail="No call_id was supplied. No call was originated and no provider was contacted.",
                ),
                E2EInspectionStep(
                    key="pinned_call_version",
                    label="Call-pinned immutable agent version",
                    status="NOT_RUN",
                    detail="No call was supplied; there is no pinned call snapshot to resolve.",
                ),
            ]
        )
    else:
        call_status = str(call.status)
        is_simulation = bool(call.is_simulation)
        call_agent_id = str(call.agent_id or "")
        agent_matches = call_agent_id in {str(agent.id), str(agent.external_key)}
        raw_call_version_number = call.agent_version_number
        call_agent_version_number = (
            int(raw_call_version_number) if raw_call_version_number is not None else None
        )
        call_metadata = call.metadata_json if isinstance(call.metadata_json, dict) else {}
        has_resolved_version_id = (
            "resolved_agent_version_id" in call_metadata
            and call_metadata["resolved_agent_version_id"] is not None
        )
        raw_version_id = call_metadata.get("resolved_agent_version_id")
        invalid_resolved_version_id = False
        requested_call_version_id: UUID | None = None
        if has_resolved_version_id:
            try:
                requested_call_version_id = UUID(str(raw_version_id))
            except (TypeError, ValueError):
                invalid_resolved_version_id = True

        pinned_version = None
        if (
            agent_matches
            and call_agent_version_number is not None
            and not invalid_resolved_version_id
        ):
            if has_resolved_version_id:
                pinned_version = await session.scalar(
                    select(AgentVersion).where(
                        AgentVersion.id == requested_call_version_id,
                        AgentVersion.tenant_id == tenant_id,
                        AgentVersion.agent_id == agent.id,
                        AgentVersion.version_number == call_agent_version_number,
                    )
                )
            else:
                # Legacy calls may retain a version number without the newer
                # resolved ID. The per-agent unique constraint makes this an
                # exact tenant/agent/version lookup, not a latest-version fallback.
                pinned_version = await session.scalar(
                    select(AgentVersion).where(
                        AgentVersion.tenant_id == tenant_id,
                        AgentVersion.agent_id == agent.id,
                        AgentVersion.version_number == call_agent_version_number,
                    )
                )

        pinned_status = str(pinned_version.status).lower() if pinned_version is not None else ""
        pinned_environment_matches = bool(
            pinned_version is not None
            and _published_version_matches_environment(
                pinned_version,
                environment_id=environment_id,
                environment_kind=environment_kind,
            )
        )
        pinned_version_valid = bool(
            agent_matches
            and call_agent_version_number is not None
            and pinned_version is not None
            and pinned_status in {"published", "superseded"}
            and pinned_environment_matches
        )
        if pinned_version_valid:
            call_agent_version_id = pinned_version.id
            pinned_step_status = "PASS"
            pinned_detail = (
                f"Call is pinned to tenant/agent-scoped immutable version "
                f"{pinned_version.version_number} (status={pinned_status}); the snapshot was not substituted."
            )
        else:
            call_agent_version_id = None
            pinned_step_status = "FAIL" if agent_matches else "NOT_RUN"
            if call_agent_version_number is None:
                pinned_detail = "The persisted call has no agent_version_number; the exact snapshot cannot be proven."
            elif invalid_resolved_version_id:
                pinned_detail = "The persisted resolved_agent_version_id is invalid; no version-number fallback was attempted."
            elif pinned_version is None:
                pinned_detail = "The call's exact version id/number does not resolve to the requested tenant and agent."
            elif not pinned_environment_matches:
                pinned_detail = "The exact call version is not bound to the resolved environment; no alternate version was substituted."
            else:
                pinned_detail = f"Call version status={pinned_status}; only published or superseded immutable snapshots are accepted."

        steps.extend(
            [
                E2EInspectionStep(
                    key="telephony_call",
                    label="Persisted telephony call session",
                    status="PASS" if agent_matches else "FAIL",
                    detail=(
                        f"Call {call.id} belongs to the requested agent; state={call_status}; "
                        f"simulation={is_simulation}; usage_finalized={bool(call.usage_finalized)}."
                        if agent_matches
                        else "The persisted call is not bound to the requested agent."
                    ),
                ),
                E2EInspectionStep(
                    key="pinned_call_version",
                    label="Call-pinned immutable agent version",
                    status=pinned_step_status,
                    detail=pinned_detail,
                ),
            ]
        )

    if not crm_configured:
        crm_step_status = "NOT_CONFIGURED"
        crm_detail = "No tenant CRM/calendar credential bundle is present; no writeback was attempted."
    elif crm_connected and legacy_call is not None and bool(legacy_call.crm_synced):
        crm_step_status = "PASS"
        crm_detail = "A persisted provider health check and legacy call CRM-sync flag are both present."
    elif crm_connected:
        crm_step_status = "PARTIAL"
        crm_detail = "A recent successful connection check exists, but this inspection found no verified CRM writeback for the supplied call."
    else:
        crm_step_status = "PARTIAL"
        crm_detail = "Integration configuration exists, but provider health is not currently verified; no writeback was attempted."
    steps.append(
        E2EInspectionStep(
            key="crm_writeback",
            label="CRM outcome writeback",
            status=crm_step_status,
            detail=crm_detail,
        )
    )

    if legacy_call is not None:
        analytics_status = "PARTIAL"
        analytics_detail = "A legacy call row is linked; analytics aggregation is a separate read path and was not executed here."
        billing_status = "PARTIAL"
        billing_detail = (
            "Telephony usage finalization is persisted for this call, but this inspector does not assert invoice, payment, or ledger reconciliation."
            if bool(call.usage_finalized)
            else "The linked telephony session has not finalized usage; invoice and payment state were not queried."
        )
    elif call is not None:
        analytics_status = "PARTIAL"
        analytics_detail = "The telephony session exists without a linked legacy call row; no analytics record is asserted."
        billing_status = "PARTIAL"
        billing_detail = (
            "Telephony usage finalization is persisted for this call, but this inspector does not assert invoice, payment, or ledger reconciliation."
            if bool(call.usage_finalized)
            else "The telephony session has not finalized usage; invoice and payment state were not queried."
        )
    else:
        analytics_status = "NOT_RUN"
        analytics_detail = "No call was supplied, so there is no call outcome to reconcile with analytics."
        billing_status = "NOT_RUN"
        billing_detail = "No call was supplied, so no call usage or billable amount is asserted."
    steps.extend(
        [
            E2EInspectionStep(
                key="analytics",
                label="Call analytics propagation",
                status=analytics_status,
                detail=analytics_detail,
            ),
            E2EInspectionStep(
                key="usage_billing",
                label="Usage and billing linkage",
                status=billing_status,
                detail=billing_detail,
            ),
        ]
    )

    if any(step.status == "FAIL" for step in steps):
        overall_status = "FAIL"
    elif all(step.status == "PASS" for step in steps):
        overall_status = "PASS"
    else:
        overall_status = "PARTIAL"

    return E2EInspectionResponse(
        tenant_id=tenant_id,
        environment_id=environment_id,
        agent_id=agent.id,
        agent_name=agent.name,
        agent_status=agent.status,
        published_version_number=(
            int(version.version_number) if current_version_is_valid and version is not None else None
        ),
        published_version_id=version.id if current_version_is_valid and version is not None else None,
        call_id=call.id if call is not None else None,
        call_status=call_status,
        call_agent_version_number=call_agent_version_number,
        call_agent_version_id=call_agent_version_id,
        is_simulation=is_simulation,
        overall_status=overall_status,
        steps=steps,
        side_effects_performed=False,
        limitation=(
            "This read-only inspection checks tenant/environment-bound persisted records. It does not dial, "
            "run a workflow, contact CRM, calculate a charge, or substitute for live-provider/E2E verification."
        ),
    )
