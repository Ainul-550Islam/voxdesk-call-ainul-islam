"""Remove non-executable padding and sales banners; --check never writes."""
from __future__ import annotations
import argparse
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ('app', 'services', 'dashboard/src', 'dashboard-next', 'sdk')
SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.rs', '.go', '.c', '.cc', '.cpp', '.h', '.hpp', '.html'}
SKIP = {'node_modules', 'target', 'build', 'dist', '__pycache__', '.next', '.cache', '.venv'}
BANNER = re.compile(r'NO SKIP FULL CODE|\b\d{3,}\+\s*lines\b', re.I)
PADDING = re.compile(r'^\s*(?:(?:#|//)\s*Padding\b.*|//\s*(?:Extended line \d+ — production implementation detail:|Production test helper \d+: real coverage for exhaustive testing).*)')


def sources(root=ROOT):
    for name in ROOTS:
        for path in sorted((root / name).rglob('*')):
            if path.is_file() and path.suffix in SUFFIXES and not SKIP.intersection(path.relative_to(root).parts):
                yield path


def cleaned(text, suffix):
    lines = text.splitlines(keepends=True)
    drop = set()
    for i, line in enumerate(lines):
        if line.lstrip().startswith('/*') and '*/' in line and BANNER.search(line):
            drop.add(i)
        if PADDING.match(line) or (line.lstrip().startswith(('#', '//', '*')) and BANNER.search(line)):
            drop.add(i)
    if suffix == '.py':
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                if BANNER.search(node.value.value):
                    for i in range(node.lineno - 1, node.end_lineno):
                        if BANNER.search(lines[i]):
                            if node.lineno == node.end_lineno or not any(q in lines[i] for q in ('\"\"\"', "'''")):
                                drop.add(i)
                            else:
                                lines[i] = BANNER.sub('', lines[i])
    result = ''.join(line for i, line in enumerate(lines) if i not in drop)
    if suffix == '.py':
        ast.parse(result)
    return result, len(drop)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    changed = {}
    for path in sources():
        text = path.read_text()
        new, count = cleaned(text, path.suffix)
        if count:
            changed[str(path.relative_to(ROOT))] = count
            if not args.check:
                path.write_text(new)
    print(json.dumps(changed, indent=2))
    return int(bool(changed) and args.check)


if __name__ == '__main__':
    raise SystemExit(main())
