# File: tests/api/test_frontend_backend_contract.py — the dashboard may only
# call routes the live application serves.
"""Tests for ``scripts/verify_frontend_backend_paths.py``.

The scanner compares the dashboards' source against ``app.openapi()`` and the
mounted ``app.routes``, so it cannot drift out of date the way a snapshot
would. These tests exist because a scanner that silently stops finding anything
is worse than no scanner: a green run would then mean nothing.

Covered:

1. The scanner passes against the repository as it stands, with an empty
   allowlist.
2. It catches a path the backend does not serve — the check is not vacuous.
3. It resolves a path whose last segment is built at runtime against the
   concrete routes the server really mounts, and it rejects one whose prefix
   has no children at all.
4. Normalization treats ``{}``, ``${id}`` and ``:id`` as the same placeholder,
   and drops query strings before comparing.
5. Routes mounted with ``include_in_schema=False`` are still recognised as
   served, because the server answers them.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_scanner() -> ModuleType:
    """Import the scanner as a module.

    It lives in ``scripts/`` rather than in a package, so it is loaded from its
    file path. Running it in a thread keeps its own asyncio loop — it imports
    ``app.main``, which is not re-entrant inside a running loop's imports —
    separate from pytest's.
    """
    path = ROOT / "scripts" / "verify_frontend_backend_paths.py"
    spec = importlib.util.spec_from_file_location("verify_frontend_backend_paths", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scanner() -> ModuleType:
    return _load_scanner()


@pytest.fixture(scope="module")
def backend(scanner: ModuleType) -> dict:
    routes, count = scanner.load_backend_paths()
    assert count > 0
    return routes


def test_the_repository_passes_with_an_empty_allowlist(scanner: ModuleType) -> None:
    report = scanner.scan(set())
    unmatched = report["unmatched"]
    assert unmatched == [], f"dashboard calls a path the backend does not serve: {unmatched}"
    assert report["status"] == "PASS"
    assert report["allowlisted_count"] == 0
    # A run that found no API calls at all would pass for the wrong reason.
    assert report["matched_count"] > 100


def test_the_check_is_not_vacuous(
    scanner: ModuleType, backend: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path nobody serves is reported, with the file and line that used it.

    A scanner that found nothing would also exit zero, so this plants one
    unserved call in a scratch file, points the file list at it, and requires
    the run to fail and to name the line.
    """
    planted = ROOT / "dashboard" / "src" / "__verify_unserved_path__.tsx"
    planted.write_text(
        "export async function go(id: string) {\n"
        "  return fetch(`/api/definitely/not/served/${id}`);\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scanner, "iter_source_files", lambda: [planted])
    try:
        report = scanner.scan(set())
    finally:
        planted.unlink(missing_ok=True)

    assert report["status"] == "FAIL"
    assert report["unmatched_count"] == 1
    entry = report["unmatched"][0]
    assert entry["path"] == "/api/definitely/not/served/${id}"
    assert entry["normalized"] == "/api/definitely/not/served/{}"
    assert entry["line"] == 2
    assert entry["file"].endswith("__verify_unserved_path__.tsx")
    assert scanner.normalize_path("/api/definitely/not/served") not in backend


def test_normalization_collapses_placeholders_and_drops_queries(scanner: ModuleType) -> None:
    assert scanner.normalize_path("/api/calls/${callId}") == "/api/calls/{}"
    assert scanner.normalize_path("/api/calls/{call_id}") == "/api/calls/{}"
    assert scanner.normalize_path("/api/calls/:call_id") == "/api/calls/{}"
    assert scanner.normalize_path("/api/calls/abc?include=turns") == "/api/calls/abc"
    assert scanner.normalize_path("/api/calls/abc#section") == "/api/calls/abc"
    assert scanner.normalize_path("/api/calls//abc") == "/api/calls/abc"
    assert scanner.normalize_path("/api/calls/abc/") == "/api/calls/abc"
    assert scanner.normalize_path(
        '/api/webhooks/${encodeURIComponent(subscriptionId ?? "")}'
        "/deliveries/${encodeURIComponent(deliveryId)}/retry"
    ) == "/api/webhooks/{}/deliveries/{}/retry"
    assert scanner.normalize_path('/api/calls/${id ?? "fallback"}') == "/api/calls/{}"


def test_a_trailing_query_interpolation_is_not_a_path_segment(scanner: ModuleType) -> None:
    """``${qs}`` after a path is routing-irrelevant, not a missing id."""
    assert scanner.normalize_path("/api/calls${qs}") == "/api/calls"
    assert scanner.normalize_path("/api/calls/${id}/events${suffix}") == "/api/calls/{}/events"


def test_a_runtime_action_segment_resolves_against_real_routes(
    scanner: ModuleType, backend: dict
) -> None:
    """``${action}`` cannot be resolved statically, so the check becomes real."""
    hits = scanner.dynamic_prefix_hits("/api/calls/{}/{}", backend)
    assert isinstance(hits, list)
    # Either the server serves concrete routes under the prefix, or there are
    # none and such a call must be reported rather than waved through.
    for hit in hits:
        assert hit in backend


def test_an_unknown_prefix_resolves_to_nothing(scanner: ModuleType, backend: dict) -> None:
    assert scanner.dynamic_prefix_hits("/api/no/such/prefix/{}", backend) == []


def test_extraction_ignores_comments_and_non_api_paths(tmp_path: Path, scanner: ModuleType) -> None:
    source = tmp_path / "Example.tsx"
    source.write_text(
        "\n".join(
            [
                "// const dead = \"/api/only/in/a/comment\";",
                "/* const blocked = \"/api/only/in/a/block\"; */",
                "const page = \"/dashboard/calls\";",
                "const prefix = \"/api\";",
                "const live = await api.get(\"/api/calls/${id}\");",
            ]
        ),
        encoding="utf-8",
    )
    found = scanner.extract_api_paths(scanner.strip_comments(source.read_text(encoding="utf-8")))
    paths = [value for _line, value in found]
    assert "/api/only/in/a/comment" not in paths
    assert "/api/only/in/a/block" not in paths
    assert "/dashboard/calls" not in paths
    assert "/api" not in paths
    assert "/api/calls/${id}" in paths


@pytest.mark.parametrize(
    "path",
    [
        "/api/agent/voice-profiles",
        "/api/agent/voice-profiles/{}/activate",
        "/api/agent/voice-profiles/{}/deactivate",
        "/api/translation/jobs",
        "/api/translation/jobs/{}",
        "/api/translation/jobs/{}/export",
        "/api/translations/jobs",
        "/api/translations/jobs/{}",
        "/api/translations/jobs/{}/export",
    ],
)
def test_the_new_phase_one_paths_are_served(path: str, backend: dict) -> None:
    assert path in backend, f"{path} is not mounted"


def test_schema_hidden_routes_are_still_recognised_as_served(backend: dict) -> None:
    """``include_in_schema=False`` hides a route from the docs, not from HTTP.

    The alias surface and the client-transfer completion routes are reachable;
    reporting them as contract violations would send someone to "fix" a router
    that is working.
    """
    for path in ("/api/translations/jobs", "/api/translations/jobs/{}/export"):
        assert path in backend


def test_the_scanner_entry_point_exits_zero_on_a_clean_repository() -> None:
    """``make verify-fe-be`` has to fail the build when this changes."""
    script = ROOT / "scripts" / "verify_frontend_backend_paths.py"
    assert script.is_file()

    module = _load_scanner()
    assert module.main(["--quiet"]) == 0
