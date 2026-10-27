"""Regenerate ``tests/truth/routes_snapshot.json`` from the mounted application.

``tests/truth/test_no_template_routes.py`` compares ``len(app.routes)`` against
this snapshot's ``route_count``. The snapshot is a *record of the live table*,
not a reviewed allowlist, so it has to be re-recorded whenever routes are
mounted or unmounted — which is exactly what Batch 06 does (23 modules, 154 new
route objects).

Usage::

    python3 scripts/record_route_snapshot.py          # write
    python3 scripts/record_route_snapshot.py --check  # verify only

The shape of the file is unchanged from the batches that produced it: a HEAD
method is added for starlette ``Route`` objects that answer GET, because the
router serves HEAD implicitly and the snapshot has always recorded that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from starlette.routing import Route as StarletteRoute

ROOT = Path(__file__).resolve().parents[1]
# Importing `app` requires the repository root on sys.path when this file is run
# as a script (`python3 scripts/...` puts `scripts/` there instead).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SNAPSHOT = ROOT / "tests" / "truth" / "routes_snapshot.json"
DIST_INDEX = ROOT / "dashboard" / "dist" / "index.html"


def build_snapshot() -> dict:
    from app.main import app

    routes = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = sorted(getattr(route, "methods", None) or [])
        if isinstance(route, StarletteRoute) and "GET" in methods and "HEAD" not in methods:
            methods.append("HEAD")
            methods.sort()
        routes.append(
            {
                "methods": methods,
                "name": getattr(route, "name", ""),
                "path": path,
            }
        )
    return {
        "dashboard_build_present": DIST_INDEX.is_file(),
        "route_count": len(routes),
        "routes": routes,
    }


def main(argv: list[str]) -> int:
    snapshot = build_snapshot()
    rendered = json.dumps(snapshot, indent=2) + "\n"
    if "--check" in argv:
        current = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.is_file() else ""
        if current == rendered:
            print(f"routes_snapshot.json is current ({snapshot['route_count']} routes)")
            return 0
        print("routes_snapshot.json is stale; run scripts/record_route_snapshot.py")
        return 1
    SNAPSHOT.write_text(rendered, encoding="utf-8")
    print(f"recorded {snapshot['route_count']} routes into {SNAPSHOT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
