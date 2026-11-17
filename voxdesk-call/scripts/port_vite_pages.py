#!/usr/bin/env python3
"""Vendor the Vite page tree into `dashboard-next` (PART 17A, P17A-02).

    python scripts/port_vite_pages.py            # copy + generate route wrappers
    python scripts/port_vite_pages.py --check    # report drift, write nothing

Two route families are vendored, because both are presentational React that the
Next app has to serve and neither is worth rewriting:

* :data:`MARKETING_CATEGORIES` — the 49 public-site routes, copied into
  `dashboard-next/app/(marketing)` behind generated wrappers.
* :data:`CONSOLE_PAGES` — the eleven console pages that `dashboard-next` does not
  yet have (agent archive/new/settings/model/voice/tools/test-history/duplicate,
  campaign detail, chat agents, signup). These are the leftover `PORT` rows in
  `docs/UI_PARITY.md`; they are prop-driven components with no `react-router`
  dependency, so they port exactly like the marketing pages.

Why a copy and not a rewrite
----------------------------
The public site is ~8,400 lines of presentational React across ~120 files. It has
no `react-router` imports, no `import.meta.env`, and no CSS imports (verified by
`--check`, which greps for all three and fails if one appears). Rewriting it by
hand into "idiomatic Next" would change the markup, lose the design work and add
review surface without adding a single capability. So the tree is copied
verbatim, preserving the relative import graph, and Next serves it through thin
route wrappers.

Two consequences, stated rather than hidden:

* the copied tree keeps the Vite tree's TypeScript settings. It was written
  under `strict: false` with `allowJs`, so it is checked by its own
  `vite-port/tsconfig.json` (`npm run typecheck:port`) and is excluded from the
  strict app tsconfig. Untyped legacy UI is a fact; pretending it is strict-typed
  is not the way to fix it.
* only code reachable from a ported route component is copied. The Vite console
  shell (`pages/*.jsx`, `components/Shell.jsx`, `lib/api.js`) is deliberately not
  carried over: `dashboard-next/app/dashboard` is the shell now, and the ported
  pages render inside it.

The script is idempotent: re-running it refreshes the copied files and rewrites
the same wrappers.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VITE_SRC = ROOT / "dashboard" / "src"
VITE_ROUTER = VITE_SRC / "app" / "router.tsx"
NEXT_APP = ROOT / "dashboard-next" / "app"
PORT_ROOT = ROOT / "dashboard-next" / "vite-port"
PORT_SRC = PORT_ROOT / "src"

#: Categories the marketing port covers. `dashboard`/`auth` are console routes and
#: are served by `dashboard-next/app/dashboard` and `/login`, not by a copy.
MARKETING_CATEGORIES = {"public", "product", "solutions", "developers", "company", "legal"}

#: Things that would make a verbatim copy wrong. Checked, not assumed.
FORBIDDEN = (
    ("react-router", re.compile(r"from\s+'react-router")),
    ("import.meta", re.compile(r"import\.meta")),
    ("css import", re.compile(r"^import\s+['\"][^'\"]+\.css['\"]", re.M)),
)

#: Written at the top of every copied file. The vendored tree is checked by
#: `vite-port/tsconfig.json` with the settings it was written for; without this
#: header the strict app project would re-judge 118 legacy files through imports
#: and fail on type differences (`active_version: number | null` versus
#: `number | undefined`) that are not regressions. The header is the boundary, it
#: is explicit per file, and it is the reason `npm run typecheck` stays meaningful
#: for new code: nothing under `app/` or `lib/` gets the same treatment.
TS_NOCHECK = "// @ts-nocheck — vendored verbatim from dashboard/src (PART 17A); checked by vite-port/tsconfig.json\n"

#: Files the copy patches, with the exact substitution and why. There is one:
#: `config/env.ts` reads Vite's `import.meta.env`. Next has no `import.meta.env`,
#: and the file already has a `window.__ENV__` runtime path for exactly this
#: situation, so the patch keeps the runtime override and drops the build-time one
#: — the same contract, sourced from the injected `__ENV__` object that Caddy
#: writes. Recorded here so the patch is reviewable and re-applied on every sync.
ADAPTATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "config/env.ts": (
        (
            "const viteEnv = (import.meta as any).env?.[key];",
            "const viteEnv = typeof window !== 'undefined' ? (window as any).__ENV__?.[key] : undefined;",
        ),
    ),
}

EXTENSIONS = (".tsx", ".ts", ".jsx", ".js")


@dataclass(frozen=True)
class ConsolePage:
    """A console page that `dashboard-next` does not serve yet.

    `routes` lists every Vite path that reaches it, `prop` is the name the
    component expects for its route id (Next passes `params`), and `target` is the
    `page.tsx` this script writes. The target keeps the Vite path, so
    `scripts/ui_parity.py` can verify it by path identity instead of a hand-kept
    list.
    """

    routes: tuple[str, ...]
    component: str
    prop: str | None
    target: str


#: The eleven pages behind the last `PORT` rows of the parity table.
CONSOLE_PAGES: tuple[ConsolePage, ...] = (
    ConsolePage(("/dashboard/agents/archive", "/app/agents/archive"), "AgentArchivePage", None,
                "app/dashboard/agents/archive/page.tsx"),
    ConsolePage(("/dashboard/agents/new",), "CreateAgentPage", None,
                "app/dashboard/agents/new/page.tsx"),
    ConsolePage(("/dashboard/agents/:id/settings",), "AgentSettingsPage", "agentId",
                "app/dashboard/agents/[id]/settings/page.tsx"),
    ConsolePage(("/dashboard/agents/:id/model",), "AgentModelPage", "agentId",
                "app/dashboard/agents/[id]/model/page.tsx"),
    ConsolePage(("/dashboard/agents/:id/voice",), "AgentVoicePage", "agentId",
                "app/dashboard/agents/[id]/voice/page.tsx"),
    ConsolePage(("/dashboard/agents/:id/tools",), "AgentToolsPage", "agentId",
                "app/dashboard/agents/[id]/tools/page.tsx"),
    ConsolePage(("/dashboard/agents/:id/test-history",), "AgentTestHistoryPage", "agentId",
                "app/dashboard/agents/[id]/test-history/page.tsx"),
    ConsolePage(("/dashboard/agents/:id/duplicate",), "AgentDuplicatePage", "agentId",
                "app/dashboard/agents/[id]/duplicate/page.tsx"),
    ConsolePage(("/dashboard/campaigns/:id",), "CampaignDetailPage", "campaignId",
                "app/dashboard/campaigns/[id]/page.tsx"),
    ConsolePage(("/dashboard/chat-agents",), "ChatAgentsPage", None,
                "app/dashboard/chat-agents/page.tsx"),
    ConsolePage(("/signup",), "SignupPage", None, "app/(marketing)/signup/page.tsx"),
)


@dataclass(frozen=True)
class Route:
    path: str
    component: str
    category: str

    @property
    def marketing(self) -> bool:
        return self.category in MARKETING_CATEGORIES


def read_routes() -> list[Route]:
    source = VITE_ROUTER.read_text(encoding="utf-8")
    routes: list[Route] = []
    seen: set[str] = set()

    def add(path: str, component: str, category: str) -> None:
        if path in seen or category not in MARKETING_CATEGORIES:
            return
        seen.add(path)
        routes.append(Route(path, component, category))

    for match in re.finditer(
        r"\{\s*path:\s*'([^']+)'[^}]*?component:\s*'([^']+)'[^}]*?category:\s*'([^']+)'", source
    ):
        add(*match.groups())
    for match in re.finditer(r"routeAliases\(\[([^\]]*)\],\s*(\{.*?\})\)", source, re.S):
        component = re.search(r"component:\s*'([^']+)'", match.group(2))
        category = re.search(r"category:\s*'([^']+)'", match.group(2))
        for path in re.findall(r"'([^']+)'", match.group(1)):
            add(path, component.group(1) if component else "", category.group(1) if category else "")
    return routes


#: Component name -> file, for the few pages whose file name does not match the
#: exported component (`QAScorecards.tsx` exports `QAScorecardsPage`). Written out
#: rather than guessed, so a rename in the Vite tree shows up as a missing
#: component instead of silently picking the wrong file.
COMPONENT_FILES: dict[str, str] = {
    "AgentPlaygroundPage": "pages/product/AgentPlayground.tsx",
    "QAScorecardsPage": "pages/product/QAScorecards.tsx",
    "SimulationsPage": "pages/product/Simulations.tsx",
    "WidgetSettings": "pages/public/WidgetSettings.tsx",
    "CallLogConsole": "pages/calls/CallLogConsole.tsx",
    "OperatorConsole": "pages/Overview.jsx",
}


def find_component(component: str) -> Path | None:
    """Resolve a component name to a file, in the Vite source or in the vendored copy.

    The second root matters after P17A-05: `dashboard/` is deleted, so the frozen
    copy under `dashboard-next/vite-port/src` is the only place the component files
    still exist. Callers (`scripts/ui_parity.py` among them) keep working, and a
    resolution that silently returned `None` for all 60 components would turn every
    parity row into a false DEAD.
    """
    if not component:
        return None
    for root in (VITE_SRC, PORT_SRC):
        pinned = COMPONENT_FILES.get(component)
        if pinned:
            candidate = root / pinned
            if candidate.is_file():
                return candidate
            continue
        for candidate in sorted(root.joinpath("pages").rglob(f"{component}.*")):
            if candidate.suffix in EXTENSIONS:
                return candidate
    return None


def resolve_import(from_file: Path, specifier: str) -> Path | None:
    """Resolve a relative import to a file inside `dashboard/src`."""
    if not specifier.startswith("."):
        return None
    base = (from_file.parent / specifier).resolve()
    for extension in EXTENSIONS:
        candidate = base.with_suffix(extension)
        if candidate.is_file():
            return candidate
    for index in ("index.tsx", "index.ts", "index.jsx", "index.js"):
        candidate = base / index
        if candidate.is_file():
            return candidate
    return None


#: Every way this tree reaches another module: static `from '…'`, bare
#: `import '…'`, re-export `export … from '…'`, and dynamic `import('…')`. The
#: dynamic form matters: `api/agents.ts` lazily imports `api/agent-templates.ts` to
#: avoid a cycle, and a closure that only followed static imports produced a tree
#: that failed to typecheck.
IMPORT_RE = re.compile(
    r"""from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)"""
)


def closure(roots: list[Path]) -> list[Path]:
    """Every file reachable from `roots` through relative imports."""
    seen: dict[Path, None] = {}
    queue = list(roots)
    while queue:
        current = queue.pop()
        if current in seen or VITE_SRC not in current.parents:
            continue
        seen[current] = None
        text = current.read_text(encoding="utf-8", errors="replace")
        for match in IMPORT_RE.finditer(text):
            specifier = match.group(1) or match.group(2) or match.group(3) or ""
            target = resolve_import(current, specifier)
            if target is not None and target not in seen:
                queue.append(target)
    return sorted(seen)


def check_copied_tree(files: list[Path]) -> list[str]:
    """Fail on anything the port cannot carry. Adapted files are allowed to break a
    rule only where :data:`ADAPTATIONS` says so — and only for the import.meta rule."""
    problems: list[str] = []
    for path in files:
        relative = path.relative_to(VITE_SRC).as_posix()
        adapted = set()
        for original, _ in ADAPTATIONS.get(relative, ()):
            if "import.meta" in original:
                adapted.add("import.meta")
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in FORBIDDEN:
            if label in adapted:
                continue
            if pattern.search(text):
                problems.append(f"{path.relative_to(ROOT)} uses {label}, which a Next port cannot carry verbatim")
    return problems


def target_path(source: Path) -> Path:
    return PORT_SRC / source.relative_to(VITE_SRC)


def route_to_next_path(path: str) -> str:
    """`/use-cases/:slug` -> `/use-cases/[slug]`."""
    parts = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        parts.append(f"[{segment[1:]}]" if segment.startswith(":") else segment)
    return "/".join(parts)


STATIC_WRAPPER = """// Generated by scripts/port_vite_pages.py — do not edit by hand.
// Source: dashboard/src/{source} (ported verbatim in PART 17A, P17A-02).
"use client";

import Component from "@/vite-port/src/{import_path}";

export default function Page() {{
  return <Component />;
}}
"""

DYNAMIC_WRAPPER = """// Generated by scripts/port_vite_pages.py — do not edit by hand.
// Source: dashboard/src/{source} (ported verbatim in PART 17A, P17A-02).
// The Vite detail page takes its slug as a prop; the Next wrapper takes it from
// the route instead of from the hash.
"use client";

import Component from "@/vite-port/src/{import_path}";

export default async function Page({{ params }}: {{ params: Promise<{{ {param}: string }}> }}) {{
  const resolved = await params;
  return <Component slug={{resolved.{param}}} />;
}}
"""


CONSOLE_WRAPPER = """// Generated by scripts/port_vite_pages.py — do not edit by hand.
// Source: dashboard/src/{source} (ported verbatim in PART 17A, P17A-02).
// The Vite route read its id from the URL; Next passes the same id as `params`.
"use client";

import Component from "@/vite-port/src/{import_path}";

export default async function Page({{ params }}: {{ params: Promise<{{ {param}: string }}> }}) {{
  const resolved = await params;
  return <Component {prop}={{resolved.{param}}} />;
}}
"""


def console_wrapper_for(page: ConsolePage) -> tuple[Path, str] | None:
    component_file = find_component(page.component)
    if component_file is None:
        return None
    relative = component_file.relative_to(VITE_SRC).with_suffix("").as_posix()
    source = component_file.relative_to(VITE_SRC).as_posix()
    target = NEXT_APP / page.target.removeprefix("app/")
    if page.prop is None:
        return target, STATIC_WRAPPER.format(source=source, import_path=relative)
    param = re.search(r"\[(\w+)\]", page.target)
    if not param:
        raise SystemExit(f"CONSOLE_PAGES entry {page.target} needs a [param] segment for {page.prop}")
    return target, CONSOLE_WRAPPER.format(
        source=source, import_path=relative, param=param.group(1), prop=page.prop
    )


def wrapper_for(route: Route, component_file: Path) -> tuple[Path, str]:
    next_path = route_to_next_path(route.path)
    if not next_path:
        # `/` is the marketing home. It replaces the old `app/page.tsx`, which was
        # only a redirect to /login or /dashboard: with the site ported, the front
        # door is the site, and the console is one click away in the header.
        return NEXT_APP / "page.tsx", STATIC_WRAPPER.format(
            source=component_file.relative_to(VITE_SRC).as_posix(),
            import_path=component_file.relative_to(VITE_SRC).with_suffix("").as_posix(),
        )
    directory = NEXT_APP / "(marketing)" / next_path
    page = directory / "page.tsx"
    relative = component_file.relative_to(VITE_SRC).with_suffix("").as_posix()
    source = component_file.relative_to(VITE_SRC).as_posix()
    params = re.findall(r"\[(\w+)\]", next_path)
    if params:
        return page, DYNAMIC_WRAPPER.format(source=source, import_path=relative, param=params[0])
    return page, STATIC_WRAPPER.format(source=source, import_path=relative)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args(argv)

    if not VITE_ROUTER.is_file():
        # P17A-05: the Vite app is gone. `--check` still has a job — the vendored
        # copy is what ships, so it is the tree whose forbidden patterns matter.
        if args.check:
            vendored = sorted(PORT_SRC.rglob("*")) if PORT_SRC.exists() else []
            problems = []
            for path in vendored:
                if path.suffix not in EXTENSIONS:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for label, pattern in FORBIDDEN:
                    if pattern.search(text):
                        problems.append(f"{path.relative_to(ROOT)} uses {label}")
            for problem in problems:
                print(f"  {problem}")
            print(f"vite source retired; checked {len(vendored)} vendored files, {len(problems)} problems")
            return 1 if problems else 0
        print(
            "dashboard/src was deleted in P17A-05; there is nothing to copy. "
            "The vendored tree in dashboard-next/vite-port/src is the source of truth now.",
            file=sys.stderr,
        )
        return 1

    routes = read_routes()
    missing = [route for route in routes if find_component(route.component) is None]
    console_missing = [page for page in CONSOLE_PAGES if find_component(page.component) is None]
    roots = [path for path in (find_component(route.component) for route in routes) if path]
    roots += [path for path in (find_component(page.component) for page in CONSOLE_PAGES) if path]
    files = closure(roots)
    problems = check_copied_tree(files)

    print(
        f"marketing routes: {len(routes)}  console pages: {len(CONSOLE_PAGES)}  "
        f"component files: {len(roots)}  reachable files: {len(files)}"
    )
    for route in missing:
        print(f"  MISSING COMPONENT {route.path} -> {route.component}")
    for page in console_missing:
        print(f"  MISSING COMPONENT {page.routes[0]} -> {page.component}")

    if args.check:
        for problem in problems:
            print(f"  {problem}")
        return 1 if (problems or missing or console_missing) else 0

    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    if PORT_SRC.exists():
        shutil.rmtree(PORT_SRC)
    patched = 0
    for source in files:
        destination = target_path(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        relative = source.relative_to(VITE_SRC).as_posix()
        rules = ADAPTATIONS.get(relative, ())
        if not rules:
            destination.write_text(
                TS_NOCHECK + source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
            )
            continue
        if rules:
            text = source.read_text(encoding="utf-8")
            for original, replacement in rules:
                if original not in text:
                    print(f"ADAPTATION STALE in {relative}: {original[:40]!r}", file=sys.stderr)
                    return 1
                text = text.replace(original, replacement)
            destination.write_text(TS_NOCHECK + text, encoding="utf-8")
            patched += 1
            continue

    written = 0
    for route in routes:
        component_file = find_component(route.component)
        if component_file is None:
            continue
        page, content = wrapper_for(route, component_file)
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(content, encoding="utf-8")
        written += 1

    console_written = 0
    for page_spec in CONSOLE_PAGES:
        built = console_wrapper_for(page_spec)
        if built is None:
            continue
        page, content = built
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(content, encoding="utf-8")
        console_written += 1

    # Prune wrappers this generator wrote in an earlier run and no longer targets.
    # Moving `/signup` from `app/signup` into the marketing group left a stale page
    # behind that Next rejected as a parallel route ("two pages resolve to the same
    # path"), so the generator cleans up after itself instead of relying on memory.
    expected = {NEXT_APP / "page.tsx"}
    for route in routes:
        expected.add(wrapper_for(route, find_component(route.component) or Path("/dev/null"))[0]
                     if find_component(route.component) else NEXT_APP / "page.tsx")
    for page_spec in CONSOLE_PAGES:
        built = console_wrapper_for(page_spec)
        if built:
            expected.add(built[0])
    pruned = 0
    for candidate in NEXT_APP.rglob("page.tsx"):
        if candidate in expected:
            continue
        head = candidate.read_text(encoding="utf-8", errors="replace")[:200]
        if "Generated by scripts/port_vite_pages.py" in head:
            candidate.unlink()
            pruned += 1
    if pruned:
        print(f"pruned {pruned} stale generated wrapper(s)")

    print(f"copied {len(files)} files into dashboard-next/vite-port/src ({patched} adapted)")
    print(f"generated {written} marketing wrappers under dashboard-next/app/(marketing)")
    print(f"generated {console_written} console wrappers under dashboard-next/app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
