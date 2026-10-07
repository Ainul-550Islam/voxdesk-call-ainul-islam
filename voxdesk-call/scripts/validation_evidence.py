"""Pytest evidence plugin: append every phase, never alter test selection or outcome."""
from __future__ import annotations

import json
import os
import resource
import threading
from pathlib import Path


def _write(kind, **values):
    output = os.environ.get("VALIDATION_EVENTS")
    if output:
        with Path(output).open("a") as stream:
            stream.write(json.dumps({"kind": kind, **values}, default=str) + "\n")


def pytest_collection_finish(session):
    _write("collection", nodeids=[item.nodeid for item in session.items])


def pytest_runtest_logreport(report):
    _write(
        "phase", nodeid=report.nodeid, phase=report.when,
        outcome=report.outcome, duration=report.duration,
        failure=str(report.longrepr) if report.failed or report.skipped else None,
        wasxfail=getattr(report, "wasxfail", None),
        rss_peak_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        threads=threading.active_count(),
        open_fds=len(os.listdir("/proc/self/fd")),
    )


def pytest_sessionfinish(session, exitstatus):
    _write("session", exitstatus=int(exitstatus))
