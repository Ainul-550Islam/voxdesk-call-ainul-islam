"""Measure source, live route registration, pytest collection, and JUnit results.

``python scripts/repo_stats.py`` prints the measured statistics JSON.
``python scripts/repo_stats.py --write-readme`` writes both ``docs/STATS.json``
(the single source of truth for repository numbers across ``README.md``,
``docs/SALES/PRICING_AND_TIERS.md``, and ``docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md``)
and ``README.md``.
``python scripts/repo_stats.py --write-stats`` writes ``docs/STATS.json`` only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pytest_collection_stats import measure_collected_pytest_nodes  # noqa: E402

SOURCE_ROOTS = ('app', 'services', 'dashboard/src', 'dashboard-next', 'sdk')
SOURCE_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.rs', '.go', '.cpp', '.h', '.hpp'}
SOURCE_SKIP_PARTS = {'node_modules', 'target', 'build', 'dist', 'vendor', '__pycache__', '.next'}

STATS_PATH = ROOT / 'docs' / 'STATS.json'
MATRIX_PATH = ROOT / 'docs' / 'SALES' / 'FEATURE_MATRIX_VERIFIED.md'
MATRIX_ROW = re.compile(r'^\|\s*(\d+):\s+(.+?)\s*\|\s*(LIVE|API_ONLY|PLANNED|NOT_CONFIGURED|LIVE_PROVEN)\s*\|')

JUNIT_PATHS: tuple[tuple[str, str], ...] = (
    ('feature_evidence', 'evidence/junit/feature_evidence.xml'),
    ('batch07_platform', 'evidence/batch07/batch07-platform-junit.xml'),
    ('batch07_truth', 'evidence/batch07/batch07-truth-junit.xml'),
    ('batch07_live', 'evidence/batch07/batch07-live-junit.xml'),
)


def _source_files(root: Path = ROOT) -> list[Path]:
    """Find source files while pruning dependencies and generated build trees."""
    source: list[Path] = []
    for directory in SOURCE_ROOTS:
        base = root / directory
        if not base.is_dir():
            continue
        for current, child_dirs, filenames in os.walk(base):
            child_dirs[:] = sorted(name for name in child_dirs if name not in SOURCE_SKIP_PARTS)
            for filename in filenames:
                path = Path(current) / filename
                if path.suffix in SOURCE_SUFFIXES:
                    source.append(path)
    return source


def _parse_junit_file(path: Path) -> dict[str, int]:
    totals = {'tests': 0, 'passed': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    if not path.is_file():
        return totals
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return totals
    root_elem = tree.getroot()
    suites = [root_elem] if root_elem.tag == 'testsuite' else list(root_elem.findall('.//testsuite'))
    for suite in suites:
        tests = int(suite.attrib.get('tests', 0))
        failures = int(suite.attrib.get('failures', 0))
        errors = int(suite.attrib.get('errors', 0))
        skipped = int(suite.attrib.get('skipped', 0))
        totals['tests'] += tests
        totals['failures'] += failures
        totals['errors'] += errors
        totals['skipped'] += skipped
        totals['passed'] += max(0, tests - failures - errors - skipped)
    return totals


def measure_junit_passed(root: Path = ROOT) -> dict[str, object]:
    suites: dict[str, dict[str, int]] = {}
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    for key, rel in JUNIT_PATHS:
        parsed = _parse_junit_file(root / rel)
        suites[key] = parsed
        total_passed += parsed['passed']
        total_failed += parsed['failures'] + parsed['errors']
        total_skipped += parsed['skipped']
    return {
        'passed_tests': total_passed,
        'failed_tests': total_failed,
        'skipped_tests': total_skipped,
        'suites': suites,
    }


def _measure_alembic_head(root: Path = ROOT) -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(root / 'alembic.ini'))
    cfg.set_main_option('script_location', str(root / 'alembic'))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f'expected single Alembic head, got {heads!r}')
    return heads[0]


def measure(root: Path = ROOT) -> dict[str, object]:
    source = _source_files(root)
    collected_pytest_nodes = measure_collected_pytest_nodes(root)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        from app.main import app
    return {
        'source_files': len(source),
        'source_physical_lines': sum(
            len(path.read_text(encoding='utf-8', errors='replace').splitlines()) for path in source
        ),
        'registered_routes': len(app.routes),
        'collected_pytest_nodes': collected_pytest_nodes,
        'passed_tests': 'not measured by collection',
    }


def matrix_status_counts(path: Path = MATRIX_PATH) -> dict[str, int]:
    """Count feature statuses straight out of the generated matrix."""
    if not path.exists():
        return {}
    counts: Counter[str] = Counter()
    for line in path.read_text(encoding='utf-8').splitlines():
        match = MATRIX_ROW.match(line)
        if match:
            counts[match.group(3)] += 1
    return dict(sorted(counts.items()))


def _gate_statuses(root: Path, junit_info: dict[str, object], matrix_counts: dict[str, int]) -> dict[str, str]:
    """Derive gate statuses from on-disk JUnit & benchmark artifacts (never hard-coded)."""
    suites = junit_info.get('suites', {})
    assert isinstance(suites, dict)
    feat = suites.get('feature_evidence', {})
    feat_ok = bool(feat.get('passed', 0) > 0 and feat.get('failures', 0) == 0 and feat.get('errors', 0) == 0)
    truth = suites.get('batch07_truth', {})
    truth_ok = bool(truth.get('passed', 0) > 0 and truth.get('failures', 0) == 0 and truth.get('errors', 0) == 0)
    allowlist_empty = not (root / 'scripts' / 'fake_success_allowlist.txt').exists() or (
        (root / 'scripts' / 'fake_success_allowlist.txt').stat().st_size == 0
    )
    latency_ok = (root / 'docs' / 'LATENCY_BENCHMARK.md').is_file()
    capacity_ok = (root / 'evidence' / 'capacity' / 'voice_capacity_ramp.json').is_file()
    dr_ok = (root / 'evidence' / 'dr' / 'dr_drill_report.json').is_file()
    sbom_ok = len(list((root / 'sbom').glob('*.cdx.json'))) >= 4 if (root / 'sbom').is_dir() else False
    live_count = int(matrix_counts.get('LIVE', 0)) + int(matrix_counts.get('LIVE_PROVEN', 0))

    def _verdict(cond: bool) -> str:
        return 'VERIFIED' if cond else 'UNVERIFIED'

    return {
        'G0': _verdict(allowlist_empty and truth_ok),
        'G1': _verdict(feat_ok),
        'G2': _verdict(feat_ok),
        'G3': _verdict(feat_ok and latency_ok),
        'G4': _verdict(feat_ok and (root / 'widget' / 'embed.js').is_file()),
        'G5': _verdict(feat_ok),
        'G6': _verdict(feat_ok),
        'G7': _verdict(feat_ok),
        'G8': _verdict(feat_ok and (root / 'sdk' / 'node' / 'package.json').is_file()),
        'G9': _verdict(feat_ok and capacity_ok and dr_ok and sbom_ok and live_count >= 38),
    }


def build_stats_document(
    root: Path = ROOT,
    measured: dict[str, object] | None = None,
) -> dict[str, object]:
    stats = measure(root) if measured is None else dict(measured)
    junit_info = measure_junit_passed(root)
    matrix_counts = matrix_status_counts(root / 'docs' / 'SALES' / 'FEATURE_MATRIX_VERIFIED.md')
    total_features = sum(matrix_counts.values())
    existing_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return {
        'schema': 'voxdesk.repo_stats/v1',
        'generated_date': existing_date,
        'source_files': int(stats['source_files']),
        'source_physical_lines': int(stats['source_physical_lines']),
        'registered_routes': int(stats['registered_routes']),
        'collected_pytest_nodes': int(stats['collected_pytest_nodes']),
        'junit_passed_tests': int(junit_info['passed_tests']),
        'junit_failed_tests': int(junit_info['failed_tests']),
        'junit_skipped_tests': int(junit_info['skipped_tests']),
        'junit_suites': junit_info['suites'],
        'alembic_head': _measure_alembic_head(root),
        'feature_matrix': {
            'total': total_features,
            'LIVE': int(matrix_counts.get('LIVE', 0)),
            'API_ONLY': int(matrix_counts.get('API_ONLY', 0)),
            'NOT_CONFIGURED': int(matrix_counts.get('NOT_CONFIGURED', 0)),
            'PLANNED': int(matrix_counts.get('PLANNED', 0)),
        },
        'gates': _gate_statuses(root, junit_info, matrix_counts),
    }


def write_stats_json(root: Path = ROOT, measured: dict[str, object] | None = None) -> dict[str, object]:
    doc = build_stats_document(root, measured=measured)
    out = root / 'docs' / 'STATS.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + '\n', encoding='utf-8')
    return doc


def _status_table(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    if not total:
        return '_No feature matrix has been generated yet. Run_ `python scripts/generate_feature_matrix.py`.'
    lines = ['| Status | Features | Meaning |', '|---|---|---|']
    meanings = {
        'LIVE_PROVEN': 'proven against a real provider account; evidence in `evidence/live/`',
        'LIVE': 'exercised end to end by the automated suite, without a live provider account',
        'API_ONLY': 'implemented and routed, not exercised end to end',
        'NOT_CONFIGURED': 'correctly refuses to operate until the buyer supplies credentials',
        'PLANNED': 'not implemented',
    }
    for status, count in sorted(counts.items(), key=lambda item: -item[1]):
        lines.append(f'| {status} | {count} | {meanings.get(status, "")} |')
    lines.append(f'| **Total** | **{total}** | |')
    return '\n'.join(lines)


README_TEMPLATE = """# VoxDesk

Multi-tenant AI voice-agent platform: inbound and outbound telephony, a
conversation-flow builder, RAG knowledge bases, CRM/helpdesk write-back, and a
React operator console, behind a single FastAPI application.

The status of every capability is generated, not asserted: see
[docs/SALES/FEATURE_MATRIX_VERIFIED.md](docs/SALES/FEATURE_MATRIX_VERIFIED.md)
and [docs/STATS.json](docs/STATS.json).

## Architecture

```mermaid
flowchart TB
    subgraph Edge
        Caddy["Caddy (TLS, HSTS, security headers)"]
    end
    subgraph Carriers
        Twilio[Twilio]
        Telnyx[Telnyx]
        Vonage[Vonage]
        SIP[SIP gateway / Go]
    end
    subgraph App["FastAPI application"]
        API["REST API ({registered_routes} routes)"]
        WS["Web call / media WebSocket"]
        Wh[Webhook signature verification]
    end
    subgraph Agents
        Pipe["Pipecat pipeline (Python)"]
        RT["Realtime gateway (Go)"]
        Tools["HTTP / MCP tools"]
    end
    subgraph Data
        PG[("PostgreSQL 16")]
        Redis[("Redis")]
        Vec[("pgvector / knowledge store")]
        KMS[["KMS envelope encryption"]]
    end
    subgraph Front
        Dash["React console (Vite)"]
        Next["Next.js console"]
        Widget["Web-call widget / SDK"]
    end

    Twilio -->|signed webhook| Wh
    Telnyx -->|signed webhook| Wh
    Vonage -->|signed webhook| Wh
    SIP -->|RTP| RT
    Caddy --> API
    Caddy --> Dash
    Caddy --> Next
    Widget --> WS
    WS --> Pipe
    API --> PG
    API --> Redis
    API --> Vec
    API --> KMS
    Pipe --> Tools
    Pipe --> RT
    RT --> Twilio
    RT --> Telnyx
    Dash --> API
    Next --> API
```

## Measured repository statistics

Generated by `python scripts/repo_stats.py --write-readme` (also persisted to
`docs/STATS.json`). Physical source lines exclude generated/vendor/build trees;
collected tests are not passed tests.

```json
{stats_json}
```

## Feature status at a glance

Read from the generated matrix, never typed by hand. Regenerate with
`python scripts/generate_feature_matrix.py`.

{status_table}

## Quickstart

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt
cp .env.example .env          # then fill in real secrets; the app refuses weak ones
make up                       # full local stack; the API applies migrations
docker compose exec -T api python -m scripts.create_owner --help
```

Open `http://localhost:8080`; the same Nginx origin serves the dashboard,
API, and API documentation. The application refuses to boot with a default or short JWT secret, and in
production it also requires an https `PUBLIC_BASE_URL`, a webhook signature
secret, and E2E mode off. Those checks are enforced by
`tests/security/test_boot_secret_policy.py`.

## Tests and evidence

| Command | What it produces |
|---|---|
| `bash scripts/run_full_tests.sh` | checks Postgres and Redis, migrates to head, runs every chunk with JUnit and a summary table (`docs/TESTING.md`) |
| `python scripts/pytest_chunks.py` | runs the whole population in chunks, JUnit per chunk |
| `python scripts/reconcile_validation_results.py --strict` | fails when collected != recorded; writes `reports/TEST_EVIDENCE_SUMMARY.json` |
| `make staging-proof` | `evidence/staging/<date>/staging_proof.json` from a real `docker compose up --build` |
| `python scripts/live_evidence_run.py --list` | opt-in, cost-guarded live-provider checks -> `evidence/live/*.json` |
| `make polyglot-evidence` | `reports/POLYGLOT_EVIDENCE_SUMMARY.json` for Go, Rust and C++ services |
| `make security-evidence` | `reports/SECURITY_EVIDENCE_SUMMARY.json` |
| `bash scripts/verify_deploy_assets.sh` | validates Helm, Terraform and Caddy for real |
| `make verify-truth` | every number quoted in the docs equals the code |
| `make verify-hygiene` | no generated artifact is tracked by git |

## Frontends

* `dashboard/` (Vite + React 18) is the **canonical production operator console**
  built into `Dockerfile` and served by FastAPI/Caddy in `docker-compose.prod.yml`
  and `infra/helm/voxdesk`.
* `dashboard-next/` (Next.js 16 App Router) is an **experimental, opt-in shadow
  console** (`dashboard-next/README.md`) validated in CI (`npm test`,
  `tsc --noEmit`, `next build`) and not deployed by default in production compose
  or Helm manifests.

## Deployment

* Single host: `docker compose -f docker-compose.prod.yml up --build`
  (Caddy terminates TLS; Postgres and Redis publish no host port).
* Kubernetes: `infra/helm/voxdesk` with `values-prod.yaml` or
  `values-staging.yaml`; validated by `helm lint`, `helm template` and
  `kubeconform`.
* Cloud: `infra/terraform` modules, validated with `fmt -check`, `validate`
  and `tflint`.

Step-by-step instructions for a buyer's team are in
[docs/BUYER_DEPLOY_GUIDE.md](docs/BUYER_DEPLOY_GUIDE.md). Security controls and
the evidence behind them are in [docs/SECURITY_POSTURE.md](docs/SECURITY_POSTURE.md).

## Repository hygiene

The repository tracks source, not output. `bash scripts/repo_cleanup.sh` (dry run
by default, `--apply` to act) untracks logs, archives and oversized generated
files and moves historical reports into `docs/archive/history/`. CI fails if any
of them come back.

## License and handoff

Commercial source-available licence; see `THIRD_PARTY_LICENSES.md` for bundled
dependencies. On handover the buyer receives this repository, the runbooks under
`docs/`, and the evidence generation scripts -- every claim in the sales material
can be regenerated from a clean clone.
"""


def render_readme(stats: dict[str, object], counts: dict[str, int] | None = None) -> str:
    counts = matrix_status_counts() if counts is None else counts
    return README_TEMPLATE.format(
        stats_json=json.dumps(stats, indent=2),
        status_table=_status_table(counts),
        registered_routes=stats['registered_routes'],
    )


def _sync_pricing_snapshot(root: Path, doc: dict[str, object]) -> None:
    pricing_path = root / 'docs' / 'SALES' / 'PRICING_AND_TIERS.md'
    if not pricing_path.exists():
        return
    start_marker = '<!-- >>> voxdesk:repo-stats >>> -->'
    end_marker = '<!-- <<< voxdesk:repo-stats <<< -->'
    text = pricing_path.read_text(encoding='utf-8')
    if start_marker not in text or end_marker not in text:
        return
    fm = doc.get('feature_matrix', {})
    if not isinstance(fm, dict):
        fm = {}
    block = '\n'.join(
        [
            start_marker,
            '## Verified Repository Snapshot (`docs/STATS.json`)',
            '',
            f"- **Source files**: `{int(doc['source_files'])}` (`{int(doc['source_physical_lines']):,}` physical lines)",
            f"- **Registered FastAPI routes**: `{int(doc['registered_routes'])}` registered routes",
            f"- **Collected pytest nodes**: `{int(doc['collected_pytest_nodes'])}` collected tests (`{int(doc['junit_passed_tests']):,}` passed JUnit executions across `evidence/junit/feature_evidence.xml` and `evidence/batch07/`)",
            f"- **Alembic migration head**: `{doc['alembic_head']}`",
            f"- **Verified Feature Matrix**: `{int(fm.get('LIVE', 0))}` `LIVE` / `{int(fm.get('total', 0))}` total (`{int(fm.get('API_ONLY', 0))}` `API_ONLY`, `{int(fm.get('NOT_CONFIGURED', 0))}` `NOT_CONFIGURED`, `{int(fm.get('PLANNED', 0))}` `PLANNED`)",
            end_marker,
        ]
    )
    before, rest = text.split(start_marker, 1)
    _, after = rest.split(end_marker, 1)
    pricing_path.write_text(before + block + after, encoding='utf-8')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-readme', action='store_true', help='write README.md and docs/STATS.json')
    parser.add_argument('--write-stats', action='store_true', help='write docs/STATS.json')
    parser.add_argument(
        '--check',
        action='store_true',
        help='exit 1 if README.md is out of date instead of rewriting it',
    )
    args = parser.parse_args(argv)
    stats = measure()
    if args.write_stats or args.write_readme:
        doc = write_stats_json(ROOT, measured=stats)
        _sync_pricing_snapshot(ROOT, doc)
        print('wrote docs/STATS.json', file=sys.stderr)
    if not args.write_readme and not args.write_stats:
        print(json.dumps(stats, indent=2))
    rendered = render_readme(stats)
    if args.write_readme:
        (ROOT / 'README.md').write_text(rendered, encoding='utf-8')
        print('wrote README.md', file=sys.stderr)
    elif args.check:
        current = (ROOT / 'README.md').read_text(encoding='utf-8') if (ROOT / 'README.md').exists() else ''
        if current.strip() != rendered.strip():
            print(
                'README.md is out of date; run: python scripts/repo_stats.py --write-readme',
                file=sys.stderr,
            )
            raise SystemExit(1)
        print('README.md is up to date', file=sys.stderr)


if __name__ == '__main__':
    main()
