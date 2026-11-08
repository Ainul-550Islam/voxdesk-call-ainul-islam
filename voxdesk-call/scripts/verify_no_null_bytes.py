"""Check tracked and new non-ignored text; permit only intentional empty markers.

Works both inside a git checkout (``git ls-files``) and on a fresh archive or
temporary directory where ``.git`` is absent (``os.walk`` fallback).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = {
    '.py', '.pyi', '.ts', '.tsx', '.js', '.jsx', '.md', '.txt', '.yaml', '.yml',
    '.toml', '.json', '.proto', '.sql', '.go', '.rs', '.c', '.cpp', '.h', '.hpp',
    '.sh', '.ini', '.cfg',
}
SPECIAL_TEXT_NAMES = {'Makefile', 'Dockerfile', '.gitattributes'}
ALLOWED_EMPTY_NAMES = {
    '__init__.py', '__init__.pyi', 'py.typed', '.gitkeep', 'fake_success_allowlist.txt',
}
WALK_SKIP_DIRS = {
    '.git', 'node_modules', '.venv', 'venv', '__pycache__', 'target', 'build',
    'dist', 'vendor', '.next', '.cache', '.pytest_cache', '.mypy_cache',
    '.ruff_cache', 'artifacts',
}


def inspect_bytes(path: Path, data: bytes) -> list[str]:
    errors: list[str] = []
    if b'\x00' in data:
        errors.append('null byte')
    if not data and path.name not in ALLOWED_EMPTY_NAMES:
        errors.append('unexpected empty text file')
    if data and b'\x00' not in data:
        try:
            data.decode('utf-8')
        except UnicodeDecodeError as exc:
            errors.append(f'invalid UTF-8 ({exc.reason})')
    return errors


def _git_candidate_files(root: Path) -> list[str] | None:
    """Return git-listed relative paths when ``root`` belongs to its own repo."""
    try:
        top = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0:
        return None
    toplevel = Path(top.stdout.strip()).resolve()
    resolved_root = root.resolve()
    # Accept git listing only when scanning the repo root itself (or its voxdesk-call subdir),
    # never an unrelated scratch child directory that lacks its own .git.
    if resolved_root not in (toplevel, toplevel / 'voxdesk-call') and not (resolved_root / '.git').exists():
        return None
    try:
        proc = subprocess.run(
            ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
            cwd=root,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return sorted(set(proc.stdout.decode('utf-8', errors='replace').split('\0')) - {''})


def _walk_candidate_files(root: Path) -> list[str]:
    """Fallback tree walk used when ``.git`` is absent (fresh tarball / tmp tree)."""
    names: list[str] = []
    for current, child_dirs, filenames in os.walk(root):
        child_dirs[:] = sorted(d for d in child_dirs if d not in WALK_SKIP_DIRS)
        cur_path = Path(current)
        for filename in sorted(filenames):
            rel = (cur_path / filename).relative_to(root).as_posix()
            names.append(rel)
    return sorted(names)


def scan(root: Path = ROOT) -> list[dict[str, str]]:
    root = Path(root)
    candidates = _git_candidate_files(root)
    if candidates is None:
        candidates = _walk_candidate_files(root)
    errors: list[dict[str, str]] = []
    for name in candidates:
        path = root / name
        if path.is_file() and (path.suffix in TEXT or path.name in SPECIAL_TEXT_NAMES):
            try:
                raw = path.read_bytes()
            except OSError as exc:
                errors.append({'file': name, 'reason': f'unreadable file: {exc}'})
                continue
            errors.extend({'file': name, 'reason': reason} for reason in inspect_bytes(path, raw))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='directory to scan (default: repo root)')
    args = parser.parse_args(argv)
    errors = scan(args.root)
    print(json.dumps(errors, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
