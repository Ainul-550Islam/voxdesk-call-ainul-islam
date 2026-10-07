"""Detect repetitive generated filler without changing any production source."""
from __future__ import annotations
import argparse
import fnmatch
import json
import re
try:
    from scripts.strip_padding_markers import ROOT, sources
except ModuleNotFoundError:
    from strip_padding_markers import ROOT, sources

FORBIDDEN = re.compile(r'/endpoint-\d+|\b(?:Struct|Class)\d+\b|_function_\d+\b|Padding\b[^\n]*\bline\s+\d+|NO SKIP FULL CODE|\b\d{3,}\+\s*lines\b', re.I)
STRINGS = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.X)


def inspect_text(text):
    findings = []
    for n, line in enumerate(text.splitlines(), 1):
        if FORBIDDEN.search(line):
            findings.append({'line': n, 'reason': 'forbidden marker'})
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith(('#', '//', '*'))]
    normalized = [re.sub(r'\d+', 'N', STRINGS.sub('STRING', line)) for line in lines]
    if len(normalized) >= 200:
        ratio = len(set(normalized)) / len(normalized)
        if ratio < .35:
            findings.append({'line': 0, 'reason': 'distinct-line ratio', 'ratio': ratio, 'code_lines': len(normalized)})
    return findings


def scan(root=ROOT):
    allow = root / 'scripts/filler_allowlist.txt'
    patterns = [s.strip() for s in allow.read_text().splitlines() if s.strip() and not s.startswith('#')] if allow.exists() else []
    findings = []
    for pattern in patterns:
        if not (pattern.endswith(('_pb2.py', '.pb.go')) or '/locales/' in pattern or '/fixtures/' in pattern):
            findings.append({'file': 'scripts/filler_allowlist.txt', 'reason': 'exception is not a generated-code category', 'pattern': pattern})
    if findings:
        return findings
    for path in sources(root):
        rel = str(path.relative_to(root))
        if any(fnmatch.fnmatchcase(rel, pattern) for pattern in patterns):
            continue
        findings.extend({'file': rel, **finding} for finding in inspect_text(path.read_text()))
    for name in ('realtime-engine', 'voxdesk-concurrency-engine', 'voxdesk-native-audio-engine'):
        if (root / name).exists():
            findings.append({'file': name, 'reason': 'forbidden filler directory'})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', choices=['json'], default='json')
    parser.parse_args()
    findings = scan()
    print(json.dumps(findings, indent=2))
    return int(bool(findings))


if __name__ == '__main__':
    raise SystemExit(main())
