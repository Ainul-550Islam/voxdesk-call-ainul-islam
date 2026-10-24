"""Pentest-fix regression harness: response headers and CORS.

The assertions are made against real responses from the mounted
application, and a run without a client reports SKIP: a header suite that
passes because it never sent a request would be worthless.
"""

from __future__ import annotations


from security_harness import headers_and_cors, models



def _names(report: models.HarnessReport) -> set[str]:
    return {result.name for result in report.results}


def test_headers_suite_passes_on_the_real_app(app, sync_client):
    report = headers_and_cors.run(app, sync_client)

    assert report.status in {models.PASS, models.REVIEW}
    assert report.failures() == []
    assert any(name.startswith("headers_and_cors.") for name in _names(report))
    assert any(name.startswith("cors.") for name in _names(report))


def test_headers_suite_skips_instead_of_passing_without_a_client(app):
    report = headers_and_cors.run(app, None)

    assert report.status == models.SKIP
    assert all(result.status == models.SKIP for result in report.results)
