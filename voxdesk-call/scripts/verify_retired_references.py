"""Reject retired-engine references in active source/configuration, not forensic archives."""
from __future__ import annotations
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED = (
    '-'.join(('realtime', 'engine')),
    '-'.join(('voxdesk', 'concurrency', 'engine')),
    '-'.join(('voxdesk', 'native', 'audio', 'engine')),
)
SKIP = {'node_modules', 'target', 'build', 'dist', '__pycache__', '.next', '.cache', '.venv', 'vendor'}
SUFFIXES = {'.py', '.rs', '.go', '.cpp', '.h', '.ts', '.tsx', '.js', '.yml', '.yaml', '.toml', '.sh'}


def _literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'join':
        separator = _literal_string(node.func.value)
        if separator is not None and len(node.args) == 1 and isinstance(node.args[0], (ast.Tuple, ast.List)):
            values = [_literal_string(item) for item in node.args[0].elts]
            if all(item is not None for item in values):
                return separator.join(values)
    return None


def declaration_lines(path, text):
    """Only the literal denylist declaration is exempt, never the whole guard file."""
    if path not in {'scripts/verify_no_filler.py', 'scripts/verify_retired_references.py'}:
        return set()
    lines = set()
    for node in ast.walk(ast.parse(text)):
        value = node.iter if isinstance(node, ast.For) else node.value if isinstance(node, ast.Assign) else None
        if isinstance(value, ast.Tuple) and all(_literal_string(v) is not None for v in value.elts):
            if tuple(_literal_string(v) for v in value.elts) == RETIRED:
                lines.update(range(value.lineno, value.end_lineno + 1))
    return lines


def scan(root=ROOT):
    files = {p for directory in ('app', 'services', 'scripts', '.github', 'dashboard/src', 'dashboard-next', 'sdk')
             for p in (root / directory).rglob('*')
             if p.is_file() and p.suffix in SUFFIXES and not SKIP.intersection(p.relative_to(root).parts)}
    files.update(root.glob('docker-compose*.yml'))
    files.update(root / n for n in ('Makefile', 'Dockerfile', 'README.md') if (root / n).exists())
    files.update((root / 'docs/SALES').glob('*.md'))
    findings = []
    for path in sorted(files):
        text = path.read_text()
        relative = str(path.relative_to(root))
        declarations = declaration_lines(relative, text) if path.suffix == '.py' else set()
        for number, line in enumerate(text.splitlines(), 1):
            if number not in declarations and any(name in line for name in RETIRED):
                findings.append({'file': relative, 'line': number, 'reason': 'retired engine reference in active surface'})
    return findings


if __name__ == '__main__':
    findings = scan()
    print(json.dumps(findings, indent=2))
    raise SystemExit(bool(findings))
