"""Guard contract tests and an unfiltered production-source gate."""
from pathlib import Path
import pytest
from scripts.verify_no_filler import inspect_text, scan
from scripts.strip_padding_markers import cleaned
from scripts.verify_no_null_bytes import inspect_bytes


def test_repository_has_no_filler():
    assert scan() == []


@pytest.mark.parametrize('text', ['/endpoint-12', 'class Class52:', 'thing_function_27()', '// Padding module line 200', 'NO SKIP FULL CODE', '// Extended Production Tests', '// Test line 900: real test coverage', '// Additional test helper 900'])
def test_forbidden_patterns_cannot_return(text):
    assert inspect_text(text)


def test_normalization_detects_numbered_repetition():
    assert any(row['reason'] == 'distinct-line ratio' for row in inspect_text('\n'.join(f'value_{i} = "literal {i}"' for i in range(220))))


def test_padding_removal_is_idempotent_and_preserves_code():
    source = '# Padding module line 100\nvalue = 7\n'
    cleaned_once, count = cleaned(source, '.py')
    assert count == 1
    assert cleaned_once == 'value = 7\n'
    assert cleaned(cleaned_once, '.py') == (cleaned_once, 0)


def test_null_bytes_and_accidental_empty_source_fail():
    assert inspect_bytes(Path('app/broken.py'), b'code\x00') == ['null byte']
    assert inspect_bytes(Path('app/broken.py'), b'') == ['unexpected empty text file']
    assert inspect_bytes(Path('tests/truth/__init__.py'), b'') == []


def test_broad_filler_exemption_is_rejected(tmp_path):
    directory = tmp_path / 'scripts'
    directory.mkdir()
    (directory / 'filler_allowlist.txt').write_text('app/*\n')
    assert scan(tmp_path)[0]['reason'] == 'exception is not a generated-code category'


def test_large_numbered_noop_helper_loop_is_filler():
    source = '''for (let i = 0; i < 900; i++) {
  const _ = `helper-${i}`;
}'''
    assert any(item['reason'] == 'large no-op numbered helper loop' for item in inspect_text(source))
