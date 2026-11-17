# File: tests/deployment/test_polyglot_scope.py — the polyglot claim must match
# what actually runs a call.
"""Enforcement for ADR-003 (``docs/adr/ADR-003-polyglot-scope.md``).

The ADR says three things that are checkable rather than merely stated:

1. `services/realtime/gateway-go` and `services/realtime/media-engine-rs` are on
   the call path and are the ones the deployment artefacts reference.
2. `experimental/` is not on the call path.
3. Nothing in production *depends* on `experimental/` — CI may reference it,
   production may not.

If any of these stops being true, the sales claim stops being true, and this
file fails. Written for PART 17B / P17B-02 (audit finding S-15).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

EXPERIMENTAL_SERVICES = ("control-plane", "signal-go", "media-plane", "ops")

#: Artefacts that decide what a deployment actually runs.
DEPLOYMENT_ARTEFACTS = (
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.sip.yml",
    "Caddyfile",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 1. The directory layout itself
# ---------------------------------------------------------------------------


def test_each_set_aside_service_lives_under_experimental() -> None:
    for name in EXPERIMENTAL_SERVICES:
        assert (ROOT / "experimental" / name).is_dir(), f"experimental/{name} is missing"
        assert not (ROOT / "services" / name).exists(), (
            f"services/{name} still exists; ADR-003 moved it to experimental/ and "
            "leaving a copy behind recreates the ambiguity the ADR resolves"
        )


def test_experimental_readme_states_the_rule_in_its_first_lines() -> None:
    """The README is what a buyer or auditor actually opens first."""
    body = _text(ROOT / "experimental" / "README.md")
    first_paragraph = " ".join(body.splitlines()[:12]).lower()
    assert "not on the call path" in first_paragraph
    assert "not part of sale claims" in first_paragraph


def test_services_directory_contains_only_call_path_code() -> None:
    present = {p.name for p in (ROOT / "services").iterdir() if p.is_dir()}
    assert present == {"realtime", "sip-gateway"}, (
        f"services/ contains {sorted(present)}; per ADR-003 only the deployed "
        "call-path services may live there"
    )
    assert (ROOT / "services" / "realtime" / "gateway-go").is_dir()
    assert (ROOT / "services" / "realtime" / "media-engine-rs").is_dir()


# ---------------------------------------------------------------------------
# 2. Deployment artefacts reference the call-path services, not experimental/
# ---------------------------------------------------------------------------


def test_prod_compose_builds_the_two_realtime_services() -> None:
    compose = _text(ROOT / "docker-compose.prod.yml")
    assert "./services/realtime/gateway-go" in compose
    assert "./services/realtime/media-engine-rs" in compose


def test_no_deployment_artefact_references_an_experimental_service() -> None:
    offenders: list[str] = []
    for name in DEPLOYMENT_ARTEFACTS:
        path = ROOT / name
        if not path.exists():
            continue
        body = _text(path)
        for service in EXPERIMENTAL_SERVICES:
            for needle in (f"experimental/{service}", f"services/{service}"):
                if needle in body:
                    offenders.append(f"{name}: {needle}")
    assert not offenders, (
        "a deployment artefact references a service that is not on the call path: "
        + ", ".join(offenders)
    )


def test_helm_renders_gateway_and_media_engine_deployments() -> None:
    """The chart is the Kubernetes truth; ADR-003 claims both are deployable."""
    helm = ROOT / "infra" / "helm" / "voxdesk" / "templates"
    assert (helm / "realtime-gateway.yaml").is_file()
    assert (helm / "media-engine.yaml").is_file()
    # And the experimental services have no template at all.
    for name in EXPERIMENTAL_SERVICES:
        assert not (helm / f"{name}.yaml").exists(), (
            f"infra/helm/voxdesk/templates/{name}.yaml exists; an experimental "
            "service must not be deployable"
        )


# ---------------------------------------------------------------------------
# 3. Dependency direction: production never reaches into experimental/
# ---------------------------------------------------------------------------


def _production_python_files() -> list[Path]:
    root = ROOT / "app"
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_application_module_imports_from_experimental() -> None:
    offenders = []
    for path in _production_python_files():
        body = _text(path)
        if "experimental" not in body:
            continue
        # A comment that mentions the directory is fine; an import is not.
        for match in re.finditer(r"^\s*(?:from|import)\s+([^\n#]+)", body, re.MULTILINE):
            if "experimental" in match.group(1):
                offenders.append(f"{path.relative_to(ROOT)}: {match.group(0).strip()}")
        for match in re.finditer(r"""["']experimental/""", body):
            offenders.append(f"{path.relative_to(ROOT)}: references {match.group(0)}")
    assert not offenders, (
        "production code depends on experimental/ (ADR-003 makes the dependency "
        "one-way):\n  " + "\n  ".join(offenders)
    )


def test_no_production_python_module_names_an_experimental_service_path() -> None:
    offenders = []
    for path in _production_python_files():
        body = _text(path)
        for service in EXPERIMENTAL_SERVICES:
            needle = f"services/{service}"
            if needle in body:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert not offenders, "stale path to a service moved to experimental/:\n  " + "\n  ".join(offenders)


def test_ci_still_builds_the_experimental_services() -> None:
    """Moved is not deleted: a broken build in experimental/ still breaks HEAD."""
    workflow = _text(ROOT / ".github" / "workflows" / "polyglot.yml")
    for name in EXPERIMENTAL_SERVICES:
        assert f"experimental/{name}" in workflow, (
            f"the polyglot workflow no longer builds experimental/{name}; "
            "either restore the job or delete the directory deliberately"
        )


# ---------------------------------------------------------------------------
# 4. The ADR exists, is linked, and names the promotion bar
# ---------------------------------------------------------------------------


def test_adr_states_the_call_path_and_the_promotion_evidence() -> None:
    adr = ROOT / "docs" / "adr" / "ADR-003-polyglot-scope.md"
    assert adr.is_file(), "ADR-003 is missing"
    body = _text(adr)
    for required in (
        "services/realtime/gateway-go",
        "services/realtime/media-engine-rs",
        "services/sip-gateway",
        "experimental/",
        "promote",  # the promotion section
    ):
        assert required in body, f"ADR-003 never mentions {required!r}"


def test_services_readme_points_at_the_adr() -> None:
    body = _text(ROOT / "services" / "README.md")
    assert "ADR-003" in body
    assert "experimental/" in body
    for name in EXPERIMENTAL_SERVICES:
        assert f"├── {name}/" not in body and f"│   └── {name}/" not in body, (
            f"services/README.md still lists {name} under services/"
        )


def test_the_adr_index_lists_adr_003() -> None:
    """An ADR nobody links to is a file, not a decision."""
    candidates = list((ROOT / "docs" / "adr").glob("*.md")) + [ROOT / "docs" / "CURRENT-ARCHITECTURE.md"]
    referencing = [
        p.name for p in candidates if p.is_file() and "ADR-003" in _text(p) and p.name != "ADR-003-polyglot-scope.md"
    ]
    assert referencing, "no other document references ADR-003"


@pytest.mark.parametrize("service", EXPERIMENTAL_SERVICES)
def test_moved_service_kept_its_own_readme(service: str) -> None:
    """The design work is preserved, not just relocated."""
    assert (ROOT / "experimental" / service / "README.md").is_file() or (
        ROOT / "experimental" / service / "Cargo.toml"
    ).is_file()
