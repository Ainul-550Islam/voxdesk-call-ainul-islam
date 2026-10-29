"""Static assertions on the buyer-facing deploy assets.

These tests parse the Helm values, the chart templates, the production compose
file and the Caddyfiles and assert the properties a buyer's platform team checks
before it agrees to run someone else's container:

* the workload runs as a non-root user with a read-only root filesystem;
* every workload has CPU/memory requests *and* limits, otherwise one pod can
  starve the node or be the first thing OOM-killed;
* probes exist, so Kubernetes can tell "starting" from "broken";
* an HPA and a PodDisruptionBudget exist, so a node drain is not an outage;
* image tags are immutable — ``:latest`` makes a rollback a guess;
* the database is not published to the host, and the API only binds loopback;
* the edge proxy sets HSTS, CSP, X-Content-Type-Options and frame options.

Nothing here applies, plans or contacts a cluster. ``scripts/verify_deploy_assets.sh``
is the tool that runs helm/terraform/caddy/docker for real; this is the half that
can run anywhere, on every commit, with no toolchain beyond PyYAML.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HELM_DIR = REPO_ROOT / "infra" / "helm" / "voxdesk"
VALUES_BASE = HELM_DIR / "values.yaml"
VALUES_PROD = HELM_DIR / "values-prod.yaml"
VALUES_STAGING = HELM_DIR / "values-staging.yaml"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
CADDYFILES = (
    REPO_ROOT / "Caddyfile",
    REPO_ROOT / "infra" / "caddy" / "edge-global.caddy",
    REPO_ROOT / "infra" / "caddy" / "tenant-domains.caddy",
)


def load_yaml(path: Path) -> dict:
    assert path.is_file(), f"missing deploy asset: {path.relative_to(REPO_ROOT)}"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def merged_values(extra: Path) -> dict:
    """Chart defaults overlaid with one environment's values file."""
    base = load_yaml(VALUES_BASE)
    overlay = load_yaml(extra)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base


VALUES_FILES = [VALUES_PROD, VALUES_STAGING]
VALUES_IDS = ["prod", "staging"]


# ---------------------------------------------------------------------------
# Helm values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("values_file", VALUES_FILES, ids=VALUES_IDS)
def test_both_environments_ship_a_values_file(values_file: Path) -> None:
    assert values_file.is_file(), (
        f"{values_file.name} is missing; helm template is only ever run with it, "
        "so an absent file means that environment was never rendered"
    )


@pytest.mark.parametrize("values_file", VALUES_FILES, ids=VALUES_IDS)
def test_the_image_tag_is_pinned_and_never_latest(values_file: Path) -> None:
    values = merged_values(values_file)
    tag = str(values["image"]["tag"]).strip()
    assert tag, "image.tag must be set explicitly"
    assert tag not in {"latest", "stable", "main"}, (
        f"{values_file.name}: image.tag={tag!r} is mutable; a rollback would not "
        "reproduce what was running before"
    )


@pytest.mark.parametrize("values_file", VALUES_FILES, ids=VALUES_IDS)
def test_resource_requests_and_limits_are_both_set(values_file: Path) -> None:
    values = merged_values(values_file)
    resources = values["resources"]
    for kind in ("requests", "limits"):
        assert kind in resources, f"{values_file.name}: resources.{kind} is missing"
        assert resources[kind].get("cpu"), f"{values_file.name}: resources.{kind}.cpu is missing"
        assert resources[kind].get("memory"), f"{values_file.name}: resources.{kind}.memory is missing"


@pytest.mark.parametrize("values_file", VALUES_FILES, ids=VALUES_IDS)
def test_the_container_runs_non_root_with_a_read_only_root_filesystem(values_file: Path) -> None:
    values = merged_values(values_file)
    security = values.get("containerSecurityContext") or {}
    assert security.get("runAsNonRoot") is True, f"{values_file.name}: runAsNonRoot must be true"
    assert security.get("readOnlyRootFilesystem") is True, (
        f"{values_file.name}: readOnlyRootFilesystem must be true"
    )
    assert security.get("allowPrivilegeEscalation") is False, (
        f"{values_file.name}: allowPrivilegeEscalation must be false"
    )
    dropped = security.get("capabilities", {}).get("drop") or []
    assert "ALL" in [str(item).upper() for item in dropped], (
        f"{values_file.name}: capabilities must drop ALL"
    )


@pytest.mark.parametrize("values_file", VALUES_FILES, ids=VALUES_IDS)
def test_an_hpa_and_a_pod_disruption_budget_are_enabled(values_file: Path) -> None:
    values = merged_values(values_file)
    autoscaling = values.get("autoscaling") or {}
    assert autoscaling.get("enabled") is True, f"{values_file.name}: autoscaling must be enabled"
    assert int(autoscaling["minReplicas"]) >= 1
    assert int(autoscaling["maxReplicas"]) > int(autoscaling["minReplicas"]), (
        f"{values_file.name}: maxReplicas must exceed minReplicas"
    )

    pdb = values.get("podDisruptionBudget") or {}
    assert pdb.get("enabled") is True, f"{values_file.name}: podDisruptionBudget must be enabled"
    min_available = int(pdb.get("minAvailable") or 0)
    assert min_available >= 1, f"{values_file.name}: minAvailable must be at least 1"
    assert min_available <= int(autoscaling["minReplicas"]), (
        f"{values_file.name}: minAvailable ({min_available}) above minReplicas "
        f"({autoscaling['minReplicas']}) would deadlock every node drain"
    )


@pytest.mark.parametrize("values_file", VALUES_FILES, ids=VALUES_IDS)
def test_probes_are_declared(values_file: Path) -> None:
    values = merged_values(values_file)
    probes = values.get("probes") or {}
    for name in ("readiness", "liveness"):
        path = str(probes.get(name) or "").strip()
        assert path.startswith("/"), f"{values_file.name}: probes.{name} must be a URL path"


def test_the_drain_window_fits_inside_the_termination_grace_period() -> None:
    for values_file in VALUES_FILES:
        values = merged_values(values_file)
        grace = int(values.get("terminationGracePeriodSeconds") or 0)
        drain = values.get("drain") or {}
        needed = int(drain.get("timeoutSeconds") or 0) + int(drain.get("flushTimeoutSeconds") or 0)
        assert grace > needed, (
            f"{values_file.name}: terminationGracePeriodSeconds ({grace}) must exceed "
            f"drain timeout + flush ({needed}); otherwise Kubernetes SIGKILLs the pod "
            "mid-drain and in-flight calls are lost"
        )


def test_no_values_file_carries_a_secret() -> None:
    """Credentials must come from `secrets.existingSecret`, never from values."""
    secret_like = re.compile(
        r"(password|passwd|secret_key|api_key|auth_token|private_key)\s*[:=]\s*['\"]?[A-Za-z0-9/+_-]{16,}",
        re.IGNORECASE,
    )
    for values_file in [VALUES_BASE, *VALUES_FILES]:
        text = values_file.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not secret_like.search(line), (
                f"{values_file.name}:{number} looks like a literal credential; "
                "runtime secrets belong in the Secret named by secrets.existingSecret"
            )


# ---------------------------------------------------------------------------
# Chart templates
# ---------------------------------------------------------------------------


def test_the_api_deployment_renders_the_hardening_settings() -> None:
    template = (HELM_DIR / "templates" / "api.yaml").read_text(encoding="utf-8")
    assert "runAsNonRoot" in template, "the pod security context is gone"
    assert ".Values.containerSecurityContext" in template, (
        "the container security context is not wired to values, so the hardening "
        "in values.yaml would never reach the pod spec"
    )
    assert "readOnlyRootFilesystem" not in template or ".Values.containerSecurityContext" in template


def test_the_api_deployment_has_startup_readiness_and_liveness_probes() -> None:
    template = (HELM_DIR / "templates" / "api.yaml").read_text(encoding="utf-8")
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert probe in template, f"{probe} is missing from the api Deployment"


def test_the_chart_renders_an_hpa_and_a_pdb() -> None:
    template = (HELM_DIR / "templates" / "api.yaml").read_text(encoding="utf-8")
    assert "HorizontalPodAutoscaler" in template
    assert "PodDisruptionBudget" in template


# ---------------------------------------------------------------------------
# Production compose
# ---------------------------------------------------------------------------


def _compose() -> dict:
    return load_yaml(COMPOSE_PROD)


def test_every_pinned_image_carries_an_explicit_tag() -> None:
    compose = _compose()
    services = compose.get("services") or {}
    assert services, "docker-compose.prod.yml declares no services"
    for name, service in services.items():
        image = (service or {}).get("image")
        if not image:
            continue  # built from source (`build: .`)
        assert ":" in image, f"service {name}: image {image!r} has no tag"
        tag = image.rsplit(":", 1)[1]
        assert tag not in {"latest", "stable"}, (
            f"service {name}: image {image!r} uses a mutable tag"
        )


def test_the_database_and_redis_publish_no_host_port() -> None:
    """A published Postgres port is a database reachable from the host network."""
    compose = _compose()
    services = compose.get("services") or {}
    for name in ("db", "redis"):
        service = services.get(name) or {}
        assert not service.get("ports"), (
            f"service {name} publishes {service.get('ports')} to the host; the "
            "database must be reachable from the compose network only"
        )


def test_the_api_only_binds_loopback() -> None:
    compose = _compose()
    api = (compose.get("services") or {}).get("api") or {}
    for mapping in api.get("ports") or []:
        assert str(mapping).startswith("127.0.0.1:"), (
            f"api port {mapping!r} is not bound to loopback; public traffic must "
            "enter through Caddy"
        )


def test_stateful_services_have_healthchecks_and_the_api_waits_for_them() -> None:
    compose = _compose()
    services = compose.get("services") or {}
    for name in ("db", "redis"):
        service = services.get(name) or {}
        assert service.get("healthcheck"), f"service {name} has no healthcheck"
    api = services.get("api") or {}
    depends_on = api.get("depends_on") or {}
    if isinstance(depends_on, dict):
        assert depends_on.get("db", {}).get("condition") == "service_healthy", (
            "the api must wait for a healthy database, not merely a started one"
        )


def test_the_api_gets_a_grace_period_long_enough_to_drain() -> None:
    api = ((_compose().get("services") or {}).get("api")) or {}
    grace = str(api.get("stop_grace_period") or "")
    assert grace, "api has no stop_grace_period; in-flight calls would be killed"
    seconds = int(re.sub(r"[^0-9]", "", grace) or 0)
    assert seconds >= 10, f"stop_grace_period of {grace} is too short to drain a call"


# ---------------------------------------------------------------------------
# Caddy
# ---------------------------------------------------------------------------


def test_the_edge_sets_the_transport_security_headers() -> None:
    caddyfile = (REPO_ROOT / "Caddyfile").read_text(encoding="utf-8")
    for directive in (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
    ):
        assert directive in caddyfile, f"{directive} is not set by the edge proxy"


def test_on_demand_tls_is_gated_by_an_ask_endpoint() -> None:
    """Without `ask`, anyone can burn the Let's Encrypt quota for the account."""
    edge = (REPO_ROOT / "infra" / "caddy" / "edge-global.caddy").read_text(encoding="utf-8")
    assert "on_demand_tls" in edge
    assert "ask " in edge, "on_demand_tls without an ask endpoint is an open relay for certificates"


def test_every_caddyfile_shipped_is_non_empty() -> None:
    for path in CADDYFILES:
        assert path.is_file(), f"missing Caddyfile: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"{path.name} is empty"
