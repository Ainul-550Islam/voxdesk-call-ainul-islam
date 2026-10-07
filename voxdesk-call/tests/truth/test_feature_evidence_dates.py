"""A regenerated sales matrix must not refresh the age of old test evidence."""
from scripts.generate_feature_matrix import junit_execution_date


def test_uses_oldest_execution_date_not_generation_date(tmp_path):
    file = tmp_path / 'evidence.xml'
    file.write_text('<testsuites><testsuite timestamp="2024-01-02T23:00:00Z"><testcase name="old"/></testsuite><testsuite timestamp="2025-05-06T10:00:00Z"><testcase name="new"/></testsuite></testsuites>')
    assert junit_execution_date(file) == '2024-01-02'


def test_missing_malformed_and_future_timestamps_cannot_verify(tmp_path):
    file = tmp_path / 'evidence.xml'
    for stamp in ['', ' timestamp="unknown"', ' timestamp="2999-01-01T00:00:00Z"']:
        file.write_text(f'<testsuite{stamp}><testcase name="test"/></testsuite>')
        assert junit_execution_date(file) is None
    assert junit_execution_date(None) is None


def test_offsets_are_normalized_to_utc(tmp_path):
    file = tmp_path / 'evidence.xml'
    file.write_text('<testsuite timestamp="2024-01-03T01:00:00+06:00"><testcase name="test"/></testsuite>')
    assert junit_execution_date(file) == '2024-01-02'
