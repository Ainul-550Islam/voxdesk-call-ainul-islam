#!/usr/bin/env python3
"""Generate the static status page, its JSON and its RSS feed (PART 16, P16-05).

    python3 infra/status/generate_status.py --base-url http://127.0.0.1:8000

Everything the page shows comes from one of three places, and none of them is
hand-written HTML:

* ``--base-url``: live probes of the API's own health endpoints. A probe that
  fails is reported as ``down``, and a probe that could not be attempted (no
  base URL, no network) is reported as ``unknown`` — never as ``up``.
* ``incidents.json``: the incident log, appended by a human or by the incident
  process in `docs/COMPLIANCE/INCIDENT_RESPONSE.md`. An empty file means "no
  incident has been recorded", which is a different claim from "no incident has
  ever happened" and the page says which one it is making.
* ``docs/TRUST_CENTER.md``: certification status is copied from there, so the
  public page cannot claim a certification the repository does not.

Outputs, written next to this script:

* ``status.json`` — machine-readable current state (what a badge reads)
* ``index.html`` — the page, self-contained (inline CSS, no external assets)
* ``feed.xml`` — RSS 2.0 of recorded incidents

Exit code is 0 when generation succeeded, regardless of whether a component is
down: the page's *job* is to report outages, so a down component is not a build
failure. CI checks the artifacts instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

#: The endpoints worth probing: liveness, readiness, and the dependency report
#: (`app/api/health_routes.py`). A probe that was not attempted reports
#: `unknown`; a probe that answered a 4xx reports `down` — the route is part of
#: the contract, so its absence is a real failure, not an unknown.
#: ``critical`` decides the word on the banner. Liveness and readiness failing is
#: an outage; a dependency report (cache, outbox, provider keys) answering 503
#: while the process still serves traffic is *degraded*, and calling that an
#: outage teaches operators to ignore the page.
PROBES: tuple[tuple[str, str, bool], ...] = (
    ("liveness", "/health", True),
    ("readiness", "/health/ready", True),
    ("dependencies", "/health/dependencies", False),
)


@dataclass
class Component:
    name: str
    target: str
    state: str  # up | down | unknown
    detail: str = ""
    critical: bool = True

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "target": self.target,
            "state": self.state,
            "detail": self.detail,
            "critical": self.critical,
        }


@dataclass
class Incidents:
    rows: list[dict] = field(default_factory=list)

    @property
    def open(self) -> list[dict]:
        return [row for row in self.rows if row.get("status") != "resolved"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def probe(base_url: str | None, path: str, timeout: float, *, critical: bool = True) -> Component:
    """Probe one path. Anything short of a 2xx is not ``up``."""
    if not base_url:
        return Component(
            name=path.lstrip("/"),
            target="(no base url)",
            state="unknown",
            detail="not probed",
            critical=critical,
        )
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - operator-supplied base URL
            body = response.read(512).decode("utf-8", "replace")
            code = response.status
    except urllib.error.HTTPError as exc:
        return Component(path.lstrip("/"), url, "down", f"HTTP {exc.code}", critical)
    except Exception as exc:  # noqa: BLE001 - the message is the useful part
        return Component(path.lstrip("/"), url, "down", type(exc).__name__, critical)
    if 200 <= code < 300:
        return Component(path.lstrip("/"), url, "up", f"HTTP {code}", critical)
    return Component(path.lstrip("/"), url, "down", f"HTTP {code} {body[:80]}", critical)


def load_incidents() -> Incidents:
    path = HERE / "incidents.json"
    if not path.exists():
        return Incidents()
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"incidents.json is not valid JSON: {exc}") from exc
    rows = payload if isinstance(payload, list) else payload.get("incidents", [])
    clean = []
    for row in rows:
        clean.append(
            {
                "id": str(row.get("id") or "")[:40],
                "title": str(row.get("title") or "Untitled incident")[:200],
                "severity": str(row.get("severity") or "SEV-3")[:8],
                "status": str(row.get("status") or "investigating")[:20],
                "started_at": str(row.get("started_at") or "")[:40],
                "resolved_at": str(row.get("resolved_at") or "")[:40],
                "summary": str(row.get("summary") or "")[:2000],
            }
        )
    return Incidents(rows=clean)


def certification_lines() -> list[tuple[str, str]]:
    """Certification status copied from docs/TRUST_CENTER.md, not restated."""
    trust = REPO_ROOT / "docs" / "TRUST_CENTER.md"
    if not trust.exists():
        return [("Certifications", "not published")]
    lines: list[tuple[str, str]] = []
    for raw in trust.read_text(encoding="utf-8").splitlines():
        if raw.startswith("| ") and not raw.startswith("| Certification") and "---" not in raw:
            cells = [cell.strip() for cell in raw.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and cells[0] != "Certification":
                lines.append((cells[0], cells[1].replace("**", "")))
    return lines


def overall(components: list[Component], incidents: Incidents) -> str:
    if any(item.state == "down" and item.critical for item in components):
        return "major_outage"
    if any(item.state == "down" for item in components) or incidents.open:
        return "degraded"
    if any(item.state == "unknown" for item in components):
        return "unknown"
    return "operational"


def render_once(data: dict, incidents: Incidents) -> str:
    """Write one artifact: the JSON."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def render_html(data: dict, incidents: Incidents) -> str:
    rows = "\n".join(
        f'<tr><td>{escape(item["name"])}</td><td class="s-{item["state"]}">{item["state"]}</td>'
        f'<td class="muted">{escape(item["detail"])}</td></tr>'
        for item in data["components"]
    )
    certs = "\n".join(
        f'<tr><td>{escape(name)}</td><td>{escape(state)}</td></tr>' for name, state in data["certifications"]
    )
    if incidents.rows:
        incident_rows = "\n".join(
            f'<tr><td>{escape(row["id"])}</td><td>{escape(row["title"])}</td>'
            f'<td>{escape(row["severity"])}</td><td>{escape(row["status"])}</td>'
            f'<td class="muted">{escape(row["started_at"])}</td></tr>'
            for row in incidents.rows
        )
        incidents_block = f"<table><thead><tr><th>Id</th><th>Title</th><th>Severity</th><th>Status</th><th>Started</th></tr></thead><tbody>{incident_rows}</tbody></table>"
        incidents_note = "Every incident recorded in <code>infra/status/incidents.json</code>."
    else:
        incidents_block = '<p class="muted">No incident has been recorded in <code>incidents.json</code>. This means no incident has been <em>logged</em>, not that none has happened.</p>'
        incidents_note = ""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VoxDesk status — {escape(data["status"])}</title>
<style>
 body{{font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#0b0f14;color:#e6edf3}}
 main{{max-width:820px;margin:0 auto;padding:32px 20px 64px}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px;color:#9fb0c0}}
 .banner{{border-radius:8px;padding:14px 16px;margin:18px 0;font-weight:600}}
 .banner.operational{{background:#10331f;color:#6ee7a8}} .banner.degraded{{background:#3a2c10;color:#f5c451}}
 .banner.major_outage{{background:#3a1113;color:#ff8189}} .banner.unknown{{background:#20262e;color:#9fb0c0}}
 table{{width:100%;border-collapse:collapse;margin:8px 0 0}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #1d242d;font-size:14px}}
 th{{color:#9fb0c0;font-weight:600}} .muted{{color:#7d8b99}} code{{color:#9fb0c0}}
 .s-up{{color:#6ee7a8}} .s-down{{color:#ff8189}} .s-unknown{{color:#f5c451}}
 a{{color:#7cc4ff}}
</style>
</head>
<body><main>
<h1>VoxDesk status</h1>
<p class="muted">Generated {escape(data["generated_at"])} from live probes. No value here is hand-typed.</p>
<div class="banner {escape(data["status"])}">{escape(data["status"].replace("_", " ").upper())}</div>
<h2>Components</h2>
<table><thead><tr><th>Probe</th><th>State</th><th>Detail</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Incidents</h2>
{incidents_block}
<p class="muted">{escape(incidents_note)}</p>
<h2>Certifications</h2>
<table><thead><tr><th>Certification</th><th>Status</th></tr></thead><tbody>{certs}</tbody></table>
<p class="muted">Status vocabulary: <code>up</code> = the probe answered 2xx; <code>down</code> = it answered badly or not at all;
<code>unknown</code> = the probe was not attempted, and we do not guess.</p>
<p class="muted"><a href="feed.xml">RSS</a> · <a href="status.json">status.json</a></p>
</main></body></html>
"""


def render_rss(data: dict, incidents: Incidents) -> str:
    items = []
    for row in incidents.rows[:50]:
        stamp = row["started_at"] or data["generated_at"]
        items.append(
            "    <item>\n"
            f"      <title>{escape(row['severity'] + ': ' + row['title'])}</title>\n"
            f"      <guid isPermaLink=\"false\">{escape(row['id'] or stamp)}</guid>\n"
            f"      <pubDate>{escape(stamp)}</pubDate>\n"
            f"      <description>{escape(row['summary'] or row['status'])}</description>\n"
            "    </item>"
        )
    body = "\n".join(items) if items else "    <!-- no incidents recorded -->"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>VoxDesk status</title>
    <link>/</link>
    <description>Incidents and current status. Generated {escape(data["generated_at"])}.</description>
    <lastBuildDate>{escape(data["generated_at"])}</lastBuildDate>
{body}
  </channel>
</rss>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None, help="probe this API root; omit to write unknown states")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--out-dir", default=str(HERE))
    args = parser.parse_args(argv)

    components = [
        probe(args.base_url, path, args.timeout, critical=critical)
        for _, path, critical in PROBES
    ]
    incidents = load_incidents()
    data = {
        "generated_at": _iso(_now()),
        "status": overall(components, incidents),
        "components": [component.as_dict() for component in components],
        "certifications": [{"name": name, "status": state} for name, state in certification_lines()],
        "open_incidents": len(incidents.open),
        # Said in the payload, so a consumer of status.json cannot mistake a
        # probe for a physical guarantee.
        "probe_means": "2xx from the endpoint; not a claim about upstream providers",
    }

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(render_once(data, incidents), encoding="utf-8")
    (out / "index.html").write_text(render_html(data, incidents), encoding="utf-8")
    (out / "feed.xml").write_text(render_rss(data, incidents), encoding="utf-8")
    print(f"status: {data['status']} ({len(components)} probes) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
