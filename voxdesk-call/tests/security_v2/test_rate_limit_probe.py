"""Pentest-fix regression harness: rate limits and lockout.

A limiter that is not mounted cannot be observed, so the probe reports
SKIP with a reason rather than a pass. These tests hold it to that rule
and keep the live 429 observation honest.
"""

from __future__ import annotations

from security_harness import models, rate_limit_probe


def test_rate_limit_suite_never_claims_a_pass_it_did_not_earn(app):
    report = rate_limit_probe.run(app)

    assert report.status in {models.PASS, models.REVIEW, models.SKIP}
    live = [result for result in report.results if "live_429" in result.name]
    assert live, "the live-429 probe must be reported, even when skipped"
    for result in live:
        assert result.status in {models.PASS, models.SKIP, models.REVIEW}
        if result.status == models.SKIP:
            assert result.detail, "a skip without a reason hides a gap"


def test_rate_limit_suite_reports_a_missing_application_as_skip():
    report = rate_limit_probe.run(None)

    middleware = next(result for result in report.results if result.name == "rate_limit.middleware")
    assert middleware.status == models.SKIP
