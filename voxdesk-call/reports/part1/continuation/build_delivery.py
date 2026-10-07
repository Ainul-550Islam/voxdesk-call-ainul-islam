"""Rebuild complete current source delivery, retaining the historical 1A report."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[3]
DEST = ROOT / 'reports/part1/continuation'
NEW_FILES = [
    'app/ai/post_call_llm.py',
    'tests/ai/test_post_call_llm.py',
    'reports/part1/continuation/STATUS.md',
    'reports/part1/continuation/build_delivery.py',
]
old_manifest = json.loads((ROOT / 'reports/part1/source-manifest.json').read_text())
with zipfile.ZipFile(ROOT / 'reports/PART_1A_FULL_CODE.zip') as old:
    historical = old.read('reports/PART_1_REPORT.md').decode()
parts = [(DEST / 'STATUS.md').read_text(), '\n# Complete new source files\n']
for name in NEW_FILES:
    raw = (ROOT / name).read_bytes()
    assert b'\x00' not in raw, name
    parts.append('\n## ' + name + '\n\n````````\n' + raw.decode() + '\n````````\n')
parts.append('\n# Current command output\n')
for name in ['ai-regression.log', 'truth-final.log', 'contracts.log', 'ruff.log']:
    parts.append('\n## ' + name + '\n\n````````text\n' + (DEST / name).read_text() + '\n````````\n')
parts.append('\n# Historical 1A report and complete 1A sources\n\n'
             'The continuation status above supersedes old not-started statements. '
             'See it for the restored Git commit hash.\n\n' + historical)
(ROOT / 'reports/PART_1_REPORT.md').write_text(''.join(parts))
paths = sorted({row['path'] for row in old_manifest} | set(NEW_FILES))
manifest = []
for name in paths:
    raw = (ROOT / name).read_bytes()
    manifest.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
(DEST / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
archive = ROOT / 'reports/PART_1_CURRENT_FULL_CODE.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
    names = set(paths) | {'reports/PART_1_REPORT.md', 'reports/part1/continuation/source-manifest.json'}
    for folder in [DEST, ROOT / 'reports/part1/final-1a']:
        for path in folder.glob('*'):
            if path.is_file() and path.suffix in {'.log', '.xml', '.json'}:
                names.add(str(path.relative_to(ROOT)))
    for name in sorted(names):
        bundle.write(ROOT / name, name)
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    for row in manifest:
        assert hashlib.sha256(bundle.read(row['path'])).hexdigest() == row['sha256']
print(f'{len(manifest)} complete source files, SHA-256 verified; {archive}')
