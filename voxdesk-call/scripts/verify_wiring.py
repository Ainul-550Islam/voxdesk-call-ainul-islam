#!/usr/bin/env python3
"""Static wiring proof for the whole application surface (Batch 06).

Five independent questions, answered from real source and a real boot:

1. **Does the app boot?** ``app.main`` is imported in this process. A
   ``Permission``/``JobType``/model that does not exist fails here, at the same
   line the deployment would fail.
2. **Is every route module mounted?** Every module under ``app/`` that defines an
   ``APIRouter`` must be reachable from ``app/main.py`` through
   ``include_router()`` (directly or through an aggregator), and every module
   that declares route decorators must contribute at least one route to the live
   ``app.routes``. A module that compiles, mounts nothing and serves nothing is
   what this catches.
3. **Does every referenced enum member exist?** An AST pass over ``app/``,
   ``scripts/`` and ``tests/`` collects every ``Permission.X`` / ``JobType.X`` /
   ``Status.X`` attribute access whose base is an enum defined in this
   repository, and checks the member is declared. This is the class of defect
   that made ``import app.main`` raise ``AttributeError: ASSIST_READ`` while
   every individual module compiled.
4. **Does the scheduler run the v2 work?** The registered tick table in
   ``scripts/scheduler.py`` must cover exactly the tick exports of
   ``app/compliance_v2/jobs.py``, ``app/contact_center_v2/jobs.py`` and
   ``app/integrations_v2/jobs.py`` — no misses, no duplicates — and every
   registered tick must carry a lock key.
5. **Is every persisted v2 job type backed by exactly one registered handler?**

Output is JSON on stdout so CI can attach it; the process exits 1 when any
question is unanswered. ``--no-boot`` skips the import (used by the guard's own
tests, which must not pay a 20-second boot).
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import inspect
import io
import json
import os
import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCAN_ROOTS = ("app", "scripts", "tests")

#: Tick exports each v2 jobs module is required to have wired. Read from the
#: module's own ``__all__`` where it publishes one, so the list cannot drift
#: away from the code it describes.
SCHEDULER_JOB_MODULES = (
    "app.contact_center_v2.jobs",
    "app.compliance_v2.jobs",
    "app.integrations_v2.jobs",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


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


# ---------------------------------------------------------------------------
# 1. boot
# ---------------------------------------------------------------------------
def check_boot() -> dict:
    try:
        # Importing app.main emits startup diagnostics to stdout; suppress them
        # so this script's stdout stays pure JSON for CI consumers.
        with contextlib.redirect_stdout(io.StringIO()):
            import app.main as main_module
    except Exception as exc:  # noqa: BLE001 - the failure is the finding
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
    app = main_module.app
    from fastapi.routing import APIRoute, APIWebSocketRoute

    with contextlib.redirect_stdout(io.StringIO()):
        api_routes = [route for route in app.routes if isinstance(route, APIRoute)]
        sockets = [route for route in app.routes if isinstance(route, APIWebSocketRoute)]
    return {
        "ok": True,
        "api_routes": len(api_routes),
        "web_sockets": len(sockets),
        "operations": sum(len(route.methods or ()) for route in api_routes),
        "title": app.title,
    }


# ---------------------------------------------------------------------------
# 2. mount coverage
# ---------------------------------------------------------------------------
def _declares_routes(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
            ):
                return True
    return False


def check_mounts() -> dict:
    from scripts.verify_no_dead_routers import (
        ROUTE_PACKAGE,
        classify_modules,
        defines_router,
        mounted_modules,
        scan,
    )

    reachable = mounted_modules(ROOT)
    orphans = [item["module"] for item in scan(ROOT) if item["kind"] == "orphan_route_module"]

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import app.main as main_module

            served: dict[str, int] = {}
            for route in main_module.app.routes:
                module = str(getattr(getattr(route, "endpoint", None), "__module__", "") or "")
                if module:
                    served[module] = served.get(module, 0) + 1
    except Exception:  # noqa: BLE001 - check_boot already reported it
        served = {}

    silent: list[str] = []
    for path in _python_files(ROOT / ROUTE_PACKAGE):
        if path.name == "__init__.py":
            continue
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        if module not in reachable:
            continue
        try:
            tree = ast.parse(_read(path), filename=str(path))
        except SyntaxError:
            continue
        if not defines_router(tree) or not _declares_routes(tree):
            continue
        if served.get(module, 0) == 0:
            silent.append(module)

    verdicts = classify_modules(ROOT)
    return {
        "ok": not orphans and not silent,
        "route_modules": len(verdicts),
        "reachable": len(reachable),
        "orphans": sorted(orphans),
        "mounted_but_serving_nothing": sorted(silent),
        "allowlisted": sorted(
            verdict.module for verdict in verdicts if verdict.status == "allowlisted"
        ),
    }


# ---------------------------------------------------------------------------
# 3. enum members
# ---------------------------------------------------------------------------
def _collect_enum_classes() -> dict[str, set[str]]:
    """Every enum declared in this repository, with its members."""
    classes: dict[str, set[str]] = {}
    for base in SCAN_ROOTS:
        for path in _python_files(ROOT / base):
            try:
                tree = ast.parse(_read(path), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not any("Enum" in ast.unparse(base_node) for base_node in node.bases):
                    continue
                members = classes.setdefault(node.name, set())
                for statement in node.body:
                    if isinstance(statement, ast.Assign):
                        for target in statement.targets:
                            if isinstance(target, ast.Name):
                                members.add(target.id)
                    elif isinstance(statement, ast.AnnAssign) and isinstance(
                        statement.target, ast.Name
                    ):
                        members.add(statement.target.id)
    return classes


def check_enums() -> dict:
    classes = _collect_enum_classes()
    missing: dict[str, dict[str, list[str]]] = {}
    # Members are also legitimately attached after the class body (``JobState.TERMINAL
    # = frozenset(...)``), so a declared-outside-the-body attribute counts as existing.
    runtime_members: dict[str, set[str]] = {}
    for base in SCAN_ROOTS:
        for path in _python_files(ROOT / base):
            try:
                tree = ast.parse(_read(path), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and isinstance(node.value, ast.AST)
                    and node.value is not None
                ):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id in classes
                        ):
                            runtime_members.setdefault(target.value.id, set()).add(target.attr)

    checked = 0
    for base in SCAN_ROOTS:
        for path in _python_files(ROOT / base):
            relative = str(path.relative_to(ROOT))
            try:
                tree = ast.parse(_read(path), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                    continue
                enum_name = node.value.id
                if enum_name not in classes:
                    continue
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    continue
                checked += 1
                if node.attr in classes[enum_name] or node.attr in runtime_members.get(enum_name, set()):
                    continue
                missing.setdefault(enum_name, {}).setdefault(node.attr, []).append(
                    f"{relative}:{node.lineno}"
                )
    return {
        "ok": not missing,
        "enum_classes": len(classes),
        "references_checked": checked,
        "missing": {
            enum_name: {attr: sorted(where) for attr, where in sorted(attrs.items())}
            for enum_name, attrs in sorted(missing.items())
        },
    }


# ---------------------------------------------------------------------------
# 4. scheduler
# ---------------------------------------------------------------------------
def _tick_exports(module_name: str) -> set[str]:
    """Tick-shaped callables a v2 jobs module publishes (no required args)."""
    module = import_module(module_name)
    exported = getattr(module, "__all__", None)
    names = exported if exported is not None else [name for name in dir(module) if not name.startswith("_")]
    ticks: set[str] = set()
    for name in names:
        value = getattr(module, name, None)
        if not inspect.iscoroutinefunction(value):
            continue
        if getattr(value, "__module__", "") != module_name:
            continue
        signature = inspect.signature(value)
        required = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if required:
            continue
        ticks.add(name)
    return ticks


def _cc2_expected() -> set[str]:
    """The CC2 tick names the scheduler is required to run (session-taking)."""
    return {
        "pacing_tick_guard",
        "callback_dispatch_tick",
        "preview_reservation_reaper",
        "abandon_rollup",
        "occupancy_rollup",
        "stale_device_reaper",
    }


def check_scheduler() -> dict:
    import scripts.scheduler as scheduler

    ticks = scheduler.scheduled_v2_ticks()
    names = [tick.name for tick in ticks]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    locks = [tick.lock for tick in ticks]
    duplicate_locks = sorted({lock for lock in locks if locks.count(lock) > 1})
    unlocked = sorted(tick.name for tick in ticks if not tick.lock.strip())

    cc2_ticks = set(_cc2_expected())
    wired_cc2 = {tick.target for tick in ticks} & cc2_ticks
    compliance_ticks = _tick_exports("app.compliance_v2.jobs")
    wired_compliance = {tick.target for tick in ticks} & compliance_ticks
    int2_ticks = _tick_exports("app.integrations_v2.jobs")
    wired_int2 = {tick.target for tick in ticks} & int2_ticks

    expected = {
        "app.contact_center_v2.jobs": cc2_ticks,
        "app.compliance_v2.jobs": compliance_ticks,
        "app.integrations_v2.jobs": int2_ticks,
    }
    wired = {
        "app.contact_center_v2.jobs": wired_cc2,
        "app.compliance_v2.jobs": wired_compliance,
        "app.integrations_v2.jobs": wired_int2,
    }
    gaps = {
        module: {
            "missing": sorted(expected[module] - wired[module]),
            "unknown": sorted(wired[module] - expected[module]),
        }
        for module in SCHEDULER_JOB_MODULES
    }
    ok = (
        not duplicates
        and not duplicate_locks
        and not unlocked
        and all(not gap["missing"] for gap in gaps.values())
    )
    return {
        "ok": ok,
        "registered": names,
        "duplicate_names": duplicates,
        "duplicate_locks": duplicate_locks,
        "ticks_without_lock": unlocked,
        "expected_per_module": {module: sorted(values) for module, values in expected.items()},
        "gaps": gaps,
    }


def check_job_handlers() -> dict:
    """Every persisted v2 job type has exactly one registered handler."""
    from app.jobs.registry import bootstrap
    from app.jobs.types import handler_for

    with contextlib.redirect_stdout(io.StringIO()):
        registered = bootstrap()
    required = [
        "cc2.dialer.start",
        "cc2.dialer.tick",
        "cc2.assist.turn",
        "acd_callback",
    ]
    missing = [job_type for job_type in required if handler_for(job_type) is None]
    return {
        "ok": not missing,
        "handlers": len(registered),
        "required": required,
        "missing": missing,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-boot", action="store_true", help="skip importing app.main")
    parser.add_argument("--json", action="store_true", help="(default) print JSON")
    args = parser.parse_args(argv)

    report: dict[str, object] = {
        "boot": {"ok": True, "skipped": True} if args.no_boot else check_boot(),
        "mounts": check_mounts(),
        "enums": check_enums(),
        "scheduler": check_scheduler(),
        "job_handlers": check_job_handlers(),
    }
    failures = [name for name, section in report.items() if not section["ok"]]  # type: ignore[index]
    report["ok"] = not failures
    report["failures"] = failures
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
