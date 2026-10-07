"""Build the complete 1A source/evidence delivery without changing runtime code."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[2]
files = [
    'alembic/versions/0050_unify_webhooks.py',
    'app/api/webhook_lifecycle_routes.py',
    'app/api/outbound_call_routes.py',
    'app/core/rate_limit.py',
    'app/db/models.py',
    'app/db/enterprise_models.py',
    'app/outbox/dispatcher.py',
    'app/webhooks/call_event_catalog.py',
    'app/webhooks/call_event_bridge.py',
    'app/webhooks/delivery.py',
    'app/webhooks/repository.py',
    'app/webhooks/replay.py',
    'app/webhooks/retry.py',
    'app/telephony/call_state.py',
    'app/telephony/call_events.py',
    'app/telephony/twilio_handler.py',
    'app/telephony/callback_reconciliation.py',
    'app/telephony/transfer_service.py',
    'app/telephony/recording.py',
    'app/telephony/outbound.py',
    'tests/webhooks/__init__.py',
    'tests/webhooks/conftest.py',
    'tests/webhooks/test_call_event_bridge.py',
    'tests/webhooks/test_webhook_lifecycle_real.py',
    'tests/webhooks/test_webhook_isolation.py',
    'tests/operations/test_notification_webhook_email.py',
    'tests/test_external_success_honesty.py',
    'tests/truth/routes_snapshot.json',
    'scripts/fake_success_allowlist.txt',
    'contracts/openapi.json',
    'docs/WEBHOOKS.md',
    'reports/part1/1A_SUMMARY.md',
    'reports/part1/final-1a/verify_migration.py',
    'reports/part1/final-1a/verify_concurrency.py',
    'reports/part1/final-1a/legacy-ddl.json',
    'reports/part1/build_report.py',
]
logs = [
    'regression.log', 'truth.log', 'contracts.log', 'ruff.log',
    'test_call_event_bridge.log', 'test_webhook_lifecycle_real.log',
    'test_webhook_isolation.log', 'repo-stats.log', 'openapi-review.log',
    'migration-contract.log', 'migration-missing-key-rejected.log',
    'migration-seeded-upgrade.log', 'migration-idempotent-head.log',
    'downgrade.log', 'reupgrade.log', 'heads.log', 'concurrency.log',
]
parts = [(root / 'reports/part1/1A_SUMMARY.md').read_text(),
         '\n# Complete changed/new files\n']
manifest = []
for name in files:
    raw = (root / name).read_bytes()
    assert b'\x00' not in raw, name
    body = raw.decode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    manifest.append({'path': name, 'bytes': len(raw), 'sha256': digest})
    fence = '`' * max(8, max((len(run) for run in body.split('\n')
                             if run and set(run) == {'`'}), default=0) + 1)
    parts.append(f'\n## `{name}`\n\nSHA-256: `{digest}`\n\n{fence}\n{body}\n{fence}\n')
parts.append('\n# Actual command output\n')
for name in logs:
    text = (root / 'reports/part1/final-1a' / name).read_text()
    parts.append(f'\n## `{name}`\n\n````````text\n{text}\n````````\n')
report = root / 'reports/PART_1_REPORT.md'
report.write_text(''.join(parts))
manifest_path = root / 'reports/part1/source-manifest.json'
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
archive = root / 'reports/PART_1A_FULL_CODE.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    names = set(files + ['reports/PART_1_REPORT.md', 'reports/part1/source-manifest.json'])
    for path in (root / 'reports/part1/final-1a').rglob('*'):
        if path.is_file() and path.suffix in {'.log', '.xml', '.json'}:
            names.add(str(path.relative_to(root)))
    for name in sorted(names):
        bundle.write(root / name, name)
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    for entry in manifest:
        assert hashlib.sha256(bundle.read(entry['path'])).hexdigest() == entry['sha256']
print(f'{len(files)} complete files; archive contents verified against SHA-256 manifest')
print(f'{report}: {report.stat().st_size} bytes')
print(f'{archive}: {archive.stat().st_size} bytes')
