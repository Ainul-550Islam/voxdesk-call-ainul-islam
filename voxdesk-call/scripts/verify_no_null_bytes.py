"""Check tracked and new non-ignored text; permit only intentional empty markers."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = {'.py', '.pyi', '.ts', '.tsx', '.js', '.jsx', '.md', '.txt', '.yaml', '.yml', '.toml', '.json', '.proto', '.sql', '.go', '.rs', '.c', '.cpp', '.h', '.hpp', '.sh', '.ini', '.cfg'}


def inspect_bytes(path, data):
    errors = []
    if b'\x00' in data:
        errors.append('null byte')
    if not data and path.name not in {'__init__.py', '__init__.pyi', 'py.typed', '.gitkeep'}:
        errors.append('unexpected empty text file')
    return errors


def scan(root=ROOT):
    output = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root)
    errors = []
    for name in sorted(set(output.decode().split('\0')) - {''}):
        path = root / name
        if path.is_file() and (path.suffix in TEXT or path.name in {'Makefile', 'Dockerfile', '.gitattributes'}):
            errors.extend({'file': name, 'reason': reason} for reason in inspect_bytes(path, path.read_bytes()))
    return errors


def main():
    errors = scan()
    print(json.dumps(errors, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
