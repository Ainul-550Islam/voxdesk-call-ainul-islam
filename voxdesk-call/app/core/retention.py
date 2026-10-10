"""Data retention and replay-receipt pruning.

Enforces per-tenant, per-agent, per-resource ``RetentionPolicy`` rows alongside
``RecordingPolicy``, honoring ``legal_hold`` across both tables, deleting
expired storage objects and rows, updating ``last_purge_at`` / ``next_purge_at``,
and writing a durable ``AuditLog`` entry per executed policy.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.core.config import settings
from app.core.data_policy import retention_cutoff as cutoff
from app.db.enterprise_models import RetentionPolicy
from app.db.models import (
    AuditAction,
    BillingWebhookReceipt,
    CalendarWebhookReceipt,
    Call,
    CrmWebhookReceipt,
    MessageWebhookReceipt,
    Tenant,
    Turn,
)
from app.db.retell_models import ChatMessage, ChatSession
from app.db.telephony_models import TelephonyCallSession
from app.telephony.recording import CallRecording, mark_deletion, purge_for_calls
from app.telephony.recording_policy import RecordingPolicy
from app.telephony.transcription import CallTranscriptJob

log = structlog.get_logger()

WEBHOOK_RECEIPT_RETENTION_DAYS = 30
RESOURCE_TYPES = ("call", "chat", "recording", "transcript", "pcap", "all")


def _naive_utc(dt: datetime | None = None) -> datetime:
    if dt is None:
        return datetime.utcnow()
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _aware_utc(dt: datetime | None = None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _matching_call_ids_for_agents(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    agent_ids: list[str],
    before_naive: datetime | None = None,
) -> list[uuid.UUID]:
    """Resolve Call IDs belonging to ``agent_ids`` (via TelephonyCallSession)."""
    if not agent_ids:
        return []
    sess_stmt = select(TelephonyCallSession.id, TelephonyCallSession.provider_call_id).where(
        TelephonyCallSession.tenant_id == tenant_id,
        TelephonyCallSession.agent_id.in_(agent_ids),
    )
    if before_naive is not None:
        sess_stmt = sess_stmt.where(TelephonyCallSession.created_at < before_naive)
    session_rows = (await session.execute(sess_stmt)).all()
    session_ids = [r[0] for r in session_rows if r[0]]
    sids = [r[1] for r in session_rows if r[1]]
    if not session_ids and not sids:
        return []
    filters = [Call.tenant_id == tenant_id]
    if before_naive is not None:
        filters.append(Call.started_at < before_naive)
    clauses = []
    if session_ids:
        clauses.append(Call.id.in_(session_ids))
    if sids:
        clauses.append(Call.call_sid.in_(sids))
    filters.append(or_(*clauses))
    return list((await session.scalars(select(Call.id).where(*filters))).all())


async def _execute_resource_purge(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    resource_type: str,
    agent_id: str,
    days: int,
    now_naive: datetime,
    now_aware: datetime,
    dry_run: bool,
    overridden_agents: list[str] | None = None,
) -> dict[str, int]:
    """Delete (or count in dry_run) expired records for one (tenant, agent, resource_type)."""
    before_naive = cutoff(days, now=now_naive)
    before_aware = now_aware - timedelta(days=max(1, int(days)))
    counts = {
        "purged_calls": 0,
        "purged_turns": 0,
        "purged_recordings": 0,
        "purged_transcripts": 0,
        "purged_chats": 0,
        "purged_pcaps": 0,
    }

    target_types = (
        ["recording", "transcript", "pcap", "chat", "call"]
        if resource_type == "all"
        else [resource_type]
    )

    # Resolve call scope if needed
    agent_scoped = bool(agent_id and agent_id.strip())
    if agent_scoped:
        scoped_call_ids = await _matching_call_ids_for_agents(
            session, tenant_id, [agent_id.strip()], before_naive
        )
    else:
        excluded_call_ids = set(
            await _matching_call_ids_for_agents(
                session, tenant_id, list(overridden_agents or []), before_naive=None
            )
        )
        all_expired_ids = list(
            (
                await session.scalars(
                    select(Call.id).where(
                        Call.tenant_id == tenant_id,
                        Call.started_at < before_naive,
                    )
                )
            ).all()
        )
        scoped_call_ids = [cid for cid in all_expired_ids if cid not in excluded_call_ids]

    for rtype in target_types:
        if rtype == "recording":
            rec_stmt = select(CallRecording).where(
                CallRecording.tenant_id == tenant_id,
                CallRecording.state != "deleted",
                or_(
                    CallRecording.created_at < before_naive,
                    CallRecording.call_id.in_(scoped_call_ids) if scoped_call_ids else False,
                ),
            )
            if agent_scoped and not scoped_call_ids:
                rec_rows = []
            else:
                if agent_scoped:
                    rec_stmt = select(CallRecording).where(
                        CallRecording.tenant_id == tenant_id,
                        CallRecording.state != "deleted",
                        CallRecording.call_id.in_(scoped_call_ids),
                    )
                rec_rows = list((await session.scalars(rec_stmt)).all())
            if dry_run:
                counts["purged_recordings"] += len(rec_rows)
            else:
                for rec in rec_rows:
                    outcome = await mark_deletion(session, rec, held=False)
                    if outcome in {"deleted", "applied"}:
                        counts["purged_recordings"] += 1

        elif rtype == "transcript":
            if scoped_call_ids:
                turn_count = int(
                    (
                        await session.scalar(
                            select(func.count(Turn.id)).where(Turn.call_id.in_(scoped_call_ids))
                        )
                    )
                    or 0
                )
                job_rows = list(
                    (
                        await session.scalars(
                            select(CallTranscriptJob).where(
                                CallTranscriptJob.tenant_id == tenant_id,
                                CallTranscriptJob.call_id.in_(scoped_call_ids),
                            )
                        )
                    ).all()
                )
                if dry_run:
                    counts["purged_turns"] += turn_count
                    counts["purged_transcripts"] += turn_count + len(job_rows)
                else:
                    if turn_count:
                        await session.execute(delete(Turn).where(Turn.call_id.in_(scoped_call_ids)))
                    if job_rows:
                        await session.execute(
                            delete(CallTranscriptJob).where(
                                CallTranscriptJob.tenant_id == tenant_id,
                                CallTranscriptJob.call_id.in_(scoped_call_ids),
                            )
                        )
                    counts["purged_turns"] += turn_count
                    counts["purged_transcripts"] += turn_count + len(job_rows)

        elif rtype == "pcap":
            # PCAP artifact table retired in 0062_drop_pcap_artifacts (W-10).
            counts["purged_pcaps"] += 0

        elif rtype == "chat":
            chat_stmt = select(ChatSession.id).where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.created_at < before_aware,
            )
            if agent_scoped:
                try:
                    chat_stmt = chat_stmt.where(ChatSession.chat_agent_id == uuid.UUID(agent_id.strip()))
                except ValueError:
                    chat_stmt = chat_stmt.where(False)
            chat_ids = list((await session.scalars(chat_stmt)).all())
            if chat_ids:
                if dry_run:
                    counts["purged_chats"] += len(chat_ids)
                else:
                    await session.execute(
                        delete(ChatMessage).where(ChatMessage.session_id.in_(chat_ids))
                    )
                    await session.execute(
                        delete(ChatSession).where(ChatSession.id.in_(chat_ids))
                    )
                    counts["purged_chats"] += len(chat_ids)

        elif rtype == "call":
            if scoped_call_ids:
                turn_count = int(
                    (
                        await session.scalar(
                            select(func.count(Turn.id)).where(Turn.call_id.in_(scoped_call_ids))
                        )
                    )
                    or 0
                )
                if dry_run:
                    rec_count = int(
                        (
                            await session.scalar(
                                select(func.count(CallRecording.id)).where(
                                    CallRecording.call_id.in_(scoped_call_ids),
                                    CallRecording.state != "deleted",
                                )
                            )
                        )
                        or 0
                    )
                    counts["purged_calls"] += len(scoped_call_ids)
                    counts["purged_turns"] += turn_count
                    counts["purged_recordings"] = max(counts["purged_recordings"], rec_count)
                else:
                    rec_res = await purge_for_calls(session, scoped_call_ids)
                    counts["purged_recordings"] += int(
                        rec_res.get("deleted", rec_res.get("purged_recordings", 0))
                    )
                    if turn_count:
                        await session.execute(delete(Turn).where(Turn.call_id.in_(scoped_call_ids)))
                        counts["purged_turns"] += turn_count
                    await session.execute(
                        delete(CallTranscriptJob).where(
                            CallTranscriptJob.call_id.in_(scoped_call_ids)
                        )
                    )
                    await session.execute(delete(Call).where(Call.id.in_(scoped_call_ids)))
                    counts["purged_calls"] += len(scoped_call_ids)

    return counts


async def purge_expired_calls(
    session: AsyncSession,
    *,
    days: int | None = None,
    now: datetime | None = None,
    tenant_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    dry_run: bool = False,
    actor_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Enforce ``RetentionPolicy`` and ``RecordingPolicy`` across tenants/agents/resources.

    1. Loads ``RetentionPolicy`` rows (filtered by ``tenant_id`` / ``policy_id`` /
       ``resource_type`` when provided), falling back to ``RecordingPolicy`` and
       ``settings.call_retention_days`` for tenants without explicit rows.
    2. Skips any tenant/agent/resource where ``RetentionPolicy.legal_hold`` or
       ``RecordingPolicy.legal_hold`` is True.
    3. Purges expired records and storage objects for ``call``, ``chat``,
       ``recording``, ``transcript``, ``pcap``, or ``all``.
    4. Updates ``RetentionPolicy.last_purge_at`` and ``next_purge_at`` and emits
       one ``AuditLog`` row per policy executed with the real deleted counts.
    """
    now_naive = _naive_utc(now)
    now_aware = _aware_utc(now)
    default_days = settings.call_retention_days if days is None else int(days)

    summary: dict[str, Any] = {
        "purged_calls": 0,
        "purged_turns": 0,
        "purged_recordings": 0,
        "purged_transcripts": 0,
        "purged_chats": 0,
        "purged_pcaps": 0,
        "skipped_legal_hold": 0,
        "policies_evaluated": 0,
        "dry_run": bool(dry_run),
        "cutoff": cutoff(max(1, default_days), now=now_naive).isoformat() if default_days > 0 else None,
    }
    if days is not None and days <= 0 and policy_id is None and tenant_id is None:
        return summary

    # Tenant-level legal holds on RecordingPolicy
    rec_hold_stmt = select(RecordingPolicy.tenant_id, RecordingPolicy.environment_scope).where(
        RecordingPolicy.legal_hold.is_(True)
    )
    if tenant_id is not None:
        rec_hold_stmt = rec_hold_stmt.where(RecordingPolicy.tenant_id == tenant_id)
    rec_hold_rows = (await session.execute(rec_hold_stmt)).all()
    held_tenants: set[uuid.UUID] = {
        r[0] for r in rec_hold_rows if r[1] in ("all", "", None)
    }
    held_agent_scopes: set[tuple[uuid.UUID, str]] = {
        (r[0], r[1]) for r in rec_hold_rows if r[1] and r[1].startswith("agent:")
    }

    # Load explicit RetentionPolicy rows
    pol_stmt = select(RetentionPolicy)
    if tenant_id is not None:
        pol_stmt = pol_stmt.where(RetentionPolicy.tenant_id == tenant_id)
    if policy_id is not None:
        pol_stmt = pol_stmt.where(RetentionPolicy.id == policy_id)
    if resource_type is not None:
        pol_stmt = pol_stmt.where(RetentionPolicy.resource_type.in_([resource_type, "all"]))
    policies = list((await session.scalars(pol_stmt.order_by(RetentionPolicy.created_at.asc()))).all())

    # Also check tenant-wide legal_hold on RetentionPolicy (agent_id == "" and resource_type == "all")
    tenant_wide_hold_stmt = select(RetentionPolicy.tenant_id).where(
        RetentionPolicy.legal_hold.is_(True),
        RetentionPolicy.agent_id == "",
        RetentionPolicy.resource_type.in_(["all", "call"]),
    )
    if tenant_id is not None:
        tenant_wide_hold_stmt = tenant_wide_hold_stmt.where(RetentionPolicy.tenant_id == tenant_id)
    for held_tid in (await session.scalars(tenant_wide_hold_stmt)).all():
        held_tenants.add(held_tid)

    # Map (tenant_id, resource_type) -> agent_ids that have explicit agent-level RetentionPolicy rows
    all_agent_pols = list(
        (
            await session.scalars(
                select(RetentionPolicy).where(RetentionPolicy.agent_id != "")
                if tenant_id is None
                else select(RetentionPolicy).where(
                    RetentionPolicy.tenant_id == tenant_id,
                    RetentionPolicy.agent_id != "",
                )
            )
        ).all()
    )
    overridden_by_tenant_rtype: dict[tuple[uuid.UUID, str], list[str]] = {}
    for ap in all_agent_pols:
        aid = (ap.agent_id or "").strip()
        if not aid:
            continue
        for rt in (RESOURCE_TYPES if ap.resource_type == "all" else (ap.resource_type,)):
            overridden_by_tenant_rtype.setdefault((ap.tenant_id, rt), []).append(aid)

    covered_tenants: set[uuid.UUID] = set()
    for policy in policies:
        summary["policies_evaluated"] += 1
        if not policy.agent_id and policy.resource_type in ("call", "all"):
            covered_tenants.add(policy.tenant_id)

        agent_scope_key = (
            f"agent:{uuid.UUID(policy.agent_id).hex}"
            if policy.agent_id and len(policy.agent_id) == 36
            else f"agent:{policy.agent_id[:34]}"
        ) if policy.agent_id else "all"

        is_held = (
            bool(policy.legal_hold)
            or policy.tenant_id in held_tenants
            or (policy.tenant_id, agent_scope_key) in held_agent_scopes
        )
        if is_held:
            summary["skipped_legal_hold"] += 1
            continue

        eff_days = int(days) if days is not None else int(policy.retention_days)
        if eff_days <= 0:
            continue
        eff_rtype = resource_type if (resource_type and policy.resource_type == "all") else policy.resource_type

        counts = await _execute_resource_purge(
            session,
            tenant_id=policy.tenant_id,
            resource_type=eff_rtype,
            agent_id=policy.agent_id or "",
            days=eff_days,
            now_naive=now_naive,
            now_aware=now_aware,
            dry_run=dry_run,
            overridden_agents=overridden_by_tenant_rtype.get((policy.tenant_id, eff_rtype), []),
        )
        for key, val in counts.items():
            summary[key] += val

        if not dry_run:
            policy.last_purge_at = now_aware
            policy.next_purge_at = now_aware + timedelta(days=1)
            policy.updated_at = now_aware
            await emit(
                session,
                AuditAction.GDPR_ERASURE,
                tenant_id=policy.tenant_id,
                actor_user_id=actor_user_id,
                detail={
                    "operation": "retention_policy_purge",
                    "policy_id": str(policy.id),
                    "agent_id": policy.agent_id or "",
                    "resource_type": eff_rtype,
                    "retention_days": eff_days,
                    "counts": counts,
                },
                commit=False,
            )

    # Fallback for tenants (or specific tenant_id) with no explicit RetentionPolicy row
    if policy_id is None:
        tenant_stmt = select(Tenant.id)
        if tenant_id is not None:
            tenant_stmt = tenant_stmt.where(Tenant.id == tenant_id)
        all_tenant_ids = list((await session.scalars(tenant_stmt)).all())
        for tid in all_tenant_ids:
            if tid in covered_tenants and resource_type in (None, "call", "all"):
                continue
            if tid in held_tenants:
                summary["skipped_legal_hold"] += 1
                continue
            # Check if any RetentionPolicy for this tenant has legal_hold on the requested resource
            any_hold = await session.scalar(
                select(RetentionPolicy.id)
                .where(
                    RetentionPolicy.tenant_id == tid,
                    RetentionPolicy.legal_hold.is_(True),
                )
                .limit(1)
            )
            if any_hold is not None:
                summary["skipped_legal_hold"] += 1
                continue

            rec_pol = await session.scalar(
                select(RecordingPolicy).where(
                    RecordingPolicy.tenant_id == tid,
                    RecordingPolicy.environment_scope == "all",
                )
            )
            if rec_pol is not None and rec_pol.legal_hold:
                summary["skipped_legal_hold"] += 1
                continue

            eff_days = (
                int(days)
                if days is not None
                else (int(rec_pol.retention_days) if rec_pol is not None else default_days)
            )
            if eff_days <= 0:
                continue
            eff_rtype = resource_type or "call"
            counts = await _execute_resource_purge(
                session,
                tenant_id=tid,
                resource_type=eff_rtype,
                agent_id="",
                days=eff_days,
                now_naive=now_naive,
                now_aware=now_aware,
                dry_run=dry_run,
            )
            any_deleted = sum(counts.values()) > 0
            for key, val in counts.items():
                summary[key] += val
            if not dry_run and (any_deleted or tenant_id is not None):
                await emit(
                    session,
                    AuditAction.GDPR_ERASURE,
                    tenant_id=tid,
                    actor_user_id=actor_user_id,
                    detail={
                        "operation": "retention_default_purge",
                        "resource_type": eff_rtype,
                        "retention_days": eff_days,
                        "counts": counts,
                    },
                    commit=False,
                )

    if not dry_run:
        await session.commit()

    total_purged = (
        summary["purged_calls"]
        + summary["purged_recordings"]
        + summary["purged_transcripts"]
        + summary["purged_chats"]
        + summary["purged_pcaps"]
    )
    if total_purged:
        log.info("retention.purged", **summary)
    return summary


async def prune_webhook_receipts(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, int]:
    """Delete webhook dedup receipts older than their replay horizon."""
    before = cutoff(WEBHOOK_RECEIPT_RETENTION_DAYS, now=_aware_utc(now))
    if before is None:
        return {"crm": 0, "calendar": 0, "billing": 0, "message": 0}

    crm = (
        await session.execute(
            delete(CrmWebhookReceipt).where(CrmWebhookReceipt.received_at < before)
        )
    ).rowcount or 0
    cal = (
        await session.execute(
            delete(CalendarWebhookReceipt).where(
                CalendarWebhookReceipt.received_at < before
            )
        )
    ).rowcount or 0
    bill = (
        await session.execute(
            delete(BillingWebhookReceipt).where(
                BillingWebhookReceipt.received_at < before
            )
        )
    ).rowcount or 0
    msg = (
        await session.execute(
            delete(MessageWebhookReceipt).where(
                MessageWebhookReceipt.received_at < before
            )
        )
    ).rowcount or 0
    await session.commit()

    total = int(crm) + int(cal) + int(bill) + int(msg)
    if total:
        log.info(
            "retention.webhook_receipts_pruned",
            crm=int(crm),
            calendar=int(cal),
            billing=int(bill),
            message=int(msg),
            cutoff=before.isoformat(),
        )
    return {
        "crm": int(crm),
        "calendar": int(cal),
        "billing": int(bill),
        "message": int(msg),
    }
