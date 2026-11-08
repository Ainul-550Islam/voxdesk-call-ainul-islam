#!/usr/bin/env python3
"""Enforce repository size and binary/archive hygiene budgets.

Fails when any of the following hold:
1. A tracked or non-ignored working-tree archive / static binary exists
   (``*.zip``, ``*.tar``, ``*.tgz``, ``*.tar.gz``, ``*.7z``, ``*.rar``, ``*.a``,
   ``*.so``, ``*.dylib``, ``*.dll``, ``*.whl``).
2. Any single file exceeds 2 MiB (``contracts/openapi.json`` and
   ``docs/api/openapi.json`` are explicitly allowlisted).
3. Total repository tree size exceeds 60 MiB (target <= 40 MiB).

Works both inside a git checkout and on a fresh export without ``.git``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MIB = 1024 * 1024
MAX_FILE_BYTES = 2 * MIB
TARGET_TREE_BYTES = 40 * MIB
MAX_TREE_BYTES = 60 * MIB

ALLOWLISTED_LARGE_FILES = frozenset(
    {
        'contracts/openapi.json',
        'docs/api/openapi.json',
    }
)

FORBIDDEN_SUFFIXES = frozenset(
    {
        '.zip', '.tar', '.tgz', '.7z', '.rar', '.a', '.so', '.dylib', '.dll', '.whl',
    }
)
FORBIDDEN_COMPOUND_SUFFIXES = ('.tar.gz', '.tar.bz2', '.tar.xz')

SKIP_DIRS = frozenset(
    {
        '.git', 'node_modules', '.venv', 'venv', '__pycache__', 'target',
        'build', 'dist', 'vendor', '.next', '.cache', '.pytest_cache',
        '.mypy_cache', '.ruff_cache', 'artifacts',
    }
)


def _git_files(root: Path) -> list[str] | None:
    try:
        top = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0:
        return None
    toplevel = Path(top.stdout.strip()).resolve()
    resolved = root.resolve()
    if resolved not in (toplevel, toplevel / 'voxdesk-call') and not (resolved / '.git').exists():
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


def _walk_files(root: Path) -> list[str]:
    rels: list[str] = []
    for current, child_dirs, filenames in os.walk(root):
        child_dirs[:] = sorted(d for d in child_dirs if d not in SKIP_DIRS)
        cur = Path(current)
        for name in sorted(filenames):
            rel = (cur / name).relative_to(root).as_posix()
            rels.append(rel)
    return sorted(rels)


def _is_forbidden_archive(rel: str) -> bool:
    lower = rel.lower()
    if lower.endswith(FORBIDDEN_COMPOUND_SUFFIXES):
        return True
    return Path(lower).suffix in FORBIDDEN_SUFFIXES


def inspect_tree(
    root: Path = ROOT,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_tree_bytes: int = MAX_TREE_BYTES,
    target_tree_bytes: int = TARGET_TREE_BYTES,
) -> dict[str, object]:
    root = Path(root)
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)

    findings: list[dict[str, object]] = []
    total_bytes = 0
    counted_files = 0

    for rel in candidates:
        parts = Path(rel).parts
        if SKIP_DIRS.intersection(parts):
            continue
        full = root / rel
        if not full.is_file() or full.is_symlink():
            continue
        size = full.stat().st_size
        total_bytes += size
        counted_files += 1

        if _is_forbidden_archive(rel):
            findings.append(
                {
                    'kind': 'forbidden_archive_or_binary',
                    'path': rel,
                    'bytes': size,
                    'reason': f'tracked/present archive or binary ({Path(rel).suffix}) is forbidden',
                }
            )
        if size > max_file_bytes and rel not in ALLOWLISTED_LARGE_FILES:
            findings.append(
                {
                    'kind': 'oversized_file',
                    'path': rel,
                    'bytes': size,
                    'limit_bytes': max_file_bytes,
                    'reason': f'file size {size} bytes exceeds {max_file_bytes} byte limit',
                }
            )

    if total_bytes > max_tree_bytes:
        findings.append(
            {
                'kind': 'tree_budget_exceeded',
                'path': '.',
                'bytes': total_bytes,
                'limit_bytes': max_tree_bytes,
                'reason': f'repository tree {total_bytes} bytes exceeds {max_tree_bytes} byte ceiling',
            }
        )

    return {
        'ok': not findings,
        'file_count': counted_files,
        'tree_bytes': total_bytes,
        'tree_mib': round(total_bytes / MIB, 2),
        'target_mib': round(target_tree_bytes / MIB, 2),
        'max_mib': round(max_tree_bytes / MIB, 2),
        'within_target': total_bytes <= target_tree_bytes,
        'findings': findings,
    }


def scan(
    root: Path = ROOT,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_tree_bytes: int = MAX_TREE_BYTES,
) -> list[dict[str, object]]:
    result = inspect_tree(
        root,
        max_file_bytes=max_file_bytes,
        max_tree_bytes=max_tree_bytes,
    )
    findings = result['findings']
    assert isinstance(findings, list)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='repository root to inspect')
    parser.add_argument('--max-file-bytes', type=int, default=MAX_FILE_BYTES)
    parser.add_argument('--max-tree-bytes', type=int, default=MAX_TREE_BYTES)
    parser.add_argument(
        '--summary',
        action='store_true',
        help='print full tree size summary object instead of the findings array',
    )
    args = parser.parse_args(argv)

    summary = inspect_tree(
        args.root,
        max_file_bytes=args.max_file_bytes,
        max_tree_bytes=args.max_tree_bytes,
    )
    if args.summary:
        print(json.dumps(summary, indent=2))
    else:
        print(json.dumps(summary['findings'], indent=2))
    return 0 if summary['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
