"""Validate the actual mounted application, not a synthetic route list."""
import json
import re
from pathlib import Path
from app.main import app


def test_no_template_routes_and_preserved_real_modules():
    assert not any(re.search(r'/endpoint-\d+', route.path) for route in app.routes)
    modules = {getattr(getattr(r, 'endpoint', None), '__module__', '') for r in app.routes}
    assert 'app.api.retell_parity_routes' in modules
    assert 'app.api.conductor_routes' in modules
    expected = json.loads(Path('tests/truth/routes_snapshot.json').read_text())
    # The /assets mount and the GET /{full_path:path} SPA fallback are mounted only when a
    # dashboard build exists, so the expected count must follow the build state.
    dashboard_routes = 2
    build_present = Path('dashboard/dist/index.html').is_file()
    expected_count = expected['route_count'] + dashboard_routes * (
        int(build_present) - int(expected['dashboard_build_present'])
    )
    assert len(app.routes) == expected_count


def test_no_api_line_count_banners():
    pattern = re.compile(r'NO SKIP FULL CODE|\d{3,}\+\s*lines', re.I)
    assert not [str(p) for p in Path('app/api').rglob('*.py') if pattern.search(p.read_text())]


def test_openapi_matches_the_reviewed_current_contract():
    expected = json.loads(Path('contracts/openapi.json').read_text())
    assert app.openapi() == expected
