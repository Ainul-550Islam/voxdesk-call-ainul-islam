"""The Helm CronJobs must say the same thing as the code.

Two definitions of "how often the rota rollup runs" is how a runbook stops being
true, so this module reads the chart text and compares it with the actual
constants:

* one CronJob per scheduler cycle, no cycle left out and no job for a cycle that
  does not exist;
* each cron expression matches the interval the module declares;
* `concurrencyPolicy: Forbid` on every job — two rollups over the same hour fight
  over the same interval rows, and two solver runs write competing versions;
* the `PLAT2_*` defaults in the template match `app/platform_v2/settings.py`, so a
  deployment that configures nothing behaves exactly like the code's defaults.

When the `helm` binary is present the chart is also rendered and the same
invariants are checked on the output; without it the text checks still run, and
the render step skips rather than pretending.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.platform_v2 import jobs as platform_jobs
from app.platform_v2.jobs import (
    ADHERENCE_INTERVAL_SECONDS,
    CERT_REFRESH_INTERVAL_SECONDS,
    DOMAIN_REVERIFY_INTERVAL_SECONDS,
    FORECAST_INTERVAL_SECONDS,
    LOOP_NAMES,
    PUSH_RETRY_INTERVAL_SECONDS,
    ROLLUP_INTERVAL_SECONDS,
    SCORE_INTERVAL_SECONDS,
    SENDER_CHECK_INTERVAL_SECONDS,
    TOKEN_GC_INTERVAL_SECONDS,
)
from app.platform_v2.settings import PlatformV2Settings

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "helm" / "voxdesk" / "templates" / "wfm-and-platform-cronjobs.yaml"
VALUES = ROOT / "infra" / "helm" / "voxdesk" / "values.yaml"

#: Cycle name -> the interval the scheduler registers it with.
CADENCE = {
    "wfm_rollup": ROLLUP_INTERVAL_SECONDS,
    "wfm_forecast": FORECAST_INTERVAL_SECONDS,
    "wfm_forecast_score": SCORE_INTERVAL_SECONDS,
    "wfm_adherence": ADHERENCE_INTERVAL_SECONDS,
    "wl_domain_reverify": DOMAIN_REVERIFY_INTERVAL_SECONDS,
    "wl_cert_refresh": CERT_REFRESH_INTERVAL_SECONDS,
    "wl_sender_domain": SENDER_CHECK_INTERVAL_SECONDS,
    "mobile_push_retry": PUSH_RETRY_INTERVAL_SECONDS,
    "mobile_token_gc": TOKEN_GC_INTERVAL_SECONDS,
}

JOB_LINE = re.compile(
    r'dict "name" "(?P<name>[\w-]+)" "module" "(?P<module>[\w.]+)" '
    r'"args" \(list "(?P<cycle>\w+)"\) "schedule" "(?P<schedule>[^"]+)"'
)


def template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def declared_jobs() -> list[dict[str, str]]:
    return [match.groupdict() for match in JOB_LINE.finditer(template_text())]


def cron_matches_interval(expression: str, seconds: float) -> bool:
    """Does this five-field cron fire once every ``seconds``?"""
    minute, hour, day_of_month, month, day_of_week = expression.split()
    daily = seconds >= 86_400
    hourly = 3_600 <= seconds < 86_400
    if seconds <= 60:
        return False
    if daily:
        return (
            re.fullmatch(r"\d+", minute) is not None
            and re.fullmatch(r"\d+", hour) is not None
            and day_of_month == "*"
            and month == "*"
            and day_of_week == "*"
        )
    if hourly:
        # A fixed minute of every hour, or every N hours with a fixed minute.
        hour_ok = hour == "*" or re.fullmatch(r"\*/\d+", hour) is not None
        if hour.startswith("*/"):
            return re.fullmatch(r"\d+", minute) is not None and _hours_match(hour, seconds)
        return re.fullmatch(r"\d+", minute) is not None and hour_ok
    minutes = seconds / 60
    if minutes >= 60:
        return False
    # Sub-hourly: */N minutes, and the hour field may only restrict to a fixed hour.
    match = re.fullmatch(r"\*/(\d+)", minute)
    if match is None:
        return False
    return int(match.group(1)) == int(minutes) and (
        hour == "*" or re.fullmatch(r"\d+", hour) is not None
    )


def _hours_match(hour_field: str, seconds: float) -> bool:
    step = int(hour_field.split("/")[1])
    return step * 3600 == seconds


# --- the jobs ------------------------------------------------------------------


def test_the_template_declares_every_cycle_exactly_once():
    cycles = [job["cycle"] for job in declared_jobs()]
    assert len(cycles) == len(set(cycles)), "a cycle is declared twice"
    assert set(cycles) == set(LOOP_NAMES)
    assert set(cycles) == set(platform_jobs.CYCLES), "CYCLES and LOOP_NAMES disagree"


def test_every_job_runs_the_platform_jobs_entrypoint():
    assert {job["module"] for job in declared_jobs()} == {"scripts.platform_jobs"}


def test_every_schedule_matches_its_declared_interval():
    wrong = [
        (job["cycle"], job["schedule"], CADENCE[job["cycle"]])
        for job in declared_jobs()
        if not cron_matches_interval(job["schedule"], CADENCE[job["cycle"]])
    ]
    assert wrong == [], "these CronJob schedules disagree with app/platform_v2/jobs.py"


def test_the_cron_matcher_rejects_a_schedule_that_would_drift():
    """The matcher is the guard, so it is itself guarded."""
    assert cron_matches_interval("5 * * * *", 3600.0) is True
    assert cron_matches_interval("5 */2 * * *", 3600.0) is False
    assert cron_matches_interval("*/5 * * * *", 300.0) is True
    assert cron_matches_interval("*/10 * * * *", 300.0) is False
    assert cron_matches_interval("0 4 * * *", 86_400.0) is True
    assert cron_matches_interval("0 4 * * 1", 86_400.0) is False
    assert cron_matches_interval("*/30 * * * *", 1800.0) is True
    assert cron_matches_interval("0 * * * *", 900.0) is False


def test_no_two_jobs_share_a_name():
    names = [job["name"] for job in declared_jobs()]
    assert len(names) == len(set(names))


def test_every_job_is_forbidden_from_overlapping():
    text = template_text()
    assert "concurrencyPolicy: Forbid" in text
    # One occurrence inside the range covers every job; the second mention in the
    # file is the comment that explains why, which is not a spec field.
    assert text.count("\n  concurrencyPolicy: Forbid") == 1


def test_a_job_that_would_fight_itself_is_also_bounded():
    text = template_text()
    assert "activeDeadlineSeconds" in text
    assert "backoffLimit: 1" in text
    assert "restartPolicy: Never" in text


def test_the_chart_renders_with_no_platform_block():
    """Everything has a default, so an operator can opt in without a values file."""
    text = template_text()
    assert '$platform := .Values.platform | default dict' in text
    assert '$jobs := $platform.jobs | default (list' in text


def test_the_values_file_does_not_duplicate_the_default_job_list():
    """``jobs: []`` means "unset": a copy of the list would rot in two places."""
    values = VALUES.read_text(encoding="utf-8")
    assert re.search(r"^\s+jobs: \[\]\s*$", values, re.MULTILINE)


# --- settings parity -----------------------------------------------------------


def template_env_defaults() -> dict[str, str]:
    """``ENV_NAME -> the literal the template falls back to``."""
    block = template_text().split("env:", 1)[1]
    found: dict[str, str] = {}
    pattern = re.compile(
        r"- name: (?P<name>[A-Z0-9_]+)\n\s+value: (?P<value>[^\n]+)"
    )
    for match in pattern.finditer(block):
        name, value = match.group("name"), match.group("value").strip()
        fallback = re.search(r"\|\s*default\s+(?P<literal>.+?)\s*\|\s*quote", value)
        if fallback is None:
            # Booleans are read with `dig` so that an explicit false wins over the
            # fallback; `dig "key" <fallback> $map` is the same default, spelled so
            # it can be turned off.
            fallback = re.search(r'dig\s+"[\w]+"\s+(?P<literal>\S+)\s+\$', value)
        if fallback is None:
            continue
        literal = fallback.group("literal").strip().strip('"')
        if literal.startswith("$") or literal.startswith("(") or literal.startswith("printf"):
            continue
        found[name] = literal
    return found


#: The one deliberate difference: the chart mounts the asset volume at an absolute
#: path, the code defaults to a path relative to the working directory.
ALLOWED_DIVERGENCE = {"BRANDING_ASSET_DIR"}


def test_template_env_defaults_match_the_settings_model():
    fields = PlatformV2Settings.model_fields
    by_alias = {
        str(field.alias): (name, field) for name, field in fields.items() if field.alias
    }
    mismatches = []
    for env_name, literal in template_env_defaults().items():
        if env_name in ALLOWED_DIVERGENCE or env_name in {"APP_ENV", "DATABASE_URL", "REDIS_URL"}:
            continue
        entry = by_alias.get(env_name)
        if entry is None:
            mismatches.append((env_name, literal, "no such setting"))
            continue
        default = entry[1].default
        if isinstance(default, bool):
            same = literal.lower() == str(default).lower()
        elif isinstance(default, (int, float)):
            try:
                same = float(literal) == float(default)
            except ValueError:
                same = False
        else:
            same = literal == str(default)
        if not same:
            mismatches.append((env_name, literal, default))
    assert mismatches == [], f"Helm defaults drifted from settings.py: {mismatches}"


def test_every_scheduler_setting_the_jobs_need_is_passed():
    """A credential the passes read but the pod never receives is a silent no-op."""
    names = set(template_env_defaults())
    required = {
        "WFM_INTERVAL_MINUTES",
        "WFM_TIMEZONE",
        "CUSTOM_DOMAIN_CNAME_TARGET",
        "TLS_ASK_ENABLED",
        "FCM_ENABLED",
        "APNS_ENABLED",
        "PUSH_TOKEN_GC_DAYS",
        "MOBILE_AUTH_PER_MINUTE",
    }
    assert required <= names
    # The secrets (SMTP, provider keys, TLS ask token) ride `envFrom` from the one
    # existing Secret rather than being repeated per job.
    assert "envFrom:" in template_text()


def test_api_uses_the_configured_irsa_service_account_and_s3_asset_settings():
    api_template = (ROOT / "infra" / "helm" / "voxdesk" / "templates" / "api.yaml").read_text(
        encoding="utf-8"
    )
    service_account_template = (
        ROOT / "infra" / "helm" / "voxdesk" / "templates" / "serviceaccount.yaml"
    ).read_text(encoding="utf-8")
    values = (ROOT / "infra" / "helm" / "voxdesk" / "values.yaml").read_text(
        encoding="utf-8"
    )
    assert 'serviceAccountName: {{ include "voxdesk.serviceAccountName" . | quote }}' in api_template
    assert 'include "voxdesk.serviceAccountName" .' in service_account_template
    assert '"eks.amazonaws.com/role-arn"' in service_account_template or "toYaml . | nindent 4" in service_account_template
    assert "automountServiceAccountToken: true" in service_account_template
    for setting in (
        "BRANDING_ASSET_DIR",
        "BRANDING_ASSET_S3_BUCKET",
        "BRANDING_ASSET_S3_REGION",
        "BRANDING_ASSET_S3_KMS_KEY_ID",
    ):
        assert f"name: {setting}" in api_template
    assert "name: voxdesk-platform" in values
    assert 'brandingAssetBucket: ""' in values


# --- rendered chart ------------------------------------------------------------


def _render(*extra: str) -> str:
    completed = subprocess.run(
        ["helm", "template", "voxdesk", str(ROOT / "infra" / "helm" / "voxdesk"), *extra],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return completed.stdout


HELM = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed here")


@HELM
def test_the_rendered_chart_has_one_cronjob_per_cycle():
    rendered = _render()
    schedules = re.findall(r"schedule: \"([^\"]+)\"", rendered)
    for job in declared_jobs():
        assert job["schedule"] in schedules, f"{job['cycle']} does not render its schedule"
    # Every rendered platform pod runs the same entrypoint with a cycle argument.
    for job in declared_jobs():
        assert f'- "{job["cycle"]}"' in rendered, job["cycle"]


@HELM
def test_the_chart_can_be_turned_off():
    enabled = _render().count("kind: CronJob")
    disabled = _render("--set", "platform.enabled=false").count("kind: CronJob")
    # The chart renders other CronJobs of its own; switching the platform block off
    # must remove exactly the nine this template owns.
    assert enabled - disabled == len(LOOP_NAMES)
