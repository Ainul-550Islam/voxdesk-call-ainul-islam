"""Campaign environment migration ↔ release gate compatibility (Batch 06).

The release machinery (P0-1, closed) must see the Batch 06 migration as the
current chain head: ``facts.migration_heads`` reports exactly
``0026_campaign_environment_scope``, the frozen-vs-live comparison the gate
performs shows no drift when both sides carry that head, the migration file
itself declares the correct revision chain, and no stale head expectation
(0011/0019/0024/0025 as *head*) remains in the active campaign release
assertions.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import re
from pathlib import Path

from app.release import facts
from app.release.evidence import default_evidence
from app.release.gate import evaluate
from tests.test_release_gate import CHECKLIST, FROZEN, LIVE, TODAY

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = REPO_ROOT / "alembic" / "versions"
HEAD = "0026_campaign_environment_scope"
PREVIOUS_HEAD = "0025_enterprise_leads"


# ------------------------------------------------------------------ facts ---

def test_migration_heads_reports_exactly_the_batch_06_head():
    heads = facts.migration_heads(VERSIONS_DIR)
    assert heads == (HEAD,)  # single head, and it is 0026


def test_migration_file_declares_the_correct_chain():
    path = VERSIONS_DIR / f"{HEAD}.py"
    assert path.is_file(), "0026 migration file must exist"

    spec = importlib.util.spec_from_file_location("_migration_0026", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == HEAD
    assert module.down_revision == PREVIOUS_HEAD
    assert module.branch_labels is None
    assert module.depends_on is None
    # upgrade/downgrade must both exist for a reversible chain
    assert callable(module.upgrade) and callable(module.downgrade)


def test_previous_head_file_exists_and_is_revised_exactly_once():
    """The chain stays linear: 0025 exists, and exactly one migration
    (0026) revises it — no branch, no duplicate head."""
    assert (VERSIONS_DIR / f"{PREVIOUS_HEAD}.py").is_file()
    revisers = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(
            rf'^down_revision\s*=\s*["\']{re.escape(PREVIOUS_HEAD)}["\']',
            text,
            re.MULTILINE,
        ):
            revisers.append(path.name)
    assert revisers == [f"{HEAD}.py"]


# ------------------------------------------------------------- gate drift ---

def test_release_facts_report_the_current_head_without_drift():
    """The gate's frozen-vs-live comparison (the check that blocks a release
    on migration drift) passes when both sides carry the real current head —
    i.e. the release facts report 0026 as the head and the campaign
    environment migration is visible to the release check."""
    real_heads = facts.migration_heads(VERSIONS_DIR)
    artifact = dataclasses.replace(FROZEN, migration_heads=real_heads)
    live = dataclasses.replace(LIVE, migration_heads=real_heads)

    report = evaluate(CHECKLIST, default_evidence(CHECKLIST), artifact, live, TODAY)
    drift_lines = [line for line in report.drift if "migration heads" in line]
    assert drift_lines == []


def test_a_stale_recorded_head_is_still_detected_as_drift():
    """Negative control: if a frozen artifact still recorded the pre-Batch-06
    head, the gate must flag migration drift — proving the comparison really
    looks at heads rather than passing unconditionally."""
    real_heads = facts.migration_heads(VERSIONS_DIR)
    artifact = dataclasses.replace(FROZEN, migration_heads=(PREVIOUS_HEAD,))
    live = dataclasses.replace(LIVE, migration_heads=real_heads)

    report = evaluate(CHECKLIST, default_evidence(CHECKLIST), artifact, live, TODAY)
    assert any("migration heads" in line for line in report.drift)


# ------------------------------------------------------------ stale heads ---

STALE_HEAD = re.compile(r"00(?:11|19|24|25)_[a-z0-9_]+")

SCAN_TARGETS = [
    REPO_ROOT / "app" / "release",
    REPO_ROOT / "app" / "services" / "campaign_service.py",
    REPO_ROOT / "app" / "api" / "campaign_routes.py",
    REPO_ROOT / "app" / "orchestration" / "campaign.py",
    REPO_ROOT / "scripts" / "release",
    REPO_ROOT / "tests" / "campaign",
]


def test_no_stale_head_expectations_in_active_campaign_release_code():
    """No campaign/release code or test asserts an old revision as the head.

    ``alembic/versions`` itself is excluded — a migration chain legitimately
    names its predecessors — and so is this file, which names them only to
    forbid them. The checklist's *evidence hint* prose (a P0-1 documentation
    string, not an assertion) is likewise out of scope here.
    """
    offenders = []
    for target in SCAN_TARGETS:
        paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in paths:
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in STALE_HEAD.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {match.group(0)}")
    assert offenders == []
