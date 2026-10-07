"""Safely remove unreferenced numbered TypeScript declarations, never component heads.

Check mode needs only Python. Apply mode uses the installed Babel TypeScript parser
in dashboard/node_modules; npm ci must run first. Every file is read in full,
all candidate edits are checked before any write, and imported/referenced
symbols are refused. Output describes removed and remaining physical lines.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'node_modules', '.next', 'dist', 'build', '.git', '.cache'}
DECL = re.compile(r'^[ \t]*(?:export\s+)?(?:declare\s+)?(?:async\s+)?(?:function|const|let|var|class|interface|type)\s+([A-Za-z_$][\w$]*_\d+)\b', re.M)
EXPLICIT = re.compile(r'(?:_real_|_CONST_)\d+$')
MARKERS = re.compile(r'\b[A-Za-z_$][\w$]*(?:_real_|_CONST_)\d+\b')
FLAGS = re.compile(r'\bverified\s*:\s*true\s*,\s*real\s*:\s*true\b')


def source_files(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and p.suffix in {'.ts', '.tsx', '.js', '.jsx', '.mjs'}
                  and not SKIP.intersection(p.relative_to(root).parts))


def candidates(text):
    names = DECL.findall(text)
    counts = Counter(re.sub(r'_\d+$', '', name) for name in names)
    return sorted({name for name in names if EXPLICIT.search(name) or counts[re.sub(r'_\d+$', '', name)] >= 15})


def inspect(text):
    return {'symbols': sorted(set(candidates(text)) | set(MARKERS.findall(text))), 'verification_flags': bool(FLAGS.search(text))}


def plan(paths, writable=None):
    """Parse complete sources; refuse dependencies and edits outside top-level AST nodes."""
    source = {p: p.read_text() for p in paths}
    edits = {}
    writable = set(paths) if writable is None else set(writable)
    for path, text in source.items():
        names = candidates(text)
        has_verification_flags = bool(FLAGS.search(text))
        if (not names and not has_verification_flags) or path not in writable:
            continue
        result = subprocess.run(['node', str(ROOT / 'scripts/strip_generated_tails.mjs'), str(path), json.dumps(names)],
                                text=True, capture_output=True, check=True)
        ranges = json.loads(result.stdout)
        remaining = text
        for start, end in reversed(ranges):
            remaining = remaining[:start] + remaining[end:]
        edits[path] = remaining.rstrip() + "\n"
    references = defaultdict(set)
    for consumer, original in source.items():
        for word in set(re.findall(r'[A-Za-z_$][\w$]*', edits.get(consumer, original))):
            references[word].add(consumer)
    for path, remaining in edits.items():
        names = candidates(source[path])
        for name in names:
            if references.get(name):
                raise ValueError(f'{path}: {name} referenced by {sorted(map(str, references[name]))}')
        # Namespace imports / star re-exports can consume generated symbols
        # without naming them. Resolve relative modules conservatively.
        for consumer, content in source.items():
            for match in re.finditer(r'(?:import\s*\*\s*as\s*\w+\s*from|export\s*\*\s*from)\s*[\'"]([^\'"]+)[\'"]', content):
                target = (consumer.parent / match.group(1)).resolve()
                if target == path.resolve() or any(target.with_suffix(ext) == path.resolve() for ext in ('.ts', '.tsx', '.js', '.jsx')):
                    raise ValueError(f'{consumer}: namespace import/export may reference generated declarations in {path}')
    return source, edits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('paths', nargs='*', default=['dashboard/src', 'dashboard-next'])
    args = parser.parse_args()
    for name in args.paths:
        if not (ROOT / name).is_dir():
            parser.error(f'Source directory does not exist: {name}')
    selected = sorted({p for name in args.paths for p in source_files(ROOT / name)})
    if args.check:
        findings = []
        for path in selected:
            finding = inspect(path.read_text())
            if finding['symbols'] or finding['verification_flags']:
                findings.append({'file': str(path.relative_to(ROOT)), **finding})
        print(json.dumps(findings, indent=2))
        return int(bool(findings))
    # References in either UI or SDK block deletion, even if only one UI was selected.
    all_paths = sorted(set(selected) | {p for folder in ('dashboard/src', 'dashboard-next', 'sdk') for p in source_files(ROOT / folder)})
    originals, edits = plan(all_paths, writable=selected)
    report = []
    for path in selected:
        if path not in edits:
            continue
        new = edits[path]
        if inspect(new)['symbols'] or FLAGS.search(new):
            raise ValueError(f'{path}: unresolved generated content remains')
        remaining_lines = new.splitlines()
        code_lines = sum(
            bool(line.strip()) and not line.lstrip().startswith(('//', '/*', '*', '*/'))
            for line in remaining_lines
        )
        report.append({
            'file': str(path.relative_to(ROOT)),
            'removed_lines': len(originals[path].splitlines()) - len(remaining_lines),
            'remaining_lines': len(remaining_lines),
            'remaining_code_lines': code_lines,
            'review_short_head': code_lines < 10,
        })
    # All validation completed before changing the first source file.
    for path in selected:
        if path in edits:
            path.write_text(edits[path])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
