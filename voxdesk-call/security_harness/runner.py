"""Run the security suites and write a hashed report.

Two entry points:

* :func:`run_all` — library use (the test suite, the evidence collector);
* :func:`main` — ``python -m security_harness.runner``, which is what CI runs.

Both write the same JSON document to ``evidence/security/security-harness-<stamp>.json``
with a sibling ``index.json`` of digests, so a later reader can tell whether the
artefact still matches what the run produced.

Exit codes: 0 clean, 1 a FAIL/ERROR, 2 a REVIEW that a human must look at when
``--strict`` is passed. A SKIP is never a failure, and never a pass either —
:class:`HarnessReport` counts it separately.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence

from security_harness import (
    authz_matrix,
    cross_tenant_fuzz,
    headers_and_cors,
    rate_limit_probe,
    ssrf_probe,
)
from security_harness import report_json
from security_harness.models import FAIL, REVIEW, HarnessReport

DEFAULT_EVIDENCE_DIR = Path("evidence/security")


def _merge(suite_reports: Sequence[HarnessReport], name: str) -> HarnessReport:
    combined = HarnessReport(suite=name)
    for report in suite_reports:
        for result in report.results:
            combined.add(result)
    return combined


def run_all(
    *,
    app: Any = None,
    client: Any = None,
    tenant_client: Any = None,
    foreign_tenant_id: str = "",
    uses_https: bool = False,
) -> dict[str, HarnessReport]:
    """Run every suite this invocation can support.

    ``client`` is a synchronous test client (headers/CORS/rate limits);
    ``tenant_client`` is an ``httpx.AsyncClient`` already authenticated as one
    tenant, used for the cross-tenant probes. Anything not supplied produces
    SKIP results rather than silent passes.
    """
    reports: dict[str, HarnessReport] = {}

    if app is not None:
        reports["authz_matrix"] = authz_matrix.run(app)
    else:
        reports["authz_matrix"] = HarnessReport(suite="authz_matrix")
        reports["authz_matrix"].record("authz_matrix.application", "SKIP", "no application supplied")

    reports["ssrf"] = ssrf_probe.run()
    reports["ssrf_resolution"] = _run_coroutine(ssrf_probe.run_resolution())
    reports["security_headers"] = headers_and_cors.run(app, client, uses_https=uses_https)
    reports["rate_limits"] = rate_limit_probe.run(app, live_client=client)

    if app is not None and tenant_client is not None and foreign_tenant_id:
        reports["cross_tenant"] = _run_coroutine(
            cross_tenant_fuzz.run(app, tenant_client, foreign_tenant_id=foreign_tenant_id)
        )
    else:
        reports["cross_tenant"] = HarnessReport(suite="cross_tenant")
        reports["cross_tenant"].record(
            "cross_tenant_fuzz.execution",
            "SKIP",
            "no authenticated tenant client or foreign tenant id was supplied",
        )
    return reports


def _run_coroutine(coro: Any) -> HarnessReport:
    import asyncio

    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def combined_report(reports: dict[str, HarnessReport]) -> HarnessReport:
    return _merge(list(reports.values()), "security-harness")


def write_report(report: HarnessReport, *, directory: Path | None = None) -> dict[str, Any]:
    """Write the report and its digest index. Returns the written metadata."""
    return report_json.write(report, directory=directory or DEFAULT_EVIDENCE_DIR)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the VoxDesk security regression harness")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat REVIEW findings as a non-zero exit (for a release gate)",
    )
    parser.add_argument(
        "--app",
        action="store_true",
        help="import the FastAPI application to run the route-level suites",
    )
    parser.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE_DIR))
    args = parser.parse_args(argv)

    app = None
    if args.app:
        from app.main import app as fastapi_app

        app = fastapi_app

    reports = run_all(app=app)
    combined = combined_report(reports)
    written = write_report(combined, directory=Path(args.evidence_dir))

    for report in reports.values():
        print(f"[{report.status}] {report.suite}: {report.by_status()}")
        for result in report.results:
            if result.status in (FAIL, REVIEW):
                print(f"    {result.status:<6} {result.name}: {result.detail}")
    print(f"report: {written['path']} sha256={written['sha256']}")

    if combined.status in (FAIL, "ERROR"):
        return 1
    if args.strict and combined.status == REVIEW:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
