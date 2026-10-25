import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.assist.kb_surfacing import GroundedKnowledge
from app.contact_center_v2.assist.service import ingest_transcript, process_turn, start_assist
from app.db.models import User
from tests.contact_center_v2.conftest import Cc2Scope
from tests.contact_center_v2.test_assist_suggestion_engine import canonical_call


async def test_provider_suggestions_are_redacted_before_durable_storage(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User, monkeypatch: pytest.MonkeyPatch,
):
    call = await canonical_call(db, cc2_scope)
    assist, _ = await start_assist(
        db, tenant_id=cc2_scope.tenant.id, call_id=call.id, agent_user_id=agent_a.id
    )
    turn = await ingest_transcript(
        db, call_id=call.id, speaker="caller", text="Please explain the policy", seq=1, is_final=True
    )
    assert turn is not None

    async def grounded(*args, **kwargs):
        return GroundedKnowledge(
            chunks=[], context="GROUNDED EVIDENCE",
            sources=[{"document_id": "doc-1", "chunk_id": "chunk-1", "title": "Policy"}],
        )

    async def provider(*args, **kwargs):
        return SimpleNamespace(
            executed=True,
            text=json.dumps({
                "suggestion": "Reply to raw.customer@example.com for help",
                "next_best_action": "Email raw.customer@example.com after the call",
            }),
            provider="test-provider", model="test-model",
        )

    monkeypatch.setattr(
        "app.contact_center_v2.assist.kb_surfacing.retrieve_grounded_knowledge", grounded
    )
    monkeypatch.setattr("app.ai.runtime.invoke", provider)
    rows = await process_turn(
        db, tenant_id=cc2_scope.tenant.id, turn_id=turn.id, executor=object()
    )
    assert len(rows) == 2
    for row in rows:
        assert "raw.customer@example.com" not in row.text
        assert row.citations[0]["document_id"] == "doc-1"
        assert row.generation_method == "governed_llm"
    assert turn.processing_status == "completed"
    assert assist.status == "active"
