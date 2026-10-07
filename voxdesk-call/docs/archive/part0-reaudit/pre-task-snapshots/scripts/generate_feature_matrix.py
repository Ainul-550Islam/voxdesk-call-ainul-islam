"""Generate conservative feature claims from exact JUnit test identities."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'LIVE', 'API_ONLY', 'PLANNED', 'NOT_CONFIGURED'}


def junit_results(path, root=ROOT):
    if path is None:
        return {}
    tree = ET.parse(path)
    if any(int(s.get('errors', '0')) for s in tree.iter('testsuite')):
        return {}
    results = {}
    for case in tree.iter('testcase'):
        classname = case.get('classname', '').split('.')
        node = None
        for i in range(len(classname), 0, -1):
            file = '/'.join(classname[:i]) + '.py'
            if (root / file).is_file():
                node = '::'.join([file, *classname[i:], case.get('name', '')])
                break
        if node:
            passed = not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped'))
            results[node] = results.get(node, True) and passed
    return results


def render(features, results, verified_date):
    ids = set()
    rows = []
    for feature in features:
        if feature['id'] in ids or feature['status'] not in STATUSES:
            raise ValueError('Duplicate feature ID or unsupported status')
        ids.add(feature['id'])
        evidence = feature['evidence_tests']
        if not isinstance(evidence, list) or any(not isinstance(x, str) or '::' not in x for x in evidence):
            raise ValueError('Evidence must contain exact pytest node IDs')
        verified = bool(evidence) and all(results.get(node, False) for node in evidence)
        status = feature['status']
        if status == 'LIVE' and not verified:
            status = 'API_ONLY'
        proof = '; '.join(f'`{node}` ({"pass" if results.get(node) else "not verified"})' for node in evidence) or 'No qualifying execution evidence supplied'
        name = feature['name'].replace('|', '\\|').replace('\n', ' ')
        rows.append(f"| {feature['id']}: {name} | {status} | {proof} | {verified_date if verified else 'Not verified'} |")
    return '# Verified feature matrix\n\nGenerated from tests/truth/feature_manifest.yaml and supplied JUnit evidence. No live-provider or production-readiness claim follows from API_ONLY. Missing, failed, skipped, or ambiguous evidence never establishes LIVE.\n\n| Feature | Status | Evidence | Last verified date |\n|---|---|---|---|\n' + '\n'.join(rows) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'tests/truth/feature_manifest.yaml')
    parser.add_argument('--junit', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/SALES/FEATURE_MATRIX_VERIFIED.md')
    args = parser.parse_args()
    data = yaml.safe_load(args.manifest.read_text())
    text = render(data['features'], junit_results(args.junit), datetime.now(timezone.utc).date().isoformat())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(args.output)


if __name__ == '__main__':
    main()
