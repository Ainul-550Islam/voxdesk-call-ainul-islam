"""Regression tests for two defects that turned ordinary requests into 500s.

1. ``app.domain.agent_models.stable_id`` took exactly two arguments, but three
   domain modules call it with three or four parts (campaign identity, automation
   run idempotency, inbox thread identity). Every one of those calls raised
   ``TypeError: stable_id() takes 2 positional arguments but 4 were given`` at
   request time — so creating a campaign, running an automation, or resolving an
   inbox thread failed, while the two-argument callers kept working and hid the
   bug from review.

2. ``app.release.facts.migration_heads`` parsed only the plain
   ``revision = "..."` / ``down_revision = "..."` form. Newer migrations use the
   annotated form (``revision: str = ...``,
   ``down_revision: Union[str, None] = ...``), so their down-revisions were never
   recorded and the release gate reported a *branched* chain that does not
   exist — a release-blocking false positive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.agent_models import stable_id
from app.release import facts

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = REPO_ROOT / "alembic" / "versions"


# ----------------------------------------------------------------- stable_id --


def test_two_argument_call_is_unchanged():
    """The original (tenant, name) call must produce the historical digest.

    Identifiers produced by this function are persisted, so the two-argument
    behaviour is frozen: only the tenant is inserted verbatim, the name is
    stripped and lower-cased, and the two are joined with a colon.
    """
    import hashlib

    # Historical input: tenant verbatim, name stripped and lower-cased.
    digest = hashlib.sha256(b"tenant-1:receptionist").hexdigest()
    assert stable_id("tenant-1", "Receptionist") == f"agt_{digest[:16]}"
    # ...and the normalisation is what makes these two the same id.
    assert stable_id("tenant-1", "Receptionist") == stable_id("tenant-1", " receptionist ")


def test_variadic_parts_are_folded_in_order():
    """The call shapes the domain actually uses must not raise."""
    tenant = "tenant-1"

    # Automation run idempotency: (tenant, automation, event, business event).
    automation = stable_id(tenant, "automation-1", "call.completed", "evt-1")
    assert automation.startswith("agt_")
    assert automation == stable_id(tenant, "automation-1", "call.completed", "evt-1")
    assert automation != stable_id(tenant, "automation-1", "call.completed", "evt-2")

    # Campaign identity: (tenant, name, goal, channel).
    campaign = stable_id(tenant, "Spring Outreach", "book_demo", "voice")
    assert campaign == stable_id(tenant, "Spring Outreach", "book_demo", "voice")
    assert campaign != stable_id(tenant, "Spring Outreach", "book_demo", "sms")

    # Inbox thread identity: (tenant, channel, source ref).
    thread = stable_id(tenant, "email", "provider-message-1")
    assert thread != stable_id(tenant, "email", "provider-message-2")


def test_parts_are_normalised_consistently():
    """Stripping and lower-casing applies to every part, so ' Voice ' == 'voice'."""
    assert stable_id("t", "a", " Voice ") == stable_id("t", "a", "voice")


def test_order_of_parts_matters():
    """Position is significant: a swapped pair is a different identity."""
    assert stable_id("t", "a", "b") != stable_id("t", "b", "a")


def test_tenant_id_is_not_normalised():
    """Only ``parts`` are folded verbatim-normalised; the tenant stays as given.

    This mirrors the historical behaviour and keeps tenant identifiers
    case-sensitive, which is what the database stores.
    """
    assert stable_id("Tenant-A", "x") != stable_id("tenant-a", "x")


def test_missing_parts_is_a_programming_error_not_a_silent_collision():
    with pytest.raises(ValueError):
        stable_id("tenant-1")


# ------------------------------------------------------- release facts chain --


def test_migration_heads_reports_a_single_head():
    heads = facts.migration_heads(VERSIONS_DIR)
    assert len(heads) == 1, f"migration chain must stay linear, got heads: {heads}"
    assert heads == ("0062_drop_pcap_artifacts",)


def test_annotated_revisions_are_parsed():
    """The annotated declaration form must be understood, not skipped.

    Before the fix, 0039 and 0040 declared ``revision: str`` / ``down_revision:
    Union[str, None]`` and were invisible to the parser, so 0038 looked like a
    second head.
    """
    heads = facts.migration_heads(VERSIONS_DIR)
    assert "0038_agent_chat_conductor" not in heads
    assert "0039_conductor_control_plane" not in heads
    assert "0040_public_widget_keys" not in heads


def test_every_migration_file_contributes_a_revision():
    """No migration file may be silently skipped by the parser."""
    files = sorted(p for p in VERSIONS_DIR.glob("*.py") if p.name != "__init__.py")
    assert files, "no migration files found"

    parsed = 0
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if facts._REVISION.search(text):
            parsed += 1
    assert parsed == len(files), (
        "some migration files were not parsed; check the revision regex against "
        "the declaration style used in alembic/versions/"
    )
