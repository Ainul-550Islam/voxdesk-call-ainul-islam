"""Never widen the approved facade set or hide stale exemptions."""
from pathlib import Path
from scripts.verify_no_fake_success import FACADE_SEED, markers, scan, validate
from scripts.generate_feature_matrix import junit_results, render


def test_production_fake_success_guard():
    assert scan() == []


def test_phrase_and_swallowed_audit_detection():
    assert markers('# We simulate successful delivery\nx=1\n')
    assert markers('try:\n    audit()\nexcept Exception:\n    pass\n')
    assert not markers('try:\n    unrelated()\nexcept ValueError:\n    pass\n')


def test_allowlist_is_shrink_only_and_stale_entries_fail():
    path = next(iter(FACADE_SEED))
    assert validate({path}, {path: [{'line': 1}]}, set())
    assert validate({path}, {}, {path})
    assert validate(set(), {}, {path}) == []
    assert validate({path}, {path: [{'line': 1}]}, {path}) == []
    assert validate({'app/arbitrary.py'}, {'app/arbitrary.py': []}, {'app/arbitrary.py'})


def test_feature_missing_failed_or_skipped_evidence_cannot_be_live():
    feature = {'id': 1, 'name': 'Delivery', 'status': 'LIVE', 'evidence_tests': ['tests/test_delivery.py::test_send']}
    for result in ({}, {'tests/test_delivery.py::test_send': False}):
        assert '| API_ONLY |' in render([feature], result, '2026-10-07')
    assert '| LIVE |' in render([feature], {'tests/test_delivery.py::test_send': True}, '2026-10-07')
    feature['evidence_tests'] = []
    assert '| API_ONLY |' in render([feature], {}, '2026-10-07')


def test_junit_uses_exact_node_identity_and_failure_dominates_retries(tmp_path):
    source = tmp_path / 'tests/test_delivery.py'
    source.parent.mkdir()
    source.write_text('def test_send():\n    assert True\n')
    xml = tmp_path / 'result.xml'
    xml.write_text('<testsuite><testcase classname="tests.test_delivery" name="test_send"/><testcase classname="tests.test_delivery" name="test_send"><skipped/></testcase></testsuite>')
    assert junit_results(xml, tmp_path) == {'tests/test_delivery.py::test_send': False}
    xml.write_text('<testsuite errors="1"><testcase classname="tests.test_delivery" name="test_send"/></testsuite>')
    assert junit_results(xml, tmp_path) == {}


def test_manifest_has_all_features_and_no_unsupported_live_claim():
    import yaml
    manifest = yaml.safe_load(Path('tests/truth/feature_manifest.yaml').read_text())
    assert len(manifest['features']) == 41
    for feature in manifest['features']:
        assert feature['status'] in {'LIVE', 'API_ONLY', 'PLANNED', 'NOT_CONFIGURED'}
        if feature['status'] == 'LIVE':
            assert feature['evidence_tests']


def test_deleted_allowlist_cannot_reset_shrink_history(tmp_path):
    import subprocess
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, text=True)
    git('init', '-q')
    git('config', 'user.name', 'Contract Test')
    git('config', 'user.email', 'contract@example.com')
    path = tmp_path / 'scripts/fake_success_allowlist.txt'
    path.parent.mkdir()
    path.write_text('# All exemptions have been removed.\n')
    git('add', '.')
    git('commit', '-qm', 'empty register')
    path.unlink()
    git('add', '-u')
    git('commit', '-qm', 'remove register')
    source = tmp_path / 'app/api/batch_call_routes.py'
    source.parent.mkdir(parents=True)
    source.write_text('# We simulate delivery\nx = 1\n')
    path.write_text('app/api/batch_call_routes.py\n')
    errors = scan(tmp_path, 'HEAD')
    assert any(row['reason'] == 'allowlist growth' for row in errors)
