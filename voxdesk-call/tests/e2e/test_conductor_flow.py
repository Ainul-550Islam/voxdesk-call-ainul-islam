from __future__ import annotations

import pytest

from tests.integration.test_conductor_workflow import (
    test_conductor_full_workflow_partial_approve_simulate_and_immutable_apply,
)


@pytest.mark.asyncio
async def test_prompt8_conductor_failure_reproduction_review_and_immutable_apply(engine):
    """Run the repository's complete persisted Conductor acceptance scenario.

    The canonical scenario lives in tests/integration/test_conductor_workflow.py
    and exercises a real API/database lifecycle: proposal, deterministic diff,
    candidate simulation, reproduction test case, granular approval/rejection,
    undo, idempotent apply, and immutable version checks. This named Prompt 8
    journey delegates to that full scenario rather than copying or weakening it.
    """
    await test_conductor_full_workflow_partial_approve_simulate_and_immutable_apply(engine)
