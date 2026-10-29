"""Synthetic-repo tests for scripts/closer/enum_members_check.py.

The checker is run against a temporary repository whose ``REPO_ROOT`` is
monkeypatched, so each test controls exactly which enum, holder class or
consumer exists. The rules under test:

* a plain upper-case ``str`` constant holder (``JobType``-style) is a holder, and
  an access to an undefined member on it is reported;
* a member attached after the class body (``JobState.TERMINAL = frozenset(DONE)``) is a
  declared member, not a missing one;
* an access to a member that exists is not reported.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[2] / "scripts" / "closer" / "enum_members_check.py"


@pytest.fixture()
def checker(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("enum_members_check_under_test", CHECKER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    return module


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _problems(checker) -> list[dict[str, str]]:
    problems, _stats = checker.run()
    return problems


def test_undefined_member_of_a_constant_holder_is_reported(checker, tmp_path):
    _write(tmp_path, "app/__init__.py", "")
    _write(
        tmp_path,
        "app/jobs/types.py",
        'class JobType:\n    OUTBOX = "outbox.delivery"\n',
    )
    _write(
        tmp_path,
        "app/worker.py",
        "from app.jobs.types import JobType\n\n\ndef pick():\n    return JobType.MISSING_MEMBER\n",
    )

    problems = _problems(checker)

    assert [p["kind"] for p in problems] == ["undefined_member"], problems
    assert "JobType.MISSING_MEMBER" in problems[0]["detail"]


def test_a_declared_constant_on_a_holder_is_not_reported(checker, tmp_path):
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/jobs/types.py", 'class JobType:\n    OUTBOX = "outbox.delivery"\n')
    _write(
        tmp_path,
        "app/worker.py",
        "from app.jobs.types import JobType\n\n\ndef pick():\n    return JobType.OUTBOX\n",
    )

    assert _problems(checker) == []


def test_a_member_attached_after_the_class_body_is_declared(checker, tmp_path):
    _write(tmp_path, "app/__init__.py", "")
    _write(
        tmp_path,
        "app/jobs/models.py",
        "from enum import Enum\n\n\nclass JobState(str, Enum):\n    QUEUED = 'queued'\n    DONE = 'done'\n\n\n"
        "JobState.TERMINAL = frozenset({JobState.DONE})\n",
    )
    _write(
        tmp_path,
        "app/consumer.py",
        "from app.jobs.models import JobState\n\n\ndef terminal():\n    return JobState.TERMINAL\n",
    )

    assert _problems(checker) == []


def test_main_exits_nonzero_on_a_problem_and_zero_when_clean(checker, tmp_path, capsys):
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/jobs/types.py", 'class JobType:\n    OUTBOX = "outbox.delivery"\n')
    _write(
        tmp_path,
        "app/worker.py",
        "from app.jobs.types import JobType\n\n\ndef pick():\n    return JobType.NOPE\n",
    )
    assert checker.main([]) == 1

    _write(
        tmp_path,
        "app/worker.py",
        "from app.jobs.types import JobType\n\n\ndef pick():\n    return JobType.OUTBOX\n",
    )
    assert checker.main([]) == 0
