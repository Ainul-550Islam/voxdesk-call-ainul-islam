"""Deterministic, redacted compliance-script adherence checks."""
from __future__ import annotations

import re
import uuid
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import NotFound, PermissionDenied, Validation
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import (
    AssistAlert,
    AssistSession,
    AssistTurn,
    ComplianceAdherence,
    ComplianceScript,
)
from app.db.models import Call


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def evaluate_text(script: ComplianceScript, text: str) -> dict[str, object]:
    normalized = _normal(text)
    required = [str(item).strip() for item in (script.required_phrases or []) if str(item).strip()]
    forbidden = [str(item).strip() for item in (script.forbidden_phrases or []) if str(item).strip()]
    matched = [phrase for phrase in required if _normal(phrase) in normalized]
    missed = [phrase for phrase in required if _normal(phrase) not in normalized]
    violated = [phrase for phrase in forbidden if _normal(phrase) in normalized]
    status = "violated" if violated else "met" if not missed else "missed"
    return {"status": status, "matched_phrases": matched, "missed_phrases": missed, "violated_phrases": violated}


def _phrase_list(values: Iterable[str], *, maximum: int = 50) -> list[str]:
    result = [str(value).strip() for value in values if str(value).strip()]
    if len(result) > maximum or any(len(value) > 200 for value in result):
        raise Validation("Compliance phrase list is too large")
    return result


async def upsert_script(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    script_text: str,
    required_phrases: list[str],
    forbidden_phrases: list[str] | None = None,
    jurisdiction: str = "US",
    version: int | None = None,
) -> ComplianceScript:
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 100 or not script_text.strip() or len(script_text) > 20_000:
        raise Validation("Compliance script name and bounded text are required")
    if version is None:
        version = int(
            await session.scalar(
                select(func.max(ComplianceScript.version)).where(
                    ComplianceScript.tenant_id == tenant_id,
                    ComplianceScript.environment_id == environment_id,
                    ComplianceScript.name == clean_name,
                )
            ) or 0
        ) + 1
    if version < 1:
        raise Validation("Compliance script version must be positive")
    previous = (
        await session.execute(
            select(ComplianceScript).where(
                ComplianceScript.tenant_id == tenant_id,
                ComplianceScript.environment_id == environment_id,
                ComplianceScript.name == clean_name,
                ComplianceScript.active.is_(True),
            )
        )
    ).scalars().all()
    for old in previous:
        old.active = False
        old.updated_at = utcnow()
    row = ComplianceScript(
        tenant_id=tenant_id,
        environment_id=environment_id,
        name=clean_name,
        jurisdiction=jurisdiction.strip().upper()[:32],
        channel="voice",
        script_text=script_text.strip(),
        required_phrases=_phrase_list(required_phrases),
        forbidden_phrases=_phrase_list(forbidden_phrases or []),
        active=True,
        version=version,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def list_compliance_scripts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    active_only: bool = True,
) -> list[ComplianceScript]:
    stmt = select(ComplianceScript).where(ComplianceScript.tenant_id == tenant_id)
    if environment_id is not None:
        stmt = stmt.where(ComplianceScript.environment_id == environment_id)
    if active_only:
        stmt = stmt.where(ComplianceScript.active.is_(True))
    return list(
        (await session.execute(stmt.order_by(ComplianceScript.name, ComplianceScript.version.desc())))
        .scalars().all()
    )


async def update_compliance_script(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    script_id: uuid.UUID,
    payload: dict[str, object],
) -> tuple[ComplianceScript, bool]:
    current = await session.get(ComplianceScript, script_id)
    if current is None or current.tenant_id != tenant_id:
        raise NotFound("compliance script")
    active = payload.get("active", True) is not False
    values = {
        "script_text": payload.get("script_text", current.script_text),
        "required_phrases": payload.get("required_phrases", list(current.required_phrases or [])),
        "forbidden_phrases": payload.get("forbidden_phrases", list(current.forbidden_phrases or [])),
        "jurisdiction": payload.get("jurisdiction", current.jurisdiction),
    }
    if not active:
        if current.active:
            current.active = False
            current.updated_at = utcnow()
            await session.flush()
            return current, True
        return current, False
    unchanged = (
        current.active
        and values["script_text"] == current.script_text
        and list(values["required_phrases"]) == list(current.required_phrases or [])
        and list(values["forbidden_phrases"]) == list(current.forbidden_phrases or [])
        and values["jurisdiction"] == current.jurisdiction
    )
    if unchanged:
        return current, False
    row = await upsert_script(
        session,
        tenant_id=tenant_id,
        environment_id=current.environment_id,
        created_by=current.created_by,
        name=current.name,
        **values,
    )
    return row, True


async def evaluate_adherence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    script_id: uuid.UUID,
    agent_user_id: uuid.UUID,
) -> ComplianceAdherence:
    call = await session.get(Call, call_id)
    script = await session.get(ComplianceScript, script_id)
    if call is None or call.tenant_id != tenant_id or script is None or script.tenant_id != tenant_id:
        raise NotFound("compliance scope")
    if call.environment_id != script.environment_id:
        raise PermissionDenied("Compliance script belongs to another environment")
    turns = (
        (
            await session.execute(
                select(AssistTurn).where(
                    AssistTurn.tenant_id == tenant_id,
                    AssistTurn.call_id == call_id,
                ).order_by(AssistTurn.created_at, AssistTurn.id)
            )
        )
        .scalars()
        .all()
    )
    # Turns have already passed through the PII redactor at ingestion. Keep a
    # bounded evidence excerpt rather than copying a whole call transcript.
    evidence = "\n".join(turn.text_redacted for turn in turns[-20:])[:8000]
    result = evaluate_text(script, evidence)
    row = (
        await session.execute(
            select(ComplianceAdherence).where(
                ComplianceAdherence.tenant_id == tenant_id,
                ComplianceAdherence.call_id == call_id,
                ComplianceAdherence.script_id == script_id,
            )
        )
    ).scalar_one_or_none()
    moment = utcnow()
    if row is None:
        row = ComplianceAdherence(
            tenant_id=tenant_id,
            environment_id=call.environment_id,
            call_id=call_id,
            script_id=script_id,
            agent_user_id=agent_user_id,
            created_at=moment,
        )
        session.add(row)
    row.agent_user_id = agent_user_id
    row.status = str(result["status"])
    row.matched_phrases = result["matched_phrases"]
    row.missed_phrases = result["missed_phrases"]
    row.violated_phrases = result["violated_phrases"]
    row.evidence_redacted = evidence
    row.updated_at = moment
    await session.flush()
    if row.status == "violated" and turns:
        turn = turns[-1]
        existing_alert = (
            await session.execute(
                select(AssistAlert).where(
                    AssistAlert.tenant_id == tenant_id,
                    AssistAlert.turn_id == turn.id,
                    AssistAlert.kind == "compliance_review",
                )
            )
        ).scalar_one_or_none()
        if existing_alert is None:
            session.add(AssistAlert(
                tenant_id=tenant_id,
                environment_id=call.environment_id,
                call_id=call_id,
                turn_id=turn.id,
                kind="compliance_review",
                severity="critical",
                message="A configured forbidden compliance phrase was detected; supervisor review is required.",
                evidence={"script_id": str(script_id), "adherence_id": str(row.id)},
                created_at=moment,
            ))
            await session.flush()
    return row


async def evaluate_active_scripts(
    session: AsyncSession,
    *,
    assist: AssistSession,
) -> list[ComplianceAdherence]:
    scripts = (
        (
            await session.execute(
                select(ComplianceScript).where(
                    ComplianceScript.tenant_id == assist.tenant_id,
                    ComplianceScript.environment_id == assist.environment_id,
                    ComplianceScript.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        await evaluate_adherence(
            session,
            tenant_id=assist.tenant_id,
            call_id=assist.call_id,
            script_id=script.id,
            agent_user_id=assist.agent_user_id,
        )
        for script in scripts
    ]


async def adherence_for_call(
    session: AsyncSession, *, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> list[ComplianceAdherence]:
    return list(
        (
            await session.execute(
                select(ComplianceAdherence).where(
                    ComplianceAdherence.tenant_id == tenant_id,
                    ComplianceAdherence.call_id == call_id,
                )
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "adherence_for_call",
    "evaluate_active_scripts",
    "evaluate_adherence",
    "evaluate_text",
    "list_compliance_scripts",
    "update_compliance_script",
    "upsert_script",
]
