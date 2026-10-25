from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.assist.compliance_prompts import evaluate_adherence, evaluate_text
from app.contact_center_v2.assist.service import ingest_transcript, start_assist
from app.contact_center_v2.assist.compliance_prompts import upsert_script
from app.db.contact_center_v2_models import AssistAlert, ComplianceAdherence
from app.db.models import User
from tests.contact_center_v2.test_assist_suggestion_engine import canonical_call


def test_compliance_evaluation_normalizes_text_and_prioritizes_violations():
    script = SimpleNamespace(
        required_phrases=["This call is recorded", "disclosure"],
        forbidden_phrases=["guaranteed approval"],
    )
    met = evaluate_text(script, "This   call is recorded. Disclosure complete.")
    assert met["status"] == "met"
    assert met["missed_phrases"] == []
    missed = evaluate_text(script, "This call is recorded")
    assert missed["status"] == "missed"
    assert missed["missed_phrases"] == ["disclosure"]
    violated = evaluate_text(script, "Disclosure: guaranteed approval")
    assert violated["status"] == "violated"
    assert violated["violated_phrases"] == ["guaranteed approval"]


async def test_adherence_is_persistent_idempotent_and_alerts_once_on_violation(
    db: AsyncSession, cc2_scope, agent_a: User,
):
    call = await canonical_call(db, cc2_scope)
    await start_assist(
        db, tenant_id=cc2_scope.tenant.id, call_id=call.id, agent_user_id=agent_a.id
    )
    script = await upsert_script(
        db,
        tenant_id=cc2_scope.tenant.id,
        environment_id=cc2_scope.environment.id,
        created_by=agent_a.id,
        name="Collections disclosure",
        script_text="State the disclosure and never promise approval.",
        required_phrases=["disclosure"],
        forbidden_phrases=["guaranteed approval"],
    )
    turn = await ingest_transcript(
        db,
        call_id=call.id,
        speaker="agent",
        text="Here is the disclosure, but this is guaranteed approval. Email person@example.com",
        seq=1,
        is_final=True,
    )
    assert turn is not None
    first = await evaluate_adherence(
        db,
        tenant_id=cc2_scope.tenant.id,
        call_id=call.id,
        script_id=script.id,
        agent_user_id=agent_a.id,
    )
    replay = await evaluate_adherence(
        db,
        tenant_id=cc2_scope.tenant.id,
        call_id=call.id,
        script_id=script.id,
        agent_user_id=agent_a.id,
    )
    assert replay.id == first.id
    assert first.status == "violated"
    assert first.matched_phrases == ["disclosure"]
    assert "person@example.com" not in first.evidence_redacted
    assert await db.scalar(select(func.count()).select_from(ComplianceAdherence)) == 1
    alerts = (await db.execute(select(AssistAlert))).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].kind == "compliance_review"
    assert alerts[0].turn_id == turn.id
    assert alerts[0].severity == "critical"
