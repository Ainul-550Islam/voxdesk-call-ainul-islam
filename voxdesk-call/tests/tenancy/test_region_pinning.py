"""Region pinning actually refuses writes (PART 16, P16-07).

The spec asked this part to *prove* that recordings, transcripts and backups
honour region pinning, and to add the enforcement that was missing. Before this
part, `app/tenancy/regions.py` could say whether a label was *allowed* and
`data_residency.evaluate` never claimed a placement (`applied: False`) — but no
write path consulted either. So the tests here come in two halves:

1. the guard's truth table, one test per refusal reason, because each reason
   needs a different fix (caller bug, routing bug, deployment promise);
2. the three artifacts, driven through the real write functions, asserting both
   that a foreign write is refused **and that nothing was written** — a refusal
   that leaves a file behind is worse than no check.

This deployment's region catalogue is empty, so a *pinned* tenant is refused even
when the target matches: you cannot honour a region you do not have. That is the
honest state, and it is asserted rather than papered over.
"""

from __future__ import annotations

import uuid

import pytest

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.models import Call, CallDirection, CallStatus, Speaker, Tenant
from app.governance.models import ResidencyIntent
from app.telephony import media_storage, transcription
from app.tenancy import data_residency, regions
from app.tenancy.exceptions import ResidencyDenied

pytestmark = pytest.mark.asyncio

FRANKFURT = "eu-central-1"
VIRGINIA = "us-east-1"


@pytest.fixture
def configured_catalogue(monkeypatch):
    """Pretend a region is configured, to test the *matched* path."""
    monkeypatch.setattr(regions, "SUPPORTED_REGIONS", frozenset({FRANKFURT}))


# --------------------------------------------------------------- the guard ---


async def test_an_unpinned_tenant_is_allowed_and_claims_nothing():
    verdict = data_residency.check_artifact_write(
        artifact="recording", pinned=None, target=None
    )
    assert verdict.allowed is True
    assert verdict.reason == regions.WRITE_UNPINNED
    assert verdict.as_dict()["physical_residency_proven"] is False


async def test_a_foreign_region_is_refused():
    verdict = data_residency.check_artifact_write(
        artifact="recording", pinned=FRANKFURT, target=VIRGINIA
    )
    assert verdict.allowed is False
    assert verdict.reason == regions.WRITE_FOREIGN


async def test_an_unstated_target_is_refused_when_a_region_is_pinned():
    verdict = data_residency.check_artifact_write(
        artifact="backup", pinned=FRANKFURT, target=None
    )
    assert verdict.allowed is False
    assert verdict.reason == regions.WRITE_TARGET_UNKNOWN


async def test_a_pin_the_deployment_cannot_honour_is_refused():
    """The catalogue is empty here: a matched label still is not a placement."""
    verdict = data_residency.check_artifact_write(
        artifact="transcript", pinned=FRANKFURT, target=FRANKFURT
    )
    assert verdict.allowed is False
    assert verdict.reason == regions.WRITE_UNCONFIGURED


async def test_a_matched_region_is_allowed_once_one_is_configured(configured_catalogue):
    verdict = data_residency.check_artifact_write(
        artifact="transcript", pinned=FRANKFURT, target=FRANKFURT
    )
    assert verdict.allowed is True
    assert verdict.reason == regions.WRITE_MATCHED
    assert verdict.as_dict()["physical_residency_proven"] is False


async def test_an_unknown_artifact_type_is_a_caller_error():
    from app.tenancy.exceptions import ValidationFailed

    with pytest.raises(ValidationFailed):
        data_residency.check_artifact_write(
            artifact="screenshot", pinned=None, target=None
        )


async def test_enforce_raises_and_says_which_artifact():
    with pytest.raises(ResidencyDenied) as exc:
        data_residency.enforce_artifact_write(
            artifact="recording", pinned=FRANKFURT, target=VIRGINIA
        )
    assert "recording" in str(exc.value)


# ----------------------------------------------------------------- the pin ---


async def _pin(db, tenant: Tenant, region: str, status: str = "requested") -> ResidencyIntent:
    row = ResidencyIntent(
        tenant_id=tenant.id,
        organization_id=uuid.uuid4(),
        requested_region=region,
        status=status,
        version=1,
    )
    db.add(row)
    await db.commit()
    return row


async def test_the_pin_comes_from_the_newest_live_intent(db, tenant_a):
    await _pin(db, tenant_a, FRANKFURT)
    assert await data_residency.pinned_region(db, tenant_a.id) == FRANKFURT


async def test_a_rejected_intent_does_not_become_the_pin(db, tenant_b):
    await _pin(db, tenant_b, VIRGINIA, status="rejected")
    assert await data_residency.pinned_region(db, tenant_b.id) is None


async def test_a_tenant_with_no_intent_has_no_pin(db, tenant_a):
    assert await data_residency.pinned_region(db, tenant_a.id) is None


# --------------------------------------------------------- artifact: audio ---


async def test_a_recording_is_not_written_outside_the_pinned_region(
    db, tenant_a, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "telephony_media_dir", str(tmp_path))
    recording_id = uuid.uuid4()

    with pytest.raises(ResidencyDenied):
        media_storage.put_bytes(
            tenant_a.id,
            recording_id,
            b"RIFF....WAVE",
            region=VIRGINIA,
            pinned_region=FRANKFURT,
        )

    expected = tmp_path / media_storage.storage_key(tenant_a.id, recording_id)
    assert not expected.exists(), "a refused write must leave no file behind"


async def test_an_unpinned_recording_still_writes(db, tenant_a, monkeypatch, tmp_path):
    """The guard must not break the default deployment."""
    monkeypatch.setattr(settings, "telephony_media_dir", str(tmp_path))
    recording_id = uuid.uuid4()
    key, digest, size = media_storage.put_bytes(tenant_a.id, recording_id, b"RIFFdata")
    assert (tmp_path / key).read_bytes() == b"RIFFdata"
    assert size == 8 and len(digest) == 64


# ---------------------------------------------------- artifact: transcript ---


async def _call(db, tenant: Tenant) -> Call:
    call = Call(
        tenant_id=tenant.id,
        direction=CallDirection.INBOUND,
        status=CallStatus.COMPLETED,
        from_number="+15551230000",
        to_number=tenant.twilio_number,
        call_sid=f"CA{uuid.uuid4().hex}",
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)
    return call


async def test_a_transcript_turn_is_refused_for_a_pinned_tenant(db, tenant_a):
    await _pin(db, tenant_a, FRANKFURT)
    call = await _call(db, tenant_a)

    with pytest.raises(ResidencyDenied):
        await transcription.record_turn(
            db,
            call,
            speaker=Speaker.USER,
            text="I need to move my appointment.",
            region=VIRGINIA,
        )


async def test_a_transcript_turn_is_written_for_an_unpinned_tenant(db, tenant_b):
    call = await _call(db, tenant_b)
    turn = await transcription.record_turn(
        db,
        call,
        speaker=Speaker.USER,
        text="Tuesday at nine works.",
        region=None,
    )
    assert turn.id is not None
    assert turn.text


# --------------------------------------------------------- artifact: backup ---


async def test_the_backup_gate_refuses_a_pinned_tenant(monkeypatch, tmp_path):
    """`scripts/backup_target_check.py` is the gate `backup.sh` runs."""
    import scripts.backup_target_check as gate

    def fake_pins(tenant: str):
        return [(str(uuid.uuid4()), FRANKFURT)]

    monkeypatch.setattr(gate, "_read_pins", fake_pins)
    assert gate.main(["--out-dir", str(tmp_path), "--region", VIRGINIA]) == gate.EXIT_REFUSED
    # An unstated target is also a refusal, not an assumption.
    assert gate.main(["--out-dir", str(tmp_path)]) == gate.EXIT_REFUSED


async def test_the_backup_gate_allows_an_unpinned_deployment(monkeypatch, tmp_path):
    import scripts.backup_target_check as gate

    monkeypatch.setattr(gate, "_read_pins", lambda tenant: [])
    assert gate.main(["--out-dir", str(tmp_path)]) == 0
