"""No code may reference an enum member the project does not declare.

``import app.main`` failed at ``e3b64fd`` with
``AttributeError: type object 'Permission' has no attribute 'ASSIST_READ'``, and
``app.contact_center_v2.jobs`` failed at import with
``JobType.CC2_DIALER_START``. Neither is visible in a per-module compile check
and neither shows up until the exact import path runs, which is why the check is
static: it reads every reference and compares it against the declarations, so a
missing member is reported with the file and line that asked for it — before any
process gets far enough to raise.
"""

from __future__ import annotations

import ast
import os
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("app", "scripts", "tests")

#: The enums every route, handler and scheduler pass through. Listed explicitly
#: so the test keeps checking *these* even if a future refactor moves them.
CRITICAL_ENUMS = ("Permission", "JobType", "JobState", "JobPriority", "FailureClass")


def _python_files(base: Path):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in {"__pycache__", "node_modules", ".next", "dist", "build"}
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield Path(dirpath) / filename


@lru_cache(maxsize=None)
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))


def _is_declaration_holder(node: ast.ClassDef) -> bool:
    """True for an enum **or** a class of string constants used the same way.

    ``JobType`` (``app/jobs/types.py``) is deliberately not an ``Enum`` — it is a
    plain class of ``str`` constants that are persisted in ``jobs.job_type``. It
    is referenced exactly like an enum (``JobType.CC2_DIALER_START``) and it is
    where the batch's second ``AttributeError`` came from, so a scanner that only
    understood ``Enum`` subclasses would have missed it.
    """
    if any("Enum" in ast.unparse(parent) for parent in node.bases):
        return True
    for statement in node.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
            if isinstance(statement.value.value, str) and any(
                isinstance(target, ast.Name) and target.id.isupper()
                for target in statement.targets
            ):
                return True
    return False


@lru_cache(maxsize=1)
def _enum_declarations() -> dict[str, set[str]]:
    """Declaration name -> declared member names, plus members attached after it."""
    declared: dict[str, set[str]] = {}
    for base in SCAN_ROOTS:
        for path in _python_files(ROOT / base):
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _is_declaration_holder(node):
                    continue
                members = declared.setdefault(node.name, set())
                for statement in node.body:
                    if isinstance(statement, ast.Assign):
                        members.update(
                            target.id for target in statement.targets if isinstance(target, ast.Name)
                        )
                    elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                        members.add(statement.target.id)
    # Members can be attached to the class after the body, e.g.
    # `JobState.TERMINAL = frozenset({...})` (app/jobs/models.py). Those are
    # real attributes and must count as declared.
    for base in SCAN_ROOTS:
        for path in _python_files(ROOT / base):
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in declared
                    ):
                        declared[target.value.id].add(target.attr)
    return declared


@lru_cache(maxsize=None)
def _references_declared() -> dict[str, dict[str, list[str]]]:
    declared = _enum_declarations()
    """Enum name -> member -> locations that reference it."""
    found: dict[str, dict[str, list[str]]] = {}
    for base in SCAN_ROOTS:
        for path in _python_files(ROOT / base):
            relative = str(path.relative_to(ROOT))
            tree = _parse(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                    continue
                enum_name = node.value.id
                # `Permission.__members__` and the other protocol attributes are
                # attributes of the *class object*, not members this test is
                # about, so dunders are out of scope.
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    continue
                if enum_name not in declared or node.attr in declared[enum_name]:
                    continue
                found.setdefault(enum_name, {}).setdefault(node.attr, []).append(
                    f"{relative}:{node.lineno}"
                )
    return found


def test_no_reference_to_an_undeclared_enum_member():
    declared = _enum_declarations()
    assert "Permission" in declared and "JobType" in declared, sorted(declared)[:20]
    missing = _references_declared()
    report = "\n".join(
        f"{enum_name}.{member} referenced at {', '.join(sorted(where))}"
        for enum_name, members in sorted(missing.items())
        for member, where in sorted(members.items())
    )
    assert missing == {}, report


@pytest.mark.parametrize("enum_name", CRITICAL_ENUMS)
def test_the_critical_enums_exist_and_are_non_empty(enum_name: str):
    declared = _enum_declarations()
    assert enum_name in declared, f"{enum_name} is not declared anywhere under app/"
    assert declared[enum_name], f"{enum_name} declares no members"


@pytest.mark.parametrize("enum_name", CRITICAL_ENUMS)
def test_the_critical_enums_are_importable_with_their_members(enum_name: str):
    """The static scan and the real import must agree."""
    import app.jobs.types as job_types
    import app.jobs.models as job_models
    from app.auth import permissions

    holders = {
        "Permission": permissions,
        "JobType": job_types,
        "JobState": job_models,
        "JobPriority": job_types,
        "FailureClass": job_types,
    }
    declared = _enum_declarations()[enum_name]
    holder = getattr(holders[enum_name], enum_name)
    for member in declared:
        if member.isupper():
            assert hasattr(holder, member), f"{enum_name}.{member} is declared but not importable"


def test_every_job_type_reference_points_at_a_registered_value():
    """``JobType`` values and the handler registry must not drift apart."""
    from app.jobs.types import JobType

    referenced = set()
    for base in ("app", "scripts"):
        for path in _python_files(ROOT / base):
            tree = _parse(path)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "JobType"
                ):
                    referenced.add(node.attr)
    values = {
        name: getattr(JobType, name)
        for name in dir(JobType)
        if name.isupper() and isinstance(getattr(JobType, name), str)
    }
    # `JobType.ALL` is the frozenset of values, not a value itself.
    unknown = {name for name in referenced if name != "ALL"} - set(values)
    assert unknown == set(), f"unknown JobType members: {sorted(unknown)}"
    # The `ALL` set must agree with the attributes, or enqueue-time validation
    # will reject a type the worker can execute.
    assert set(values.values()) == set(JobType.ALL)


def test_permission_values_are_unique():
    """Two names for one scope is an authorization bug waiting to happen."""
    from app.auth.permissions import Permission

    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for member in Permission:
        if member.value in seen:
            duplicates.append(f"{seen[member.value]} and {member.name} share {member.value!r}")
        else:
            seen[member.value] = member.name
    assert duplicates == [], duplicates


def test_every_role_bundle_only_holds_declared_permissions():
    from app.auth.rbac import ROLE_PERMISSIONS
    from app.auth.permissions import Permission

    for role, bundle in ROLE_PERMISSIONS.items():
        for permission in bundle:
            assert isinstance(permission, Permission), f"{role} holds {permission!r}"
            assert permission.name in Permission.__members__
