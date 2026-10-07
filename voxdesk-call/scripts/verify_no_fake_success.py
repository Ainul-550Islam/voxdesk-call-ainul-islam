"""Fail on new facade markers, stale exemptions, or allowlist growth."""
from __future__ import annotations
import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = 'scripts/fake_success_allowlist.txt'
PHRASES = re.compile(r'we simulate|simulate token|simulate http|in production this would|in a real implementation|real implementation would|for now we mark|for now, we return', re.I)
FACADE_SEED = frozenset('app/api/' + name + '_routes.py' for name in (
    'webhook_lifecycle', 'salesforce', 'live_monitoring', 'batch_call', 'outbound_call',
    'crm_writeback', 'ab_testing', 'retention', 'post_call_analysis', 'transfer_control', 'multichannel'
)) | {'app/telephony/realtime.py', 'app/integrations/connector.py'}


def entries(text):
    return {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')}


def markers(text):
    found = [{'line': i, 'reason': 'fake-success phrase'} for i, line in enumerate(text.splitlines(), 1) if PHRASES.search(line)]
    lines = text.splitlines()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == 'Exception':
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                near = '\n'.join(lines[max(0, node.lineno - 6):node.end_lineno + 5])
                if re.search(r'audit|outbox|webhook', near, re.I):
                    found.append({'line': node.lineno, 'reason': 'swallowed audit/outbox/webhook exception'})
    return found


def validate(allow, findings, previous):
    errors = []
    for path in sorted(allow - FACADE_SEED):
        errors.append({'file': path, 'reason': 'not in approved facade register'})
    for path in sorted(allow - previous):
        errors.append({'file': path, 'reason': 'allowlist growth'})
    for path in sorted(allow - findings.keys()):
        errors.append({'file': path, 'reason': 'stale allowlist entry'})
    for path in sorted(findings.keys() - allow):
        errors.extend({'file': path, **row} for row in findings[path])
    return errors


def scan(root=ROOT, base_ref="HEAD"):
    path = root / ALLOWLIST
    allow = entries(path.read_text()) if path.exists() else set()
    findings = {}
    for source in sorted((root / 'app').rglob('*.py')):
        result = markers(source.read_text())
        if result:
            findings[str(source.relative_to(root))] = result
    previous = FACADE_SEED
    if base_ref:
        prefix = subprocess.check_output(['git', 'rev-parse', '--show-prefix'], cwd=root, text=True).strip()
        result = subprocess.run(['git', 'show', f'{base_ref}:{prefix}{ALLOWLIST}'], cwd=root, text=True, capture_output=True)
        if result.returncode == 0:
            previous = entries(result.stdout)
        else:
            exists = subprocess.run(['git', 'cat-file', '-e', base_ref], cwd=root, capture_output=True)
            if exists.returncode:
                return [{'file': ALLOWLIST, 'reason': 'invalid comparison revision'}]
            # Deleting the list must not reset its shrink-only history.
            history = subprocess.check_output(
                ['git', 'log', '-n1', '--format=%H', '--diff-filter=AM', base_ref, '--', ALLOWLIST],
                cwd=root, text=True,
            ).strip()
            if history:
                old = subprocess.check_output(['git', 'show', f'{history}:{prefix}{ALLOWLIST}'], cwd=root, text=True)
                previous = entries(old)
    return validate(allow, findings, previous)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-ref', default='HEAD')
    args = parser.parse_args()
    errors = scan(base_ref=args.base_ref)
    print(json.dumps(errors, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
