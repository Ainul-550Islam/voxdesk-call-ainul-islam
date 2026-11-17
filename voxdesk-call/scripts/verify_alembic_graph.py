#!/usr/bin/env python3
"""Verify the Alembic revision graph without a database or an installed alembic.

Alembic stores ``version_num`` as ``VARCHAR(32)``. A revision id longer than
that, a duplicated id, a ``down_revision`` pointing at nothing, or a second head
all fail *at deploy time* — on the buyer's machine, not in CI. This guard reads
``alembic/versions/*.py`` with :mod:`ast` (so it needs no database, no
``.git`` and no installed alembic), and prints ``[]`` when the graph is sound.

Checks, each reported as a structured finding:

``duplicate_revision``  two files declare the same ``revision``
``overlong_revision``   ``revision`` longer than 32 characters
``missing_revision``    file has no ``revision`` assignment
``missing_down_revision`` file has no ``down_revision`` assignment
``dangling_down_revision`` ``down_revision`` names a revision no file declares
``multiple_heads``      more than one revision is not anybody's parent
``cycle``               the parent links do not terminate

Usage::

    python scripts/verify_alembic_graph.py            # prints JSON findings
    python scripts/verify_alembic_graph.py --summary  # prints the descent path
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = Path('alembic/versions')
MAX_REVISION_LENGTH = 32


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _assigned(tree: ast.Module, name: str) -> tuple[bool, Any]:
    """Return ``(found, value)`` for a module-level ``name = ...``/``name: T = ...``."""
    found = False
    value: Any = None
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        if target == name:
            found = True
            value = _literal(node.value)
    return found, value


def collect(versions_dir: Path) -> dict[str, dict[str, Any]]:
    """Map ``revision -> {files, down_revisions}`` for every migration on disk.

    Duplicate ids are merged into one entry with several ``files`` rather than
    silently overwriting each other, so the duplicate survives to be reported.
    """
    revisions: dict[str, dict[str, Any]] = {}
    for path in sorted(versions_dir.glob('*.py')):
        if path.name.startswith('__'):
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError) as exc:
            revisions[f'<unparsable {path.name}>'] = {'files': [path.as_posix()], 'down_revisions': (), 'error': str(exc)}
            continue
        has_rev, revision = _assigned(tree, 'revision')
        has_down, down = _assigned(tree, 'down_revision')
        if not has_rev or not isinstance(revision, str):
            revisions[f'<missing revision in {path.name}>'] = {
                'files': [path.as_posix()],
                'down_revisions': (),
                'missing_revision': True,
                'missing_down_revision': not has_down,
            }
            continue
        if isinstance(down, (tuple, list)):
            parents = tuple(str(item) for item in down if isinstance(item, str))
        elif isinstance(down, str):
            parents = (down,)
        else:
            parents = ()
        entry = revisions.setdefault(
            revision,
            {'files': [], 'down_revisions': parents, 'missing_down_revision': not has_down},
        )
        entry['files'].append(path.as_posix())
        if parents and not entry['down_revisions']:
            entry['down_revisions'] = parents
    return revisions


def heads(revisions: dict[str, dict[str, Any]]) -> list[str]:
    """Revisions that no other revision lists as a parent."""
    parents = {parent for entry in revisions.values() for parent in entry.get('down_revisions', ())}
    return sorted(rev for rev in revisions if rev not in parents and not rev.startswith('<'))


def descent(revisions: dict[str, dict[str, Any]], head: str) -> tuple[list[str], bool]:
    """Walk from ``head`` to a root. Returns ``(path, cyclic)``."""
    path: list[str] = []
    seen: set[str] = set()
    current: str | None = head
    while current:
        if current in seen:
            return path, True
        seen.add(current)
        path.append(current)
        entry = revisions.get(current)
        if entry is None:
            break
        parents = entry.get('down_revisions') or ()
        current = parents[0] if parents else None
    return path, False


def scan(root: Path = ROOT) -> list[dict[str, Any]]:
    versions_dir = Path(root) / VERSIONS_DIR
    if not versions_dir.is_dir():
        return [{'kind': 'missing_versions_dir', 'path': VERSIONS_DIR.as_posix(), 'reason': 'alembic/versions not found'}]

    revisions = collect(versions_dir)
    findings: list[dict[str, Any]] = []

    for revision, entry in revisions.items():
        path = entry['files'][0]
        if revision.startswith('<'):
            findings.append(
                {
                    'kind': 'missing_revision' if 'missing revision' in revision else 'unparsable_migration',
                    'path': path,
                    'reason': entry.get('error') or 'module does not declare a string `revision`',
                }
            )
            continue
        if len(entry['files']) > 1:
            findings.append(
                {
                    'kind': 'duplicate_revision',
                    'revision': revision,
                    'path': path,
                    'also_in': entry['files'][1:],
                    'reason': 'two migration files declare the same revision id',
                }
            )
        if len(revision) > MAX_REVISION_LENGTH:
            findings.append(
                {
                    'kind': 'overlong_revision',
                    'revision': revision,
                    'path': path,
                    'length': len(revision),
                    'reason': f'version_num is VARCHAR({MAX_REVISION_LENGTH}); {len(revision)} characters will not fit',
                }
            )
        if entry.get('missing_down_revision'):
            findings.append(
                {
                    'kind': 'missing_down_revision',
                    'revision': revision,
                    'path': path,
                    'reason': 'module does not declare `down_revision` (use None for the root)',
                }
            )
        for parent in entry.get('down_revisions', ()):
            if parent not in revisions:
                findings.append(
                    {
                        'kind': 'dangling_down_revision',
                        'revision': revision,
                        'path': path,
                        'down_revision': parent,
                        'reason': 'down_revision names a revision no file declares',
                    }
                )

    graph_heads = heads(revisions)
    if len(graph_heads) > 1:
        findings.append(
            {
                'kind': 'multiple_heads',
                'heads': graph_heads,
                'reason': '`alembic upgrade head` is ambiguous when the graph has more than one head',
            }
        )
    elif not graph_heads and revisions:
        findings.append({'kind': 'no_head', 'reason': 'no revision qualifies as a head (cycle or all-roots graph)'})

    for head in graph_heads:
        path, cyclic = descent(revisions, head)
        if cyclic:
            findings.append({'kind': 'cycle', 'head': head, 'path': path[-6:], 'reason': 'parent links loop'})

    return findings


def summarise(root: Path = ROOT) -> dict[str, Any]:
    revisions = collect(Path(root) / VERSIONS_DIR)
    graph_heads = heads(revisions)
    head = graph_heads[0] if len(graph_heads) == 1 else None
    path = descent(revisions, head)[0] if head else []
    return {
        'migration_files': len(revisions),
        'root': path[-1] if path else None,
        'head': head,
        'heads': graph_heads,
        'chain_length': len(path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=ROOT, help='directory to scan (default: repo root)')
    parser.add_argument('--summary', action='store_true', help='print the descent summary instead of findings')
    args = parser.parse_args(argv)
    if args.summary:
        print(json.dumps(summarise(args.root), indent=2))
        return 0
    findings = scan(args.root)
    print(json.dumps(findings, indent=2))
    return int(bool(findings))


if __name__ == '__main__':
    raise SystemExit(main())
