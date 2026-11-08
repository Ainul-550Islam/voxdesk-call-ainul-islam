"""Fail on new facade markers, corrupt/unparseable Python, stale exemptions, or allowlist growth."""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = 'scripts/fake_success_allowlist.txt'
PHRASES = re.compile(
    r'we simulate|simulate token|simulate http|in production this would|'
    r'in a real implementation|real implementation would|for now we mark|for now, we return',
    re.I,
)
FACADE_SEED = frozenset(
    'app/api/' + name + '_routes.py'
    for name in (
        'webhook_lifecycle', 'salesforce', 'live_monitoring', 'batch_call', 'outbound_call',
        'crm_writeback', 'ab_testing', 'retention', 'post_call_analysis', 'transfer_control',
        'multichannel',
    )
) | {'app/telephony/realtime.py', 'app/integrations/connector.py'}
CORRUPT_PREFIX = 'corrupt or unparseable source'


def entries(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')}


def markers(text: str) -> list[dict[str, object]]:
    if '\x00' in text:
        return [{'line': 1, 'reason': f'{CORRUPT_PREFIX}: null byte'}]
    found: list[dict[str, object]] = [
        {'line': i, 'reason': 'fake-success phrase'}
        for i, line in enumerate(text.splitlines(), 1)
        if PHRASES.search(line)
    ]
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        lineno = getattr(exc, 'lineno', 1) or 1
        msg = getattr(exc, 'msg', str(exc))
        found.append({'line': int(lineno), 'reason': f'{CORRUPT_PREFIX}: {type(exc).__name__}: {msg}'})
        return found

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) and node.type.id == 'Exception':
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                end_lineno = getattr(node, 'end_lineno', node.lineno) or node.lineno
                near = '\n'.join(lines[max(0, node.lineno - 6):end_lineno + 5])
                if re.search(r'audit|outbox|webhook', near, re.I):
                    found.append({'line': node.lineno, 'reason': 'swallowed audit/outbox/webhook exception'})
    return found


def validate(
    allow: set[str],
    findings: dict[str, list[dict[str, object]]],
    previous: set[str],
) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    for path in sorted(allow - FACADE_SEED):
        errors.append({'file': path, 'reason': 'not in approved facade register'})
    for path in sorted(allow - previous):
        errors.append({'file': path, 'reason': 'allowlist growth'})
    for path in sorted(allow - findings.keys()):
        errors.append({'file': path, 'reason': 'stale allowlist entry'})
    for path in sorted(findings.keys()):
        for row in findings[path]:
            reason = str(row.get('reason', ''))
            # Corrupt/unparseable files are never exempted by an allowlist entry.
            if path not in allow or reason.startswith(CORRUPT_PREFIX):
                errors.append({'file': path, **row})
    return errors


def _git_belongs_to_root(root: Path) -> bool:
    try:
        top = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if top.returncode != 0:
        return False
    toplevel = Path(top.stdout.strip()).resolve()
    resolved = root.resolve()
    return resolved in (toplevel, toplevel / 'voxdesk-call') or (resolved / '.git').exists()


def scan(root: Path = ROOT, base_ref: str = 'HEAD') -> list[dict[str, object]]:
    root = Path(root)
    path = root / ALLOWLIST
    allow: set[str] = set()
    if path.exists():
        try:
            allow_raw = path.read_bytes()
            if b'\x00' in allow_raw:
                return [{'file': ALLOWLIST, 'line': 1, 'reason': f'{CORRUPT_PREFIX}: null byte'}]
            allow = entries(allow_raw.decode('utf-8'))
        except (OSError, UnicodeDecodeError) as exc:
            return [{'file': ALLOWLIST, 'line': 1, 'reason': f'{CORRUPT_PREFIX}: {exc}'}]

    findings: dict[str, list[dict[str, object]]] = {}
    app_dir = root / 'app'
    if app_dir.is_dir():
        for source in sorted(app_dir.rglob('*.py')):
            if '__pycache__' in source.parts:
                continue
            rel = source.relative_to(root).as_posix()
            try:
                raw = source.read_bytes()
            except OSError as exc:
                findings[rel] = [{'line': 1, 'reason': f'{CORRUPT_PREFIX}: {exc}'}]
                continue
            if b'\x00' in raw:
                findings[rel] = [{'line': 1, 'reason': f'{CORRUPT_PREFIX}: null byte'}]
                continue
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError as exc:
                findings[rel] = [{'line': 1, 'reason': f'{CORRUPT_PREFIX}: invalid UTF-8 ({exc.reason})'}]
                continue
            result = markers(text)
            if result:
                findings[rel] = result

    previous = set(FACADE_SEED)
    if base_ref and _git_belongs_to_root(root):
        prefix_proc = subprocess.run(
            ['git', 'rev-parse', '--show-prefix'],
            cwd=root,
            text=True,
            capture_output=True,
        )
        prefix = prefix_proc.stdout.strip() if prefix_proc.returncode == 0 else ''
        result = subprocess.run(
            ['git', 'show', f'{base_ref}:{prefix}{ALLOWLIST}'],
            cwd=root,
            text=True,
            capture_output=True,
        )
        if result.returncode == 0:
            previous = entries(result.stdout)
        else:
            exists = subprocess.run(['git', 'cat-file', '-e', base_ref], cwd=root, capture_output=True)
            if exists.returncode:
                return [{'file': ALLOWLIST, 'reason': 'invalid comparison revision'}]
            # Deleting the list must not reset its shrink-only history.
            history_proc = subprocess.run(
                ['git', 'log', '-n1', '--format=%H', '--diff-filter=AM', base_ref, '--', ALLOWLIST],
                cwd=root,
                text=True,
                capture_output=True,
            )
            history = history_proc.stdout.strip() if history_proc.returncode == 0 else ''
            if history:
                old_proc = subprocess.run(
                    ['git', 'show', f'{history}:{prefix}{ALLOWLIST}'],
                    cwd=root,
                    text=True,
                    capture_output=True,
                )
                if old_proc.returncode == 0:
                    previous = entries(old_proc.stdout)
    return validate(allow, findings, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-ref', default='HEAD')
    parser.add_argument('--root', type=Path, default=ROOT, help='directory to scan (default: repo root)')
    args = parser.parse_args(argv)
    errors = scan(root=args.root, base_ref=args.base_ref)
    print(json.dumps(errors, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
