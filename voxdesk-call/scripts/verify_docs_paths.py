#!/usr/bin/env python3
"""Verify every backticked repository path in README, SALES, and DUE_DILIGENCE docs exists.

Scans ``README.md``, ``docs/SALES/*.md``, and ``docs/DUE_DILIGENCE/*.md`` outside
fenced code blocks and checks that every backticked repository file or directory
reference resolves on disk.

Exit codes: ``0`` all paths exist, ``1`` one or more missing paths.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REPO_TOP_LEVEL = frozenset(
    {
        'app', 'bench', 'services', 'dashboard', 'dashboard-next', 'sdk', 'scripts', 'tests',
        'docs', 'contracts', 'alembic', 'infra', 'observability', 'loadtest',
        'evidence', 'reports', 'sbom', 'widget', 'assets', 'audit',
        'security_harness', 'marketing', '.github', 'Makefile', 'Dockerfile',
        'LICENSE', 'NOTICE', 'SECURITY.md', 'CHANGELOG.md',
        'THIRD_PARTY_LICENSES.md', 'README.md', '.env.example', '.gitignore',
        '.gitattributes', 'requirements.txt', 'requirements-test.txt',
        'docker-compose.yml', 'docker-compose.prod.yml', 'docker-compose.staging.yml',
        'docker-compose.full.yml', 'Caddyfile',
    }
)

FILE_SUFFIXES = frozenset(
    {
        '.py', '.pyi', '.ts', '.tsx', '.js', '.jsx', '.md', '.json', '.yaml', '.yml',
        '.sh', '.sql', '.proto', '.toml', '.rs', '.go', '.c', '.cpp', '.h', '.hpp',
        '.html', '.css', '.wav', '.xml', '.csv', '.txt', '.ini', '.cfg', '.tpl',
    }
)

IGNORE_PREFIXES = (
    'http://', 'https://', 'ws://', 'wss://', '/', '@',
    'autoscaling/', 'policy/', 'voxdesk.io/', 'text/', 'application/', 'audio/',
    'dist/',
)

COMMAND_VERBS = frozenset(
    {
        'python', 'python3', 'pytest', 'bash', 'sh', 'make', 'npm', 'npx',
        'cargo', 'go', 'docker', 'uvicorn', 'alembic', 'ls', 'cp', 'cd',
    }
)


def target_documents(root: Path = ROOT) -> list[Path]:
    docs: list[Path] = []
    readme = root / 'README.md'
    if readme.is_file():
        docs.append(readme)
    for subdir in ('docs/SALES', 'docs/DUE_DILIGENCE'):
        folder = root / subdir
        if folder.is_dir():
            docs.extend(sorted(folder.glob('*.md')))
    return docs


def _normalize_candidate(raw: str) -> str | None:
    tok = raw.strip().rstrip('.,:;)')
    if not tok or '=' in tok:
        return None
    if tok.startswith('/widget/'):
        return tok.lstrip('/')
    if tok.startswith(IGNORE_PREFIXES):
        return None
    # Strip pytest node id (`tests/foo.py::test_bar`)
    tok = tok.split('::', 1)[0]
    # Strip line range (`app/foo.py:42` or `app/foo.py:10-20`)
    if ':' in tok:
        lhs, rhs = tok.split(':', 1)
        if rhs[:1].isdigit():
            tok = lhs
        else:
            return None
    first = tok.split('/', 1)[0]
    if first in REPO_TOP_LEVEL:
        return tok
    if '/' in tok and Path(tok).suffix in FILE_SUFFIXES and not first.startswith('-'):
        return tok
    return None


def extract_candidates(token: str) -> list[str]:
    stripped = token.strip()
    if not stripped or '->' in stripped or '→' in stripped:
        return []
    parts = stripped.split()
    if len(parts) > 1:
        if parts[0] not in COMMAND_VERBS and not parts[0].startswith('./'):
            return []
        found: list[str] = []
        for idx, part in enumerate(parts):
            # Skip output target immediately after --output / -o
            if idx > 0 and parts[idx - 1] in {'--output', '-o', '--results-out', '--junit-out'}:
                continue
            cand = _normalize_candidate(part.lstrip('./') if part.startswith('./') else part)
            if cand:
                found.append(cand)
        return found

    cand = _normalize_candidate(stripped[2:] if stripped.startswith('./') else stripped)
    return [cand] if cand else []


def _path_exists(root: Path, candidate: str) -> bool:
    # Handle template/wildcard paths like `sbom/*.cdx.json` or `evidence/staging/<date>/...`
    if any(ch in candidate for ch in ('*', '<', '>', '{', '}', '…')):
        prefix = re.split(r'[*<>{}…]', candidate, maxsplit=1)[0].rstrip('/')
        if not prefix:
            return True
        base = root / prefix
        if not base.exists():
            return False
        if '*' in candidate and '<' not in candidate and '{' not in candidate:
            parent = (root / candidate).parent
            pattern = Path(candidate).name
            if parent == base and pattern != '*':
                # e.g. `evidence/live/*.json` is an opt-in output directory; parent existence suffices
                # when directory is empty, while populated dirs like `sbom/*.cdx.json` also match.
                return parent.is_dir()
        return True
    return (root / candidate).exists()


def scan(root: Path = ROOT) -> list[dict[str, object]]:
    root = Path(root)
    findings: list[dict[str, object]] = []
    for doc in target_documents(root):
        rel_doc = doc.relative_to(root).as_posix()
        in_fence = False
        for lineno, line in enumerate(doc.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if line.lstrip().startswith('```'):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in re.finditer(r'`([^`\n]+)`', line):
                raw_token = match.group(1)
                for candidate in extract_candidates(raw_token):
                    if not _path_exists(root, candidate):
                        findings.append(
                            {
                                'document': rel_doc,
                                'line': lineno,
                                'token': raw_token,
                                'path': candidate,
                                'reason': 'missing repo path',
                            }
                        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='repository root to scan')
    args = parser.parse_args(argv)
    findings = scan(args.root)
    print(json.dumps(findings, indent=2))
    return int(bool(findings))


if __name__ == '__main__':
    raise SystemExit(main())
