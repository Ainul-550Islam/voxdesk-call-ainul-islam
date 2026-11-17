#!/usr/bin/env python3
"""Route parity: the Vite dashboard versus `dashboard-next` (PART 17A, P17A-01).

    python scripts/ui_parity.py            # print the table, write docs/UI_PARITY.md
    python scripts/ui_parity.py --check    # exit 1 while any PORT row remains

Why this is a mapping and not a diff
------------------------------------
The two apps do not use the same URLs. The Vite console is a hash router with
`/app/...` and `/dashboard/agents/:id/builder`; the Next app uses real paths and
`/dashboard/agents/[id]/flow`. A path-by-path diff would report ~97 differences
and tell you nothing, so parity is expressed as an explicit table
(:data:`EQUIVALENTS`) and the script *checks* every row: a mapping whose target
does not exist in `dashboard-next/app` is a failure, not a mapping. That is what
keeps this file from becoming a wish list.

Three classifications
---------------------
* ``DONE`` — the Vite route has a working equivalent in the Next app. The row
  names the equivalent path, so a reviewer can open both.
* ``PORT`` — the route has a real component in the Vite tree and nothing in Next.
  Every PORT row is work; the script exits 1 while one exists.
* ``DEAD`` — the route cannot be carried over because there is nothing to carry:
  a declared component that does not exist in the Vite tree, a parity placeholder
  page, or an alias that resolves to a route already counted. DEAD pages die with
  the Vite app and need no port. Each DEAD row carries its reason in the table
  rather than being quietly dropped.

The script reads the Vite router (`dashboard/src/app/router.tsx`) — the actual
route table, not a directory listing — and the Next app's `page.tsx` files. No
hand-maintained duplicate list of what exists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# One resolver for both scripts. `ui_parity` used to match component names against
# file names, which is wrong exactly where it matters: six Vite routes declare an
# exported component whose file is named differently (`QAScorecardsPage` lives in
# `QAScorecards.tsx`). It reported those as dead pages. `port_vite_pages`
# already had the pin map, so the mapping is imported rather than duplicated.
from port_vite_pages import (  # noqa: E402
    CONSOLE_PAGES,
    MARKETING_CATEGORIES,
    find_component,
    route_to_next_path,
)

ROOT = Path(__file__).resolve().parents[1]
VITE_ROUTER = ROOT / "dashboard" / "src" / "app" / "router.tsx"
VITE_PAGES = ROOT / "dashboard" / "src" / "pages"
NEXT_APP = ROOT / "dashboard-next" / "app"
REPORT = ROOT / "docs" / "UI_PARITY.md"
#: The route inventory this table compares against, frozen when the Vite app is
#: retired (P17A-05). Without it the generator would stop working the moment
#: `dashboard/` is deleted, and the parity claim would become unreproducible.
SNAPSHOT = ROOT / "docs" / "ui_parity_routes.json"

#: Vite path -> Next path. Every target is verified against the filesystem.
#: The comment on each row is the *reason* the two are the same page, because a
#: reviewer's first question is always "is this really the same screen?".
EQUIVALENTS: dict[str, str] = {
    # --- console shell -------------------------------------------------------
    "/app": "/dashboard",
    "/app/overview": "/dashboard",
    "/workspace": "/dashboard",
    "/dashboard": "/dashboard",
    # --- agents --------------------------------------------------------------
    "/agent": "/dashboard/agents",
    "/app/agents": "/dashboard/agents",
    "/dashboard/agents": "/dashboard/agents",
    "/app/agents/list": "/dashboard/agents",
    "/dashboard/agents/list": "/dashboard/agents",
    "/dashboard/agents/:id": "/dashboard/agents/[id]",
    "/dashboard/agents/:id/builder": "/dashboard/agents/[id]/flow",
    "/dashboard/agents/:id/versions/:version": "/dashboard/agents/[id]/versions",
    # --- analytics -----------------------------------------------------------
    "/dashboard/analytics": "/dashboard/analytics",
    "/dashboard/analytics-console": "/dashboard/analytics",
    "/app/customer-service/analytics": "/dashboard/analytics",
    "/dashboard/customer-service/analytics": "/dashboard/analytics",
    # --- calls ---------------------------------------------------------------
    "/calls": "/dashboard/calls",
    "/calls/:id": "/dashboard/calls/[id]",
    "/dashboard/calls": "/dashboard/calls",
    "/dashboard/calls/:id": "/dashboard/calls/[id]",
    "/dashboard/call-runtime": "/dashboard/live",
    "/app/call-runtime": "/dashboard/live",
    # --- campaigns, contacts, workflows --------------------------------------
    "/campaigns": "/dashboard/campaigns",
    "/dashboard/campaigns": "/dashboard/campaigns",
    "/dashboard/contacts": "/dashboard/leads",
    "/workflows": "/dashboard/workflows",
    "/dashboard/workflows": "/dashboard/workflows",
    "/studio/conductor": "/dashboard/conductor",
    "/dashboard/conductor": "/dashboard/conductor",
    # --- knowledge, integrations, telephony ----------------------------------
    "/app/knowledge": "/dashboard/knowledge",
    "/dashboard/knowledge": "/dashboard/knowledge",
    "/dashboard/knowledge-base": "/dashboard/knowledge",
    "/dashboard/integrations-console": "/dashboard/integrations",
    "/app/phone-numbers": "/dashboard/phone-numbers",
    "/phone-numbers": "/dashboard/phone-numbers",
    "/dashboard/phone-numbers": "/dashboard/phone-numbers",
    # --- QA, tests, experiments ---------------------------------------------
    "/dashboard/qa-scorecards": "/dashboard/reviews",
    "/dashboard/simulations": "/dashboard/tests",
    "/app/experiments": "/dashboard/experiments",
    # `/experiments` and `/app/experiments` render the same component; the Vite
    # router exposed both spellings, the console has one route.
    "/experiments": "/dashboard/experiments",
    "/dashboard/experiments": "/dashboard/experiments",
    # --- settings, audit, billing, team -------------------------------------
    # All three of these render the retired Vite `WorkspaceSettingsPage`
    # (`routeAliases(['/settings','/dashboard/settings','/security-settings'])`):
    # workspace policy, live sessions, API keys and the security posture. The Next
    # equivalent is `/dashboard/settings/identity`, NOT `/dashboard/settings`,
    # which is only an index of links. `/dashboard/settings/identity/security` is
    # "My security" — a self-service page for the caller alone — so it is a
    # different screen and mapping to it would have been wrong.
    "/settings": "/dashboard/settings/identity",
    "/dashboard/settings": "/dashboard/settings/identity",
    "/dashboard/settings/identity": "/dashboard/settings/identity",
    "/security-settings": "/dashboard/settings/identity",
    "/dashboard/audit-log": "/dashboard/audit",
    "/billing": "/dashboard/billing",
    "/dashboard/billing": "/dashboard/billing",
    "/dashboard/team": "/dashboard/team",
    "/dashboard/webhooks": "/dashboard/webhooks",
    # --- auth ----------------------------------------------------------------
    "/login": "/login",
}

#: Routes that must not be carried over, and why. A DEAD row is a decision, so it
#: is written down here instead of disappearing in a directory listing.
DEAD: dict[str, str] = {
    "/dashboard/final-parity": "parity placeholder page: it renders a hand-written table of the "
    "same comparison `scripts/ui_parity.py` now generates from code",
    "/dashboard/playground": "the Vite route declares `AgentPlaygroundPage`, which does not exist "
    "in dashboard/src/pages; the working playground is `/dashboard/agents/[id]/test-call`",
    "/app/public-keys": "the Vite route declares `WidgetSettings`, which does not exist in "
    "dashboard/src/pages; widget keys are managed under `/dashboard/settings/identity/api-keys`",
    "/dashboard/public-keys": "same missing component as `/app/public-keys`",
}

#: Every console page vendored by `scripts/port_vite_pages.py`. Imported from the
#: port script instead of restated, so this file cannot claim a page is ported to
#: a path the generator does not write.
PORTED: dict[str, str] = {
    route: page.target
    for page in CONSOLE_PAGES
    for route in page.routes
}

DONE, PORT, DEAD_STATE = "DONE", "PORT", "DEAD"

#: Route categories in the Vite router. Kept because the retirement decision has
#: to be readable per area: console parity is a product claim, site parity is a
#: marketing claim, and they fail differently.
CONSOLE_CATEGORIES = {"dashboard", "auth"}


@dataclass(frozen=True)
class ViteRoute:
    path: str
    component: str
    category: str


@dataclass(frozen=True)
class Row:
    path: str
    component: str
    category: str
    state: str
    target: str
    note: str

    @property
    def console(self) -> bool:
        return self.category in CONSOLE_CATEGORIES


def read_vite_routes() -> list[ViteRoute]:
    """Parse the Vite route table.

    Two shapes exist in the file: single objects and `routeAliases([...], {...})`
    groups that share one component and category. Both are read because the
    aliases are real routes (e.g. `/` and `/home`).
    """
    if not VITE_ROUTER.exists():
        raise SystemExit(f"missing {VITE_ROUTER.relative_to(ROOT)}; was the Vite app retired?")
    source = VITE_ROUTER.read_text(encoding="utf-8")
    routes: list[ViteRoute] = []
    seen: set[str] = set()

    def add(path: str, component: str, category: str) -> None:
        if path in seen:
            return
        seen.add(path)
        routes.append(ViteRoute(path=path, component=component, category=category))

    for match in re.finditer(
        r"\{\s*path:\s*'([^']+)'[^}]*?component:\s*'([^']+)'[^}]*?category:\s*'([^']+)'",
        source,
    ):
        add(*match.groups())
    for match in re.finditer(r"routeAliases\(\[([^\]]*)\],\s*(\{.*?\})\)", source, re.S):
        paths = re.findall(r"'([^']+)'", match.group(1))
        component = re.search(r"component:\s*'([^']+)'", match.group(2))
        category = re.search(r"category:\s*'([^']+)'", match.group(2))
        for path in paths:
            add(path, component.group(1) if component else "", category.group(1) if category else "")
    return routes


def load_vite_routes(write_snapshot: bool = False) -> tuple[list[ViteRoute], str]:
    """Vite routes, live from `router.tsx` or from the frozen snapshot.

    Returns the routes and a one-line provenance string that ends up in the
    report, so a reader can tell whether the table was built from the code or from
    the record of it.
    """
    if VITE_ROUTER.is_file():
        routes = read_vite_routes()
        if write_snapshot:
            payload = {
                "note": "Frozen route inventory of dashboard/src/app/router.tsx, "
                "captured by scripts/ui_parity.py before the Vite app was deleted "
                "(PART 17A, P17A-05). Do not edit by hand.",
                "routes": [
                    {"path": r.path, "component": r.component, "category": r.category} for r in routes
                ],
            }
            SNAPSHOT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return routes, f"live from `dashboard/src/app/router.tsx` ({SNAPSHOT.name} refreshed)"
    if SNAPSHOT.is_file():
        payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        routes = [
            ViteRoute(path=row["path"], component=row.get("component", ""), category=row.get("category", ""))
            for row in payload["routes"]
        ]
        return routes, f"frozen snapshot `docs/{SNAPSHOT.name}` (Vite app retired)"
    return [], "no source and no snapshot"


def read_next_routes() -> set[str]:
    """Every route the Next app serves, from `page.tsx` files (route groups stripped)."""
    routes: set[str] = set()
    if not NEXT_APP.exists():
        return routes
    for page in NEXT_APP.rglob("page.tsx"):
        relative = page.parent.relative_to(NEXT_APP)
        segments = [part for part in relative.parts if not (part.startswith("(") and part.endswith(")"))]
        route = "/" + "/".join(segments) if segments else "/"
        routes.add(route)
    return routes


def component_exists(component: str) -> bool:
    return find_component(component) is not None


def marketing_wrapper(path: str) -> str | None:
    """Relative `dashboard-next` path of the wrapper that serves a marketing route.

    Returns the wrapper only when the route really is marketing (its category says
    so) *and* the generated file is on disk, so a marketing row can never be called
    done on the strength of a category name alone.
    """
    next_path = route_to_next_path(path)
    relative = f"app/{'(marketing)/' if next_path else ''}{next_path}/page.tsx".replace("//", "/")
    if not next_path:
        relative = "app/page.tsx"
    return relative if (ROOT / "dashboard-next" / relative).exists() else None


def build_rows(write_snapshot: bool = False) -> tuple[list[Row], list[str], str]:
    """Classify every Vite route. Returns the rows, the problems and the provenance."""
    routes, provenance = load_vite_routes(write_snapshot=write_snapshot)
    next_routes = read_next_routes()
    problems: list[str] = []
    rows: list[Row] = []

    for route in routes:
        path = route.path
        if path in DEAD:
            rows.append(Row(path, route.component, route.category, DEAD_STATE, "—", DEAD[path]))
            continue

        target = EQUIVALENTS.get(path)
        if target:
            if target not in next_routes:
                problems.append(f"{path} maps to {target}, which dashboard-next does not serve")
                rows.append(
                    Row(path, route.component, route.category, PORT, target, "mapping target is missing")
                )
                continue
            rows.append(Row(path, route.component, route.category, DONE, target, "equivalent route"))
            continue

        ported_to = PORTED.get(path)
        if ported_to and (ROOT / "dashboard-next" / ported_to).exists():
            rows.append(Row(path, route.component, route.category, DONE, ported_to, "ported in PART 17A"))
            continue

        if ported_to:
            problems.append(f"{path} claims {ported_to}, which does not exist")
            rows.append(Row(path, route.component, route.category, PORT, ported_to, "claimed port file missing"))
            continue

        if route.category in MARKETING_CATEGORIES and component_exists(route.component):
            wrapper = marketing_wrapper(path)
            if wrapper:
                rows.append(
                    Row(path, route.component, route.category, DONE, wrapper, "ported verbatim (P17A-02)")
                )
                continue
            problems.append(f"{path} is a marketing route with no generated wrapper")
            rows.append(Row(path, route.component, route.category, PORT, "—", "marketing wrapper missing"))
            continue

        if not component_exists(route.component):
            rows.append(
                Row(
                    path,
                    route.component,
                    route.category,
                    DEAD_STATE,
                    "—",
                    f"component {route.component or '(none)'} does not exist in dashboard/src/pages",
                )
            )
            continue

        rows.append(Row(path, route.component, route.category, PORT, "—", "no Next equivalent yet"))

    if not routes:
        problems.append("no route inventory: neither dashboard/src/app/router.tsx nor the snapshot exists")
    return rows, problems, provenance


def render_table(rows: list[Row], problems: list[str], provenance: str = "live") -> str:
    console = [row for row in rows if row.console]
    site = [row for row in rows if not row.console]
    counts = {
        state: sum(1 for row in rows if row.state == state) for state in (DONE, PORT, DEAD_STATE)
    }
    console_counts = {
        state: sum(1 for row in console if row.state == state) for state in (DONE, PORT, DEAD_STATE)
    }
    site_counts = {
        state: sum(1 for row in site if row.state == state) for state in (DONE, PORT, DEAD_STATE)
    }

    lines: list[str] = []
    lines.append("# UI parity — Vite dashboard versus `dashboard-next`")
    lines.append("")
    lines.append(
        "Generated by `python scripts/ui_parity.py`. Do not edit by hand: the table is computed "
        "from the Vite route inventory and `dashboard-next/app/**/page.tsx`, and every "
        "`DONE` row's target is checked against the filesystem before it is printed."
    )
    lines.append("")
    lines.append(f"**Route inventory:** {provenance}")
    lines.append("")
    lines.append(
        "This is the single source for the retirement decision. A `PORT` row means the "
        "capability exists only in the Vite app; deleting that app would remove it."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Area | routes | DONE | PORT | DEAD |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| Console (`dashboard`, `auth`) | {len(console)} | {console_counts[DONE]} | "
        f"{console_counts[PORT]} | {console_counts[DEAD_STATE]} |"
    )
    lines.append(
        f"| Public site (marketing) | {len(site)} | {site_counts[DONE]} | "
        f"{site_counts[PORT]} | {site_counts[DEAD_STATE]} |"
    )
    lines.append(f"| **Total** | **{len(rows)}** | **{counts[DONE]}** | **{counts[PORT]}** | **{counts[DEAD_STATE]}** |")
    lines.append("")
    if problems:
        lines.append("## Problems")
        lines.append("")
        for problem in problems:
            lines.append(f"- {problem}")
        lines.append("")
    for title, group in (("Console", console), ("Public site", site)):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| Vite path | Component | State | Next route / file | Note |")
        lines.append("|---|---|---|---|---|")
        for row in sorted(group, key=lambda item: (item.state != PORT, item.path)):
            lines.append(
                f"| `{row.path}` | `{row.component or '—'}` | **{row.state}** | `{row.target}` | {row.note} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 while any PORT row remains")
    parser.add_argument("--write", default=str(REPORT), help="where to write the table")
    args = parser.parse_args(argv)

    rows, problems, provenance = build_rows(write_snapshot=not args.check)
    counts = {state: sum(1 for row in rows if row.state == state) for state in (DONE, PORT, DEAD_STATE)}
    ports = [row for row in rows if row.state == PORT]

    out = Path(args.write)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_table(rows, problems, provenance), encoding="utf-8")

    print(f"vite routes: {len(rows)}  DONE {counts[DONE]}  PORT {counts[PORT]}  DEAD {counts[DEAD_STATE]}")
    for row in ports:
        print(f"  PORT {row.path:48s} {row.component:28s} {row.note}")
    for problem in problems:
        print(f"  PROBLEM {problem}", file=sys.stderr)
    print(f"wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")

    if problems:
        return 2
    if args.check and ports:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
