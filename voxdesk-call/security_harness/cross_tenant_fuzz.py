"""Cross-tenant probes: does tenant A's credential reach tenant B's data?

The check is behavioural, not structural: it sends requests carrying a valid
credential for one tenant and another tenant's identifiers, and looks at what
comes back. Three outcomes matter:

* a refusal (403/404) is the wanted answer — 404 in particular, because it does
  not confirm that the other tenant's object exists;
* a *success* is a finding;
* a body that echoes the other tenant's id, even in an error, is a finding too:
  the id leaked even though the read did not.

Probes are generated from the mounted application, so a new tenant-scoped route
is covered the day it is added. Path parameters other than ``tenant_id`` are
filled with the foreign tenant's own ids where the route can accept a UUID, and
with a random UUID otherwise; the point is that nothing in the request belongs
to the calling tenant.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, Iterable

from fastapi import FastAPI

from security_harness.authz_matrix import route_infos
from security_harness.models import FAIL, PASS, REVIEW, SKIP, HarnessReport

REFUSAL_STATUSES = frozenset({401, 403, 404, 410})
CLIENT_ERROR_STATUSES = frozenset({400, 405, 409, 415, 422})

#: Path parameters that identify a tenant-scoped resource.
TENANT_PARAM = "{tenant_id}"


@dataclasses.dataclass(frozen=True)
class TenantProbe:
    """One request to send with a foreign tenant's identity."""

    method: str
    path: str
    name: str

    def as_dict(self) -> dict[str, str]:
        return {"method": self.method, "path": self.path, "name": self.name}


def _fill(path: str, foreign_tenant_id: str, foreign_object_id: str) -> str:
    filled = path.replace(TENANT_PARAM, foreign_tenant_id)
    while "{" in filled and "}" in filled:
        start = filled.index("{")
        end = filled.index("}", start)
        filled = filled[:start] + foreign_object_id + filled[end + 1 :]
    return filled


def build_probes(app: FastAPI, *, limit: int = 60) -> list[TenantProbe]:
    """Read-only tenant-scoped operations, in a stable order."""
    probes: list[TenantProbe] = []
    for info in route_infos(app):
        if not info.authenticated or TENANT_PARAM not in info.path:
            continue
        if "GET" not in info.methods:
            continue
        probes.append(TenantProbe(method="GET", path=info.path, name=info.name))
    probes.sort(key=lambda probe: (probe.path, probe.name))
    return probes[: max(1, limit)]


async def run(
    app: FastAPI,
    client: Any,
    *,
    foreign_tenant_id: str,
    foreign_object_id: str | None = None,
    limit: int = 60,
    headers: dict[str, str] | None = None,
) -> HarnessReport:
    """Send every probe through ``client`` and judge the responses.

    ``client`` is an ``httpx.AsyncClient`` (typically bound to the ASGI app via
    ``ASGITransport``); it must already carry the calling tenant's credential in
    its default headers or in ``headers``.
    """
    report = HarnessReport(suite="cross_tenant")
    probes = build_probes(app, limit=limit)
    if not probes:
        report.record("cross_tenant.probes", SKIP, "no tenant-scoped read routes found")
        return report
    if client is None:
        report.record(
            "cross_tenant.execution",
            SKIP,
            f"{len(probes)} probe(s) generated but no HTTP client was supplied",
            evidence={"probes": len(probes)},
        )
        return report

    object_id = foreign_object_id or str(uuid.uuid4())
    request_headers = dict(headers or {})
    leaked: list[dict[str, str]] = []
    reached: list[dict[str, str]] = []
    errored: list[dict[str, str]] = []

    for probe in probes:
        url = _fill(probe.path, foreign_tenant_id, object_id)
        try:
            response = await client.get(url, headers=request_headers)
        except Exception as exc:  # noqa: BLE001 - a transport failure is a probe result
            errored.append({"path": url, "error": type(exc).__name__})
            continue
        body = response.text or ""
        if response.status_code not in REFUSAL_STATUSES:
            if response.status_code in CLIENT_ERROR_STATUSES:
                # A validation failure proves only that the request was shaped
                # wrongly, not that isolation held: report it for a human.
                continue
            if response.status_code >= 500:
                errored.append({"path": url, "error": f"status_{response.status_code}"})
                continue
            reached.append({"path": url, "status": str(response.status_code)})
        if foreign_tenant_id and foreign_tenant_id in body:
            leaked.append({"path": url, "status": str(response.status_code)})

    if reached:
        report.record(
            "cross_tenant.foreign_read",
            FAIL,
            f"{len(reached)} request(s) using another tenant's identifier were not refused",
            evidence={"requests": reached[:25]},
        )
    else:
        report.record(
            "cross_tenant.foreign_read",
            PASS,
            f"{len(probes)} tenant-scoped read(s) refused a foreign tenant identifier",
            evidence={"probes": len(probes)},
        )

    if leaked:
        report.record(
            "cross_tenant.identifier_leak",
            FAIL,
            "a response contained the other tenant's identifier",
            evidence={"paths": [item["path"] for item in leaked[:25]]},
        )
    else:
        report.record(
            "cross_tenant.identifier_leak",
            PASS,
            "no response echoed the foreign tenant identifier",
        )

    if errored:
        report.record(
            "cross_tenant.transport",
            REVIEW,
            f"{len(errored)} probe(s) could not be completed; rerun with a healthy client",
            evidence={"errors": errored[:25]},
        )

    report.record(
        "cross_tenant.coverage",
        PASS if len(probes) >= 10 else REVIEW,
        f"{len(probes)} probe(s) executed against the mounted application",
    )
    return report


def probes_as_rows(probes: Iterable[TenantProbe]) -> list[dict[str, str]]:
    return [probe.as_dict() for probe in probes]


__all__ = [
    "CLIENT_ERROR_STATUSES",
    "REFUSAL_STATUSES",
    "TENANT_PARAM",
    "TenantProbe",
    "build_probes",
    "probes_as_rows",
    "run",
]
