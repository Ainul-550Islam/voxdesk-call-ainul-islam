"""The harness's own reporting contract.

Each suite produces a status, and the combined report is the worst of them. The
rules that keep the numbers honest live here: an unsupplied suite is SKIP and
never PASS, a FAIL exits non-zero while a REVIEW does not (a human still has to
look, but the build is not wrong), and every written report is appended to a
digest index so the history cannot be rewritten.
"""

from __future__ import annotations

import hashlib
import json

from security_harness import (
    authz_matrix,
    headers_and_cors,
    models,
    rate_limit_probe,
    runner,
    ssrf_probe,
)


def test_run_all_marks_every_unsupplied_suite_as_skip():
    reports = runner.run_all()

    assert set(reports) == {
        "authz_matrix",
        "ssrf",
        "ssrf_resolution",
        "security_headers",
        "rate_limits",
        "cross_tenant",
    }
    for report in reports.values():
        assert report.status in {models.SKIP, models.PASS, models.REVIEW}


def test_combined_report_is_the_worst_case(tmp_path, app, sync_client):
    reports = {
        "authz_matrix": authz_matrix.run(app),
        "security_headers": headers_and_cors.run(app, sync_client),
        "ssrf": ssrf_probe.run(),
        "rate_limits": rate_limit_probe.run(app),
    }

    combined = runner.combined_report(reports)

    assert combined.status == models.worst([report.status for report in reports.values()])
    assert set(combined.by_status()) == {"ERROR", "FAIL", "REVIEW", "SKIP", "PASS"}
    written = runner.write_report(combined, directory=tmp_path)
    payload = json.loads((tmp_path / written["file"]).read_text())
    assert payload["status"] == combined.status
    index = json.loads((tmp_path / "index.json").read_text())
    assert index[-1]["sha256"] == written["sha256"]
    assert len(written["sha256"]) == 64


def test_report_exit_code_distinguishes_a_failure_from_a_review():
    failing = models.HarnessReport(suite="synthetic")
    failing.record("probe", models.FAIL, "injected")
    reviewing = models.HarnessReport(suite="synthetic")
    reviewing.record("probe", models.REVIEW, "needs a human")
    skipped = models.HarnessReport(suite="synthetic")
    skipped.record("probe", models.SKIP, "cannot run here")

    assert failing.exit_code == 1
    assert reviewing.exit_code == 0
    assert skipped.exit_code == 0


def test_write_report_appends_to_the_digest_index(tmp_path):
    first = models.HarnessReport(suite="run-1")
    first.record("probe", models.PASS, "fine")
    second = models.HarnessReport(suite="run-2")
    second.record("probe", models.REVIEW, "needs a human")

    runner.write_report(first, directory=tmp_path)
    runner.write_report(second, directory=tmp_path)

    index = json.loads((tmp_path / "index.json").read_text())
    assert len(index) == 2
    assert [entry["status"] for entry in index] == ["PASS", "REVIEW"]
    # Every indexed digest names a report file whose bytes still hash to it.
    for entry in index:
        body = (tmp_path / entry["file"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
