#!/usr/bin/env python3
"""AST gate: every enum member referenced in code must be defined.

Pattern this guards against (found in earlier batches): ``Permission.X`` or
``JobType.X`` written in code while ``X`` is not a member of the enum. Python only
fails at the moment that line executes, so a rarely used branch can ship broken
and stay invisible until production traffic reaches it.

Method (static, no imports of the scanned code):

1. Collect every "declaration holder" in ``app/``: a class whose bases name an
   ``Enum`` family class (``Enum``, ``StrEnum``, ``IntEnum``, ``Flag``,
   ``IntFlag``), or a plain class of upper-case ``str`` constants that is used the
   same way (``JobType`` in ``app/jobs/types.py`` is deliberately not an ``Enum``
   and was the source of a production ``AttributeError``). Record its member
   names from the class body.
   Members attached after the class body (``JobState.TERMINAL = frozenset(DONE)``,
   ``app/jobs/models.py``) are real attributes and are counted as declared.
2. Scan ``app/``, ``tests/``, ``scripts/`` and ``alembic/`` for attribute
   accesses ``Name.Attr`` where ``Name`` is bound to an enum class in that
   module (imported from the enum's defining module, or defined in it).
   ``Attr`` must be a member, or a method/property/attribute that the enum class
   defines (for example ``.value``, ``.name`` or a classmethod).
3. Report ``Name.Attr`` where ``Name`` is an enum class name that is *used but not
   bound* in the module, since that is a ``NameError`` waiting to happen.

Exit code 0 when no problems are found, 1 otherwise. ``--json`` emits a stable
report for CI.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENUM_BASES = frozenset({"Enum", "StrEnum", "IntEnum", "Flag", "IntFlag"})
SCAN_ROOTS = ("app", "tests", "scripts", "alembic")
ENUM_DEFINITION_ROOT = "app"
# Attributes every enum member/class has, regardless of the class body.
BUILTIN_ENUM_ATTRIBUTES = frozenset(
    {
        "name",
        "value",
        "_value_",
        "_name_",
        "_member_map_",
        "_member_names_",
        "_value2member_map_",
        "mro",
        "__members__",
    }
)


@dataclass
class EnumClass:
    qualified: str  # dotted module path + class name
    module: str
    name: str
    members: set[str] = field(default_factory=set)
    attributes: set[str] = field(default_factory=set)  # methods, properties, class attrs


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _enum_bases_of(cls: ast.ClassDef, known_enum_names: set[str]) -> bool:
    for base in cls.bases:
        name = _base_name(base)
        if name and (name in ENUM_BASES or name in known_enum_names):
            return True
    return False


def _is_constant_holder(cls: ast.ClassDef) -> bool:
    """A plain class of upper-case ``str`` constants used like an enum."""
    for statement in cls.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
            if isinstance(statement.value.value, str) and any(
                isinstance(target, ast.Name) and target.id.isupper() for target in statement.targets
            ):
                return True
    return False


def _collect_enum_classes() -> dict[str, EnumClass]:
    """Enum classes defined under app/, keyed by ``module.ClassName``.

    Enum-of-enum inheritance is resolved in two passes so that a class deriving
    from another app enum is recognised too.
    """
    trees: list[tuple[str, ast.Module]] = []
    for path in sorted((REPO_ROOT / ENUM_DEFINITION_ROOT).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            trees.append((_module_name(path), ast.parse(path.read_text(encoding="utf-8"))))
        except SyntaxError:
            continue

    enum_names: set[str] = set()
    for _module, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                _base_name(b) in ENUM_BASES for b in node.bases
            ):
                enum_names.add(node.name)

    classes: dict[str, EnumClass] = {}
    for module, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not (_enum_bases_of(node, enum_names) or _is_constant_holder(node)):
                continue
            entry = EnumClass(qualified=f"{module}.{node.name}", module=module, name=node.name)
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            if target.id.startswith("_"):
                                entry.attributes.add(target.id)
                            else:
                                entry.members.add(target.id)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if item.value is not None and not item.target.id.startswith("_"):
                        entry.members.add(item.target.id)
                    else:
                        entry.attributes.add(item.target.id)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    entry.attributes.add(item.name)
            classes[entry.qualified] = entry

    # Attributes attached after the class body, e.g. `JobState.TERMINAL = frozenset(DONE)`.
    # Matched by class name across app/, the same rule tests/test_no_undefined_enum_members.py
    # applies, so a module that attaches a member to a class imported from elsewhere counts.
    by_name: dict[str, list[EnumClass]] = {}
    for enum in classes.values():
        by_name.setdefault(enum.name, []).append(enum)
    for _module, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    for enum in by_name.get(target.value.id, []):
                        enum.attributes.add(target.attr)
    return classes


def _resolve_import(module: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    base_parts = module.split(".")
    # level 1 means the importing module's package.
    package_parts = base_parts[: len(base_parts) - node.level]
    if node.module:
        package_parts += node.module.split(".")
    return ".".join(package_parts) if package_parts else None


def _check_file(
    path: Path,
    module: str,
    enum_classes: dict[str, EnumClass],
    enum_by_name: dict[str, list[EnumClass]],
) -> list[dict[str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [
            {
                "file": str(path.relative_to(REPO_ROOT)),
                "line": str(exc.lineno or 0),
                "kind": "syntax",
                "detail": str(exc),
            }
        ]

    bound: dict[str, str] = {}  # local name -> qualified enum class
    bound_names: set[str] = set()  # every name bound in this module
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = _resolve_import(module, node)
            for alias in node.names:
                local = alias.asname or alias.name
                bound_names.add(local)
                if source and f"{source}.{alias.name}" in enum_classes:
                    bound[local] = f"{source}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound_names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound_names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound_names.add(node.id)
        elif isinstance(node, ast.arg):
            bound_names.add(node.arg)
    for qualified, enum in enum_classes.items():
        if enum.module == module:
            bound[enum.name] = qualified
            bound_names.add(enum.name)

    problems: list[dict[str, str]] = []
    relative = str(path.relative_to(REPO_ROOT))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        name = node.value.id
        if name in bound:
            enum = enum_classes[bound[name]]
            attr = node.attr
            if attr in enum.members or attr in enum.attributes or attr in BUILTIN_ENUM_ATTRIBUTES:
                continue
            if attr.startswith("__") and attr.endswith("__"):
                continue
            problems.append(
                {
                    "file": relative,
                    "line": str(node.lineno),
                    "kind": "undefined_member",
                    "detail": f"{name}.{attr} is not a member of {enum.qualified}",
                }
            )
        elif name in enum_by_name and name not in bound_names:
            candidates = ", ".join(sorted(e.qualified for e in enum_by_name[name]))
            problems.append(
                {
                    "file": relative,
                    "line": str(node.lineno),
                    "kind": "unbound_enum",
                    "detail": f"{name}.{node.attr} used but {name} is not imported here (defined in: {candidates})",
                }
            )
    return problems


def run() -> tuple[list[dict[str, str]], dict[str, int]]:
    enum_classes = _collect_enum_classes()
    enum_by_name: dict[str, list[EnumClass]] = {}
    for enum in enum_classes.values():
        enum_by_name.setdefault(enum.name, []).append(enum)

    problems: list[dict[str, str]] = []
    scanned = 0
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            scanned += 1
            problems.extend(_check_file(path, _module_name(path), enum_classes, enum_by_name))
    stats = {
        "enum_classes": len(enum_classes),
        "enum_members": sum(len(e.members) for e in enum_classes.values()),
        "files_scanned": scanned,
    }
    return problems, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    problems, stats = run()
    if args.json:
        json.dump({"ok": not problems, "stats": stats, "problems": problems}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(
            f"enum classes: {stats['enum_classes']}, members: {stats['enum_members']}, "
            f"files scanned: {stats['files_scanned']}"
        )
        for problem in problems:
            print(
                f"FAIL  {problem['file']}:{problem['line']} [{problem['kind']}] {problem['detail']}"
            )
        if not problems:
            print("OK: every enum member referenced in code is defined.")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
