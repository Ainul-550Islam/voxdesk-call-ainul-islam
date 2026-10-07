"""Scorecard versions.

A version that already has a finalized review cannot have its items rewritten.
A later version is a new row. Finalized calculations keep their own snapshot.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.exceptions import InvalidRubric
from app.qa.models import QAReview, Scorecard, ScorecardItem, ScorecardSection
from app.qa.repository import get_scorecard, items_for, sections_for

_STATUSES = {"active", "inactive", "retired"}


def _name(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or len(cleaned) > 80:
        raise InvalidRubric("Scorecard name is required")
    return cleaned


async def create_scorecard(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    sections: list[dict],
    pass_threshold: int = 7000,
    environment_id: uuid.UUID | None = None,
    description: str = "",
) -> Scorecard:
    label = _name(name)
    if not isinstance(pass_threshold, int) or isinstance(pass_threshold, bool):
        raise InvalidRubric("Pass threshold must be an integer")
    if pass_threshold < 0 or pass_threshold > 10000:
        raise InvalidRubric("Pass threshold is outside 0..10000")
    if not sections:
        raise InvalidRubric("A scorecard needs at least one section")
    current = await session.scalar(
        select(func.max(Scorecard.version)).where(
            Scorecard.tenant_id == tenant_id,
            Scorecard.name == label,
        )
    )
    version = int(current or 0) + 1
    card = Scorecard(
        tenant_id=tenant_id,
        environment_id=environment_id,
        name=label,
        version=version,
        status="active",
        pass_threshold=pass_threshold,
        description=(description or "")[:300],
    )
    session.add(card)
    await session.flush()
    for index, section in enumerate(sections):
        section_name = _name(str(section.get("name") or ""))
        weight = section.get("weight", 1)
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            raise InvalidRubric("Section weight must be a positive integer")
        held = ScorecardSection(
            tenant_id=tenant_id,
            scorecard_id=card.id,
            name=section_name,
            weight=weight,
            position=index,
        )
        session.add(held)
        await session.flush()
        raw_items = section.get("items") or []
        if not raw_items:
            raise InvalidRubric("A section needs at least one item")
        for item_index, item in enumerate(raw_items):
            item_name = _name(str(item.get("name") or ""))
            item_weight = item.get("weight", 1)
            min_score = item.get("min_score", 0)
            max_score = item.get("max_score", 100)
            if not isinstance(item_weight, int) or isinstance(item_weight, bool) or item_weight < 1:
                raise InvalidRubric("Item weight must be a positive integer")
            if not isinstance(min_score, int) or not isinstance(max_score, int) or max_score <= min_score:
                raise InvalidRubric("Item score range is invalid")
            session.add(
                ScorecardItem(
                    tenant_id=tenant_id,
                    scorecard_id=card.id,
                    section_id=held.id,
                    name=item_name,
                    weight=item_weight,
                    min_score=min_score,
                    max_score=max_score,
                    required=bool(item.get("required", False)),
                    allow_na=bool(item.get("allow_na", False)),
                    position=item_index,
                )
            )
    await session.flush()
    return card


async def set_status(
    session: AsyncSession, card: Scorecard, status: str
) -> Scorecard:
    if status not in _STATUSES:
        raise InvalidRubric("Unknown scorecard status")
    card.status = status
    await session.flush()
    return card


async def assert_mutable(session: AsyncSession, card: Scorecard) -> None:
    finalized = await session.scalar(
        select(func.count()).select_from(QAReview).where(
            QAReview.scorecard_id == card.id,
            QAReview.status == "finalized",
        )
    )
    if int(finalized or 0) > 0:
        raise InvalidRubric("A finalized review pins this scorecard version")


async def load_tree(session: AsyncSession, tenant_id: uuid.UUID, scorecard_id: uuid.UUID):
    card = await get_scorecard(session, tenant_id, scorecard_id)
    return card, await sections_for(session, tenant_id, card.id), await items_for(session, tenant_id, card.id)
