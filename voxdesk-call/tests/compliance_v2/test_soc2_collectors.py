"""Evidence automation: collectors, statuses and the persisted snapshot.

The behaviour that matters is honesty. A source this deployment does not have
(no GitHub token, no DR drill record, no vulnerability feed) must come back
``NOT_CONFIGURED`` — never ``PASS`` — and a source that contradicts a control
must come back ``FAIL``. The runner writes one immutable snapshot per run, with
a digest over the document it wrote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.compliance_v2.soc2 import control_map as control_map_module
from app.compliance_v2.soc2 import runner
from app.compliance_v2.soc2.collectors_base import (
    EVIDENCE_FAIL,
    EVIDENCE_NOT_CONFIGURED,
    EVIDENCE_PASS,
    STATUSES,
    CollectorResult,
    EvidenceCollector,
    EvidenceItem,
)
from app.db.compliance_v2_models import ControlStatus, EvidenceSnapshot


async def _run(db, settings, evidence_root):
    """One evidence run with the real collectors, rooted at ``evidence_root``.

    The control matrix is read from the repository documentation (default
    root); only the evidence sources are redirected into the temporary tree, so
    the run exercises both the real mapping and the "no files here" path.
    """
    return await runner.run_evidence(
        db,
        settings=settings,
        collectors=runner.build_collectors(root=evidence_root),
    )


class _SilentCollector(EvidenceCollector):
    name = "access"
    control_ids = ("CC6.1", "CC6.2", "CC6.3")

    async def collect(self, session) -> CollectorResult:
        return CollectorResult(
            collector=self.name, control_ids=self.control_ids, configured=True, items=[]
        )


def test_control_map_covers_seventeen_controls():
    mapping = control_map_module.load_control_map()

    assert len(mapping.controls) == 17
    assert mapping.unmapped == ("CC9.2",)
    assert set(mapping.controls) == {
        "CC6.1", "CC6.2", "CC6.3", "CC6.6", "CC6.7",
        "CC7.1", "CC7.2", "CC7.3",
        "A1.1", "A1.2", "C1.1", "C1.2", "PI1.1", "PI1.4",
        "P1.1", "P3.1", "P4.2",
    }
    assert {owner for owner in mapping.controls.values()} == {
        "access", "change", "ops", "incident",
    }


def test_every_control_names_one_collector():
    mapping = control_map_module.load_control_map()
    for control in mapping.ids:
        assert mapping.collector_for(control) in {"access", "change", "ops", "incident"}


def test_control_map_document_is_self_describing():
    document = control_map_module.load_control_map().as_dict()

    assert len(document["controls"]) == 17
    assert document["unmapped"] == ["CC9.2"]
    assert document["document_present"] is True
    assert set(document["collectors"]) == {"access", "change", "ops", "incident"}


async def test_evidence_run_marks_missing_sources_not_configured(
    db, compliance_env, compliance_settings
):
    run = await _run(db, compliance_settings, compliance_env)
    await db.commit()

    assert set(run.statuses) == set(control_map_module.load_control_map().controls)
    assert set(run.statuses.values()) <= set(STATUSES)
    # No GitHub token and no DR drill record in this deployment.
    assert run.statuses["CC7.1"] == EVIDENCE_NOT_CONFIGURED
    assert run.statuses["A1.2"] == EVIDENCE_NOT_CONFIGURED
    assert EVIDENCE_PASS not in {run.statuses["CC7.1"], run.statuses["A1.2"]}


async def test_evidence_run_persists_a_snapshot_and_its_control_rows(
    db, compliance_env, compliance_settings
):
    run = await _run(db, compliance_settings, compliance_env)
    await db.commit()

    snapshot = await db.scalar(
        select(EvidenceSnapshot).where(
            EvidenceSnapshot.run_key.endswith(run.run_id[:8])
        )
    )
    assert snapshot is not None
    assert len(snapshot.sha256) == 64
    written = Path(snapshot.file_path)
    assert written.is_file()
    document = json.loads(written.read_text())
    assert document["run_id"] == run.run_id
    assert document["counts"] == run.document["counts"]

    rows = list(
        (
            await db.execute(
                select(ControlStatus).where(ControlStatus.snapshot_id == snapshot.id)
            )
        ).scalars()
    )
    assert len(rows) == 17
    assert {row.control_id for row in rows} == set(run.statuses)
    assert all(row.status in STATUSES for row in rows)


async def test_a_contradicted_control_is_reported_as_fail(db, compliance_env, compliance_settings):
    class _Failing(EvidenceCollector):
        name = "access"
        control_ids = ("CC6.1", "CC6.2", "CC6.3")

        async def collect(self, session) -> CollectorResult:
            return CollectorResult(
                collector=self.name,
                control_ids=self.control_ids,
                configured=True,
                items=[
                    EvidenceItem(
                        control_id="CC6.1",
                        collector=self.name,
                        source="users",
                        payload={"admins_without_mfa": 2},
                    )
                ],
                findings=["two admin accounts have no second factor"],
            )

    class _Empty(EvidenceCollector):
        name = "change"
        control_ids = ("CC7.1", "CC7.2")

        async def collect(self, session) -> CollectorResult:
            return CollectorResult(
                collector=self.name,
                control_ids=self.control_ids,
                configured=False,
                not_configured_reason="no repository configured",
            )

    collectors = {
        "access": _Failing(),
        "change": _Empty(),
        "ops": _Empty(),
        "incident": _Empty(),
    }
    run = await runner.run_evidence(db, settings=compliance_settings, collectors=collectors)
    await db.commit()

    assert run.statuses["CC6.1"] == EVIDENCE_FAIL
    assert run.statuses["CC7.1"] == EVIDENCE_NOT_CONFIGURED
    assert run.document["counts"][EVIDENCE_FAIL] >= 1
    access_result = next(item for item in run.results if item.collector == "access")
    assert access_result.findings == ["two admin accounts have no second factor"]


async def test_a_missing_control_matrix_is_reported_not_guessed(
    db, compliance_env, compliance_settings
):
    run = await runner.run_evidence(db, settings=compliance_settings, root=compliance_env)
    await db.commit()

    assert run.control_map.document_present is False
    assert run.control_map.controls == {}
    assert run.statuses == {}
    assert run.document["counts"]["PASS"] == 0


async def test_a_missing_collector_becomes_an_error_not_a_pass(
    db, compliance_env, compliance_settings
):
    run = await runner.run_evidence(
        db, settings=compliance_settings, collectors={"access": _SilentCollector()}
    )
    await db.commit()

    assert run.statuses["CC7.1"] != EVIDENCE_PASS
    assert run.document["counts"][EVIDENCE_NOT_CONFIGURED] >= 1


def test_each_evidence_item_carries_its_own_digest():
    item = EvidenceItem(
        control_id="CC6.1",
        collector="access",
        source="users",
        payload={"active_users": 3},
    )
    assert len(item.sha256) == 64
    assert item.as_dict()["sha256"] == item.sha256


@pytest.mark.parametrize("status", STATUSES)
def test_status_vocabulary_is_closed(status: str):
    assert status in {"PASS", "FAIL", "NOT_CONFIGURED", "ERROR"}
