import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.assist import (
    _parse_suggestions,
    ingest_transcript,
    list_compliance_scripts,
    process_turn,
    start_assist,
    update_compliance_script,
    upsert_script,
)
from app.contact_center_v2.errors import Conflict, Validation
from app.db.contact_center_v2_models import AssistAlert, AssistSuggestion
from app.db.models import Call, User
from tests.contact_center_v2.conftest import Cc2Scope


async def canonical_call(db: AsyncSession, scope: Cc2Scope) -> Call:
    call = Call(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        call_sid=f"CA{uuid.uuid4().hex[:30]}",
        from_number="+15550001000",
        to_number="+15550004000",
    )
    db.add(call)
    await db.commit()
    return call


async def test_assist_ingests_only_final_redacted_turns_and_replays_sequence(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User,
):
    call = await canonical_call(db, cc2_scope)
    assist, created = await start_assist(
        db, tenant_id=cc2_scope.tenant.id, call_id=call.id, agent_user_id=agent_a.id,
    )
    replay, replay_created = await start_assist(
        db, tenant_id=cc2_scope.tenant.id, call_id=call.id, agent_user_id=agent_a.id,
    )
    assert created and not replay_created and replay.id == assist.id
    assert await ingest_transcript(
        db, call_id=call.id, speaker="caller", text="terrible awful partial", seq=None, is_final=False,
    ) is None
    turn = await ingest_transcript(
        db,
        call_id=call.id,
        speaker="caller",
        text="This is terrible and awful. Email me at person@example.com",
        seq=1,
        is_final=True,
    )
    assert turn is not None
    assert "person@example.com" not in turn.text_redacted
    assert turn.sentiment_method == "lexical_proxy_v1"
    assert turn.processing_status == "queued"
    duplicate = await ingest_transcript(
        db,
        call_id=call.id,
        speaker="caller",
        text="This is terrible and awful. Email me at person@example.com",
        seq=1,
        is_final=True,
    )
    assert duplicate.id == turn.id
    with pytest.raises(Conflict):
        await ingest_transcript(
            db, call_id=call.id, speaker="caller", text="Different final text", seq=1, is_final=True,
        )
    alerts = (await db.execute(select(AssistAlert))).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].evidence["method"] == "lexical_proxy_v1"


async def test_no_retrieved_evidence_means_no_generated_suggestion(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User, monkeypatch: pytest.MonkeyPatch,
):
    call = await canonical_call(db, cc2_scope)
    await start_assist(
        db, tenant_id=cc2_scope.tenant.id, call_id=call.id, agent_user_id=agent_a.id,
    )
    turn = await ingest_transcript(
        db, call_id=call.id, speaker="caller", text="What is the refund policy?", seq=1, is_final=True,
    )

    async def no_chunks(*args, **kwargs):
        return []

    monkeypatch.setattr("app.knowledge.retrieval.retrieve_with_timeout", no_chunks)
    rows = await process_turn(db, tenant_id=cc2_scope.tenant.id, turn_id=turn.id)
    assert rows == []
    assert turn.processing_status == "no_evidence"
    assert not (await db.execute(select(AssistSuggestion))).scalars().all()


def test_assist_provider_json_contract_is_strict():
    assert _parse_suggestions('{"suggestion":"Use the cited policy","next_best_action":"Confirm the order"}') == (
        "Use the cited policy", "Confirm the order",
    )
    for payload in (
        "not-json",
        '{"suggestion":"missing action"}',
        '{"suggestion":"","next_best_action":"x"}',
        '{"suggestion":"x","next_best_action":"y","extra":true}',
    ):
        with pytest.raises(Validation):
            _parse_suggestions(payload)


async def test_compliance_script_updates_are_versioned_and_idempotent(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User,
):
    first = await upsert_script(
        db, tenant_id=cc2_scope.tenant.id, environment_id=cc2_scope.environment.id,
        created_by=agent_a.id, name="Required disclosure", script_text="State the disclosure",
        required_phrases=["disclosure"], forbidden_phrases=["guarantee"], jurisdiction="US",
    )
    same, changed = await update_compliance_script(
        db, tenant_id=cc2_scope.tenant.id, script_id=first.id,
        payload={"script_text": "State the disclosure", "required_phrases": ["disclosure"], "forbidden_phrases": ["guarantee"], "jurisdiction": "US"},
    )
    assert same.id == first.id and not changed
    second, changed = await update_compliance_script(
        db, tenant_id=cc2_scope.tenant.id, script_id=first.id,
        payload={"script_text": "State the revised disclosure"},
    )
    assert changed and second.id != first.id and second.version == 2
    assert first.active is False and second.active is True
    active = await list_compliance_scripts(db, tenant_id=cc2_scope.tenant.id)
    assert [row.id for row in active] == [second.id]
    disabled, changed = await update_compliance_script(
        db, tenant_id=cc2_scope.tenant.id, script_id=second.id, payload={"active": False},
    )
    assert changed and disabled.active is False
    disabled_again, changed = await update_compliance_script(
        db, tenant_id=cc2_scope.tenant.id, script_id=second.id, payload={"active": False},
    )
    assert disabled_again.id == second.id and not changed
    assert await list_compliance_scripts(db, tenant_id=cc2_scope.tenant.id) == []


async def test_newer_turn_cancels_publication_of_stale_suggestions(
    db: AsyncSession, cc2_scope: Cc2Scope, agent_a: User, monkeypatch: pytest.MonkeyPatch,
):
    call = await canonical_call(db, cc2_scope)
    await start_assist(
        db, tenant_id=cc2_scope.tenant.id, call_id=call.id, agent_user_id=agent_a.id
    )
    turn = await ingest_transcript(
        db, call_id=call.id, speaker="caller", text="Explain the old policy", seq=1, is_final=True
    )
    assert turn is not None

    async def grounded(*args, **kwargs):
        return SimpleNamespace(
            context="GROUNDED POLICY",
            sources=[{"document_id": "doc-1", "chunk_id": "chunk-1", "title": "Policy"}],
        )

    async def provider(session, *args, **kwargs):
        # A committed newer turn in production wakes the watcher. The final
        # locked check also covers this same-transaction race deterministically.
        await ingest_transcript(
            session, call_id=call.id, speaker="caller", text="Explain the new policy", seq=2, is_final=True
        )
        return SimpleNamespace(
            executed=True,
            text=json.dumps({"suggestion": "Old answer", "next_best_action": "Old action"}),
            provider="test-provider",
            model="test-model",
        )

    monkeypatch.setattr(
        "app.contact_center_v2.assist.kb_surfacing.retrieve_grounded_knowledge", grounded
    )
    monkeypatch.setattr("app.ai.runtime.invoke", provider)
    rows = await process_turn(db, tenant_id=cc2_scope.tenant.id, turn_id=turn.id, executor=object())
    assert rows == []
    assert turn.processing_status == "failed"
    assert turn.failure_category == "superseded_by_new_turn"
    assert not (await db.execute(select(AssistSuggestion))).scalars().all()
