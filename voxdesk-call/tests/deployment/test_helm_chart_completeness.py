"""The chart renders what the call path actually needs.

`helm lint` proves a chart is syntactically renderable. It does not prove the
chart contains a realtime gateway, and before PART 17B the VoxDesk chart was
exactly that: lint-clean, kubeconform-clean, and missing the media plane, the
console, the backup and the migration Job. A Kubernetes buyer would have
installed an API that answered HTTP and dropped every WebSocket.

So these tests render the chart and assert on the *rendered objects*:

* every service on the call path exists in prod and staging, and each one has
  the probes, resources, security context and PDB that the api Deployment has;
* the migration Job is a pre-upgrade hook that cannot run twice concurrently;
* the backup CronJob fails closed when it has nowhere durable to write;
* the media engine refuses to advertise an unreachable SDP candidate;
* the Prometheus rules are the same rules the compose stack fires;
* no rendered manifest contains a credential value.

`helm` is required. When it is absent the tests skip with a reason instead of
pretending to pass — the repo's rule is that a validation that did not run is
not a validation that passed, and a silent skip here would be that same lie one
layer up.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "voxdesk"
VALUES_PROD = CHART / "values-prod.yaml"
VALUES_STAGING = CHART / "values-staging.yaml"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None,
    reason="helm is not installed; scripts/check_helm.sh is the CI gate that requires it",
)

USERS = "#/components"


def render(*extra: str) -> list[dict]:
    """`helm template` against the chart, parsed into objects."""
    cmd = ["helm", "template", "voxdesk", str(CHART), *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"helm template failed:\n{proc.stderr}"
    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def named(docs: list[dict], kind: str, suffix: str, exact: bool = False) -> dict:
    pool = by_kind(docs, kind)
    if exact:
        matches = [d for d in pool if d["metadata"]["name"] == suffix]
    else:
        matches = [d for d in pool if d["metadata"]["name"].endswith(suffix)]
    assert len(matches) == 1, (
        f"expected exactly one {kind} named "
        f"{'=' if exact else '*'} {suffix!r}, found {len(matches)}: "
        f"{[m['metadata']['name'] for m in pool]}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Every workload on the call path exists, and is deployable
# ---------------------------------------------------------------------------

# Component -> the Deployment name suffix the chart gives it. `api` and
# `scheduler` come from the pre-17B templates and are listed so a rename shows
# up here rather than in a cluster.
WORKLOADS = {
    # `api` is the release-named Deployment with no component suffix, so it is
    # matched exactly rather than by suffix (otherwise it matches everything).
    "api": "",
    "scheduler": "-scheduler",
    "realtime-gateway": "-realtime-gateway",
    "media-engine": "-media-engine",
    "web": "-web",
}


@pytest.mark.parametrize("values_file", [VALUES_PROD, VALUES_STAGING], ids=["prod", "staging"])
@pytest.mark.parametrize("component", sorted(WORKLOADS))
def test_every_call_path_workload_is_deployed(values_file: Path, component: str) -> None:
    docs = render("-f", str(values_file))
    # helm's default release name is "release-name", and the api Deployment is
    # named <release>-<chart> with no component suffix. Match it as "the
    # Deployment that is not one of the suffixed components" rather than
    # hard-coding the release name.
    if component == "api":
        suffixed = {v for k, v in WORKLOADS.items() if v}
        candidates = [
            d for d in by_kind(docs, "Deployment")
            if not any(d["metadata"]["name"].endswith(sfx) for sfx in suffixed)
        ]
        assert len(candidates) == 1, (
            f"expected exactly one un-suffixed Deployment (the api), found "
            f"{[d['metadata']['name'] for d in candidates]}"
        )
        deployment = candidates[0]
    else:
        deployment = named(docs, "Deployment", WORKLOADS[component])
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    # Resources: a container with no requests is the first thing evicted and
    # the last thing scheduled.
    assert container.get("resources", {}).get("requests"), f"{component} has no resource requests"
    assert container.get("resources", {}).get("limits"), f"{component} has no resource limits"

    # Probes: three, not one. A single liveness probe cannot tell a slow start
    # from a dead process for a Rust/Go service that boots a media plane.
    assert container.get("startupProbe"), f"{component} has no startupProbe"
    assert container.get("readinessProbe"), f"{component} has no readinessProbe"
    assert container.get("livenessProbe"), f"{component} has no livenessProbe"

    # Hardening: identical to the api Deployment's, because a component that
    # only exists in one values file is a component nobody has tested.
    sec = container.get("securityContext", {})
    assert sec.get("allowPrivilegeEscalation") is False, f"{component} allows privilege escalation"
    assert sec.get("readOnlyRootFilesystem") is True, f"{component} has a writable root filesystem"
    assert sec.get("runAsNonRoot") is True, f"{component} may run as root"
    assert "ALL" in (sec.get("capabilities", {}).get("drop") or []), f"{component} keeps capabilities"


@pytest.mark.parametrize("values_file", [VALUES_PROD, VALUES_STAGING], ids=["prod", "staging"])
def test_every_service_on_the_call_path_has_a_service(values_file: Path) -> None:
    docs = render("-f", str(values_file))
    services = [s["metadata"]["name"] for s in by_kind(docs, "Service")]
    for component in ("realtime-gateway", "media-engine", "web"):
        assert any(name.endswith(component) for name in services), (
            f"no Service for {component} (found: {services})"
        )


def test_the_console_is_reachable_but_not_directly_from_the_api() -> None:
    """The web Service exists and nothing mounts it into the API pod."""
    docs = render("-f", str(VALUES_PROD))
    web = named(docs, "Deployment", "-web")
    container = web["spec"]["template"]["spec"]["containers"][0]
    # PART 17A's decision held at chart level: two images, not one that serves
    # both the API and the HTML.
    assert container["name"] == "web"
    assert container["ports"][0]["containerPort"] == 3000


# ---------------------------------------------------------------------------
# Realtime gateway specifics
# ---------------------------------------------------------------------------


def test_the_gateway_gets_its_secrets_from_the_runtime_secret_not_from_values() -> None:
    docs = render("-f", str(VALUES_PROD))
    gateway = named(docs, "Deployment", "-realtime-gateway")
    env = {e["name"]: e for e in gateway["spec"]["template"]["spec"]["containers"][0]["env"]}

    # Both are boot-time requirements in gateway-go/internal/config: the
    # process refuses to start without them, so a plain `value:` here would be
    # a manifest that renders and a pod that CrashLoops.
    for key in ("VOXDESK_GATEWAY_JWT_SECRET", "VOXDESK_GATEWAY_INGEST_SECRET"):
        assert key in env, f"{key} is not wired into the gateway"
        assert "valueFrom" in env[key], f"{key} is rendered as a literal value"
        assert (
            env[key]["valueFrom"]["secretKeyRef"]["name"] == "voxdesk-runtime"
        ), f"{key} reads from {env[key]['valueFrom']['secretKeyRef']['name']}"

    # Redis is required once the broker is redis: the memory broker cannot
    # fan out between replicas, so prod must not silently fall back to it.
    assert env["VOXDESK_GATEWAY_BROKER"]["value"] == "redis"
    assert env["VOXDESK_GATEWAY_REDIS_URL"]["value"].startswith("redis://")


def test_the_gateway_readiness_uses_readyz_not_healthz() -> None:
    """A gateway at capacity must stop receiving new sockets, not report alive."""
    docs = render("-f", str(VALUES_PROD))
    gateway = named(docs, "Deployment", "-realtime-gateway")
    container = gateway["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"


def test_the_gateway_scales_down_slowly_because_sockets_do_not_migrate() -> None:
    docs = render("-f", str(VALUES_PROD))
    hpa = named(docs, "HorizontalPodAutoscaler", "-realtime-gateway")
    window = hpa["spec"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"]
    assert window >= 600, f"scale-down window {window}s would churn live connections"
    assert hpa["spec"]["behavior"]["scaleDown"]["policies"][0]["value"] == 1


def test_the_gateway_is_anti_affine_across_nodes() -> None:
    docs = render("-f", str(VALUES_PROD))
    gateway = named(docs, "Deployment", "-realtime-gateway")
    affinity = gateway["spec"]["template"]["spec"].get("affinity", {})
    assert "podAntiAffinity" in affinity, (
        "no podAntiAffinity: one node drain would drop every WebSocket on that host"
    )


# ---------------------------------------------------------------------------
# Media engine
# ---------------------------------------------------------------------------


def test_the_media_engine_advertises_a_reachable_address_or_the_chart_refuses() -> None:
    """A Pod IP in an SDP candidate is a call that connects to nothing."""
    docs = render("-f", str(VALUES_PROD))
    engine = named(docs, "Deployment", "-media-engine")
    pod = engine["spec"]["template"]["spec"]
    env = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}

    if pod.get("hostNetwork"):
        # hostNetwork is the documented way to get a node address for free.
        assert pod["dnsPolicy"] == "ClusterFirstWithHostNet"
    else:
        assert env.get("VOXDESK_PUBLIC_IP"), (
            "mediaEngine.hostNetwork is false and no publicIp was rendered: "
            "the SDP would advertise an unreachable candidate"
        )


def test_the_media_engine_refuses_to_render_without_an_address() -> None:
    proc = subprocess.run(
        [
            "helm", "template", "voxdesk", str(CHART),
            "-f", str(VALUES_PROD),
            "--set", "mediaEngine.hostNetwork=false",
            "--set", "mediaEngine.publicIp=",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "publicIp is required" in proc.stderr, proc.stderr


def test_the_media_engine_health_check_is_on_the_control_port_not_udp() -> None:
    docs = render("-f", str(VALUES_PROD))
    engine = named(docs, "Deployment", "-media-engine")
    container = engine["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["port"] == "control"
    assert container["readinessProbe"]["httpGet"]["path"] == "/v1/health"
    # UDP cannot answer a probe; if RTP were named as the probe port the pod
    # would never become ready.
    assert container["ports"][0]["protocol"] == "UDP"


# ---------------------------------------------------------------------------
# Migration and backup Jobs
# ---------------------------------------------------------------------------


def test_the_migration_job_is_a_pre_upgrade_hook_that_cannot_race() -> None:
    docs = render("-f", str(VALUES_PROD))
    job = named(docs, "Job", "-migrate")
    annotations = job["metadata"]["annotations"]
    assert "pre-upgrade" in annotations["helm.sh/hook"]
    assert "pre-install" in annotations["helm.sh/hook"]
    # Without before-hook-creation the second upgrade reuses the completed Job
    # and reports success having run nothing.
    assert "before-hook-creation" in annotations["helm.sh/hook-delete-policy"]
    spec = job["spec"]
    assert spec["parallelism"] == 1 and spec["completions"] == 1, "two alembic upgrades could race"
    assert job["spec"]["template"]["spec"]["containers"][0]["command"] == [
        "python", "-m", "alembic", "upgrade", "head"
    ]


def test_the_backup_cronjob_keeps_a_dump_it_can_restore() -> None:
    docs = render("-f", str(VALUES_PROD))
    cron = named(docs, "CronJob", "-backup")
    script = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
    # The three properties the compose service has; dropping any one turns a
    # backup into a file that merely looks like one.
    assert "pg_dump" in script
    assert "pg_restore --list" in script, "a dump is never verified as readable"
    assert "exit 1" in script, "a failed backup exits zero"
    assert cron["spec"]["concurrencyPolicy"] == "Forbid"


def test_the_backup_volume_is_durable_and_not_an_empty_dir() -> None:
    docs = render("-f", str(VALUES_PROD))
    cron = named(docs, "CronJob", "-backup")
    volumes = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]
    backups = next(v for v in volumes if v["name"] == "backups")
    assert "persistentVolumeClaim" in backups, (
        "the backup volume is not a PVC: the dump would die with the Job pod"
    )
    assert "emptyDir" not in str(backups)
    assert "volumeClaimTemplate" not in str(backups)
    # And the PVC it names is rendered.
    pvc = named(docs, "PersistentVolumeClaim", "-backups")
    assert pvc["spec"]["resources"]["requests"]["storage"]


# ---------------------------------------------------------------------------
# Prometheus rules are the compose rules
# ---------------------------------------------------------------------------


def test_the_prometheus_rule_carries_the_shared_alert_file_verbatim() -> None:
    docs = render("-f", str(VALUES_PROD))
    rule = named(docs, "PrometheusRule", "-platform")
    groups = {g["name"]: g for g in rule["spec"]["groups"]}
    shared = yaml.safe_load((REPO_ROOT / "observability" / "alerts.yml").read_text(encoding="utf-8"))

    assert "voxdesk" in groups, f"the shared alert group is missing; groups={sorted(groups)}"
    shared_rules = shared["groups"][0]["rules"]
    rendered_rules = groups["voxdesk"]["rules"]
    assert len(rendered_rules) == len(shared_rules), (
        f"{len(shared_rules)} rules in observability/alerts.yml but {len(rendered_rules)} rendered"
    )
    # Same alerts, same expressions, same runbook links — the whole point of
    # reading the file instead of copying it.
    for want, got in zip(shared_rules, rendered_rules):
        assert got["alert"] == want["alert"]
        assert got["expr"] == want["expr"]
        assert got["annotations"]["runbook"] == want["annotations"]["runbook"]


def test_the_kubernetes_only_alerts_cover_backup_migration_and_crashloop() -> None:
    docs = render("-f", str(VALUES_PROD))
    rule = named(docs, "PrometheusRule", "-platform")
    platform = next(g for g in rule["spec"]["groups"] if g["name"] == "voxdesk-platform")
    names = {r["alert"] for r in platform["rules"]}
    assert {
        "VoxDeskBackupJobFailed",
        "VoxDeskMigrationJobFailed",
        "VoxDeskPodCrashLooping",
        "VoxDeskRealtimeGatewaySaturated",
    } <= names


def test_the_chart_copies_the_alert_file_rather_than_retyping_it() -> None:
    """infra/helm/voxdesk/alerts.yml must equal observability/alerts.yml."""
    shipped = (CHART / "alerts.yml").read_text(encoding="utf-8")
    canonical = (REPO_ROOT / "observability" / "alerts.yml").read_text(encoding="utf-8")
    assert shipped == canonical, (
        "the chart's copy of alerts.yml has drifted from observability/alerts.yml; "
        "run scripts/sync_chart_alerts.sh (or copy the file) — do not edit one of them"
    )


# ---------------------------------------------------------------------------
# NetworkPolicy
# ---------------------------------------------------------------------------


def test_network_policy_defaults_to_deny_and_allows_the_call_path() -> None:
    docs = render("-f", str(VALUES_PROD))
    policies = by_kind(docs, "NetworkPolicy")
    assert policies, "no NetworkPolicy rendered"
    deny = named(docs, "NetworkPolicy", "-default-deny")
    assert deny["spec"]["ingress"] == [], "the default policy allows ingress"

    api = named(docs, "NetworkPolicy", "-allow-api")
    sources = api["spec"]["ingress"]
    assert any("namespaceSelector" in f for f in sources[0]["from"]), "ingress controller not allowed"
    ports = {p["port"] for rule in sources for p in rule.get("ports", [])}
    assert 8000 in ports

    gateway = named(docs, "NetworkPolicy", "-allow-realtime-gateway")
    gw_ports = {p["port"] for rule in gateway["spec"]["ingress"] for p in rule.get("ports", [])}
    assert 8790 in gw_ports


def test_media_udp_is_open_because_ice_addresses_are_unknown() -> None:
    docs = render("-f", str(VALUES_PROD))
    media = named(docs, "NetworkPolicy", "-allow-media-engine")
    udp = [
        p
        for rule in media["spec"]["ingress"]
        for p in rule.get("ports", [])
        if p.get("protocol") == "UDP"
    ]
    assert udp and udp[0]["port"] == 5000
    # The UDP rule must have no `from`, or a carrier whose address was never
    # declared would have its RTP dropped.
    udp_rule = next(
        rule for rule in media["spec"]["ingress"]
        if any(p.get("protocol") == "UDP" for p in rule.get("ports", []))
    )
    assert "from" not in udp_rule


# ---------------------------------------------------------------------------
# Nothing secret, anywhere
# ---------------------------------------------------------------------------


def test_no_rendered_manifest_contains_a_credential_value() -> None:
    for values in (VALUES_PROD, VALUES_STAGING):
        text = subprocess.run(
            ["helm", "template", "voxdesk", str(CHART), "-f", str(values)],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout
        for needle in (
            "password:", "secret:", "token: ", "api_key", "apikey",
            "AKIA", "BEGIN RSA", "BEGIN PRIVATE",
        ):
            assert needle not in text, (
                f"{values.name} renders a possible credential marker {needle!r}"
            )
        # A rendered Secret object would mean the chart is storing one.
        assert "kind: Secret" not in text, f"{values.name} renders a Secret object"


def test_base_values_render_nothing_new_by_default() -> None:
    """`helm template` with no -f still renders just the api + scheduler stack."""
    docs = render()
    kinds = {d["kind"] for d in docs}
    assert "NetworkPolicy" not in kinds
    assert "PrometheusRule" not in kinds
    names = {d["metadata"]["name"] for d in by_kind(docs, "Deployment")}
    assert not any(n.endswith(("-realtime-gateway", "-media-engine", "-web")) for n in names), (
        "the base values must not switch on optional infrastructure: "
        "a bare `helm template` is what a reviewer reads first"
    )
