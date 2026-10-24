"""Pentest-fix regression harness: the outbound-URL guard.

Two corpora run through the production guard: dangerous destinations
(metadata addresses, loopback, IPv6 and decimal forms, userinfo tricks)
that must be refused, and ordinary public URLs that must NOT be refused,
because an over-restrictive guard silently disables integrations.
"""

from __future__ import annotations

from security_harness import models, ssrf_probe


def test_ssrf_corpus_is_refused():
    report = ssrf_probe.run()

    assert report.status == models.PASS, [result.detail for result in report.failures()]
    blocked = next(result for result in report.results if result.name == "ssrf_probe.blocked_destinations")
    assert blocked.evidence["cases"] == len(ssrf_probe.DANGEROUS_URLS)
    assert blocked.evidence["cases"] >= 20


async def test_ssrf_resolution_guard_covers_the_metadata_address():
    report = await ssrf_probe.run_resolution()

    assert report.status in {models.PASS, models.SKIP}
    for result in report.results:
        assert result.status != models.FAIL, result.detail
