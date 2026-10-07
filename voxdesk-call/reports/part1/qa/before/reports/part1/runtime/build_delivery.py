"""Produce the complete applied PART 1 source and raw verification artifacts."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[3]
DEST = ROOT / 'reports/part1/runtime'
FILES = ['alembic/versions/0050_unify_webhooks.py', 'alembic/versions/0051_post_call_pipeline.py', 'app/ai/post_call_llm.py', 'app/api/outbound_call_routes.py', 'app/api/webhook_lifecycle_routes.py', 'app/core/rate_limit.py', 'app/db/enterprise_models.py', 'app/db/models.py', 'app/db/telephony_models.py', 'app/jobs/idempotency.py', 'app/jobs/registry.py', 'app/jobs/types.py', 'app/outbox/dispatcher.py', 'app/telephony/call_events.py', 'app/telephony/call_state.py', 'app/telephony/callback_reconciliation.py', 'app/telephony/outbound.py', 'app/telephony/post_call.py', 'app/telephony/recording.py', 'app/telephony/transcription.py', 'app/telephony/transfer_service.py', 'app/telephony/twilio_handler.py', 'app/webhooks/call_event_bridge.py', 'app/webhooks/call_event_catalog.py', 'app/webhooks/delivery.py', 'app/webhooks/replay.py', 'app/webhooks/repository.py', 'app/webhooks/retry.py', 'contracts/openapi.json', 'docs/POST_CALL.md', 'docs/WEBHOOKS.md', 'reports/part1/1A_SUMMARY.md', 'reports/part1/build_report.py', 'reports/part1/continuation/STATUS.md', 'reports/part1/continuation/build_delivery.py', 'reports/part1/final-1a/legacy-ddl.json', 'reports/part1/final-1a/verify_concurrency.py', 'reports/part1/final-1a/verify_migration.py', 'reports/part1/runtime/STATUS.md', 'reports/part1/runtime/build_delivery.py', 'reports/part1/runtime/verify_post_call_postgres.py', 'scripts/fake_success_allowlist.txt', 'tests/ai/test_post_call_llm.py', 'tests/operations/test_notification_webhook_email.py', 'tests/telephony/test_post_call_pipeline.py', 'tests/test_enterprise_batch01.py', 'tests/test_external_success_honesty.py', 'tests/truth/routes_snapshot.json', 'tests/webhooks/__init__.py', 'tests/webhooks/conftest.py', 'tests/webhooks/test_call_event_bridge.py', 'tests/webhooks/test_webhook_isolation.py', 'tests/webhooks/test_webhook_lifecycle_real.py']
FILES += ['alembic/versions/0052_analysis_versions.py', 'app/api/post_call_analysis_routes.py', 'app/services/post_call_analysis_service.py', 'tests/telephony/test_custom_analysis.py', 'reports/part1/analysis/STATUS.md']
FILES += ['alembic/versions/0053_analysis_backfill.py', 'app/resilience/idempotency.py', 'tests/telephony/test_analysis_backfill.py', 'reports/part1/backfill/STATUS.md', 'reports/part1/backfill/verify_postgres.py']
LOGS = ['final-regression.log', 'pipeline.log', 'truth.log', 'contracts.log',
        'ruff.log', 'routes.log', 'repo-stats.log', 'runtime-versions.log',
        'migration-upgrade.log', 'migration-downgrade.log', 'migration-reupgrade.log',
        'heads.log', 'postgres-final.log']

parts = [(ROOT / 'reports/part1/backfill/STATUS.md').read_text(), '\n# Historical analysis delivery status\n', (ROOT / 'reports/part1/analysis/STATUS.md').read_text(), '\n# Historical core delivery status\n', (DEST / 'STATUS.md').read_text(), '\n# Complete applied source files\n']
manifest = []
for name in FILES:
    raw = (ROOT / name).read_bytes()
    assert b'\x00' not in raw, name
    digest = hashlib.sha256(raw).hexdigest()
    manifest.append({'path': name, 'bytes': len(raw), 'sha256': digest})
    parts.append(f'\n## `{name}`\n\nSHA-256: `{digest}`\n\n````````\n' + raw.decode() + '\n````````\n')
parts.append('\n# Historical core command outputs\n')
for name in LOGS:
    parts.append(f'\n## {name}\n\n````````text\n' + (DEST / name).read_text() + '\n````````\n')
parts.append('\n# Historical analysis command outputs\n')
for path in sorted((ROOT / 'reports/part1/analysis').glob('*.log')):
    parts.append(f'\n## {path.name}\n\n````````text\n' + path.read_text() + '\n````````\n')
parts.append('\n# Current backfill command outputs\n')
for path in sorted((ROOT / 'reports/part1/backfill').glob('*.log')):
    parts.append(f'\n## {path.name}\n\n````````text\n' + path.read_text() + '\n````````\n')
report = ROOT / 'reports/PART_1_REPORT.md'
report.write_text(''.join(parts))
(DEST / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
archive = ROOT / 'reports/PART_1_MAIN_CODE_APPLIED.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    names = set(FILES) | {'reports/PART_1_REPORT.md', 'reports/part1/runtime/source-manifest.json'}
    for folder in ['reports/part1/final-1a', 'reports/part1/continuation', 'reports/part1/runtime', 'reports/part1/analysis', 'reports/part1/backfill']:
        for path in (ROOT / folder).glob('*'):
            if path.is_file() and path.suffix in {'.log', '.xml', '.json'}:
                names.add(str(path.relative_to(ROOT)))
    for name in sorted(names):
        bundle.write(ROOT / name, name)
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    for row in manifest:
        assert hashlib.sha256(bundle.read(row['path'])).hexdigest() == row['sha256']
print(f'{len(manifest)} complete files; ZIP SHA-256 matches applied working tree')
print(report)
print(archive)
