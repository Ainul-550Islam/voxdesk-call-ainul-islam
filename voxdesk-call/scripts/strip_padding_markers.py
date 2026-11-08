"""Remove non-executable padding and sales banners; --check never writes.

Corrupt or unparseable source files (NUL bytes, invalid UTF-8, invalid Python
syntax) are reported as structured findings in the JSON output and exit nonzero
instead of raising an unhandled traceback.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ('app', 'services', 'dashboard/src', 'dashboard-next', 'sdk')
SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.rs', '.go', '.c', '.cc', '.cpp', '.h', '.hpp', '.html'}
SKIP = {'node_modules', 'target', 'build', 'dist', 'vendor', '__pycache__', '.next', '.cache', '.venv'}
BANNER = re.compile(r'NO SKIP FULL CODE|\b\d{3,}\+\s*lines\b', re.I)
PADDING = re.compile(
    r'^\s*(?:(?:#|//)\s*Padding\b.*|//\s*(?:Extended [\w.-]*\s*line \d+ — production (?:implementation detail|hook logic):|Production (?:test helper|line) \d+:\s*real (?:coverage|logic)\b|Real (?:production|analytics|handoff) helper(?:\s+\d+|\s+for\b)|==================== Extended (?:Production|Real Production)\b).*)'
)


def sources(root: Path = ROOT):
    root = Path(root)
    for name in ROOTS:
        base = root / name
        if not base.exists():
            continue
        for path in sorted(base.rglob('*')):
            if path.is_file() and path.suffix in SUFFIXES and not SKIP.intersection(path.relative_to(root).parts):
                yield path


def cleaned(text: str, suffix: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    drop: set[int] = set()
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
                    end_lineno = getattr(node, 'end_lineno', node.lineno) or node.lineno
                    for i in range(node.lineno - 1, end_lineno):
                        if BANNER.search(lines[i]):
                            if node.lineno == end_lineno or not any(q in lines[i] for q in ('"""', "'''")):
                                drop.add(i)
                            else:
                                lines[i] = BANNER.sub('', lines[i])
    result = ''.join(line for i, line in enumerate(lines) if i not in drop)
    if suffix == '.py':
        ast.parse(result)
    return result, len(drop)


def scan(root: Path = ROOT, *, check: bool = True) -> dict[str, object]:
    root = Path(root)
    changed: dict[str, object] = {}
    for path in sources(root):
        rel = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            changed[rel] = {'error': f'unreadable file: {exc}'}
            continue
        if b'\x00' in raw:
            changed[rel] = {'error': 'null byte in source'}
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            changed[rel] = {'error': f'invalid UTF-8: {exc.reason}'}
            continue
        try:
            new, count = cleaned(text, path.suffix)
        except (SyntaxError, ValueError) as exc:
            lineno = getattr(exc, 'lineno', 1) or 1
            msg = getattr(exc, 'msg', str(exc))
            changed[rel] = {'error': f'unparseable source at line {lineno}: {type(exc).__name__}: {msg}'}
            continue
        if count:
            changed[rel] = count
            if not check:
                path.write_text(new, encoding='utf-8')
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT, help='directory to scan (default: repo root)')
    args = parser.parse_args(argv)
    changed = scan(args.root, check=args.check)
    print(json.dumps(changed, indent=2))
    has_errors = any(isinstance(val, dict) and 'error' in val for val in changed.values())
    return int(has_errors or (bool(changed) and args.check))


if __name__ == '__main__':
    raise SystemExit(main())
