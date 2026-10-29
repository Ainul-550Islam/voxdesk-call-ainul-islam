"""Probe the job registry in a clean interpreter.

pytest imports many modules before a test runs, and importing a handler module
registers it as a side effect. An in-process check therefore cannot tell whether
the bootstrap registers a type on its own. The probe runs ``bootstrap()`` in a
fresh interpreter and reports every ``register_handler_function`` call it saw.
"""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import json
from app.jobs import types as t
seen = []
original = t.register_handler_function
def spy(job_type, *a, **k):
    seen.append(job_type)
    return original(job_type, *a, **k)
t.register_handler_function = spy
from app.jobs.registry import bootstrap
bootstrap()
print("CLOSER_PROBE " + json.dumps({"seen": seen, "registered": sorted(t.registered_job_types())}))
"""


@lru_cache(maxsize=1)
def clean_registry_report() -> dict:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO_ROOT, capture_output=True, text=True, timeout=600
    )
    assert out.returncode == 0, out.stderr[-2000:]
    line = next(line for line in out.stdout.splitlines() if line.startswith("CLOSER_PROBE "))
    return json.loads(line[len("CLOSER_PROBE ") :])
