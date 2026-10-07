"""Python-only policy tests; the Vite suite independently tests the Babel parser."""
import json
import re
from types import SimpleNamespace

import pytest

from scripts import strip_generated_tails as tails
from scripts.strip_padding_markers import cleaned
from scripts.verify_no_filler import inspect_text


def test_numbered_definitions_and_threshold():
    assert tails.candidates('export function component_real_0() {}') == ['component_real_0']
    assert tails.candidates('export const COMPONENT_CONST_1 = 1;') == ['COMPONENT_CONST_1']
    assert tails.candidates('\n'.join(f'export const helper_{i} = {i};' for i in range(14))) == []
    assert len(tails.candidates('\n'.join(f'export const helper_{i} = {i};' for i in range(15)))) == 15
    assert tails.inspect('verified: true,\n real: true')['verification_flags']
    assert tails.inspect('const value = page_real_123;')['symbols'] == ['page_real_123']


@pytest.fixture
def parser_seam(monkeypatch):
    """Return spans for trivial fixture declarations, not a substitute parser test."""
    def parse_fixture(command, **kwargs):
        text = __import__('pathlib').Path(command[2]).read_text()
        names = json.loads(command[3])
        spans = []
        if not names:
            match = re.search(r'verified: true, real: true,', text)
            assert match, 'Flag-only fixture must contain the exact unsupported verification pair'
            spans.append([match.start(), match.end()])
        for name in names:
            match = re.search(r'export const ' + name + r' = \d+;', text)
            assert match, 'Fixture must be an integer const declaration'
            spans.append([match.start(), match.end()])
        return SimpleNamespace(stdout=json.dumps(sorted(spans)))
    monkeypatch.setattr(tails.subprocess, 'run', parse_fixture)


def test_apply_plan_preserves_real_code_and_is_idempotent(tmp_path, parser_seam):
    file = tmp_path / 'page.ts'
    head = 'export const meaningful = "বাংলা 🎧";\n'
    file.write_text(head + 'export const PAGE_CONST_0 = 0;\n')
    original, edits = tails.plan([file])
    assert file.read_text() == original[file], 'Planning must not mutate source'
    assert edits[file] == head
    file.write_text(edits[file])
    assert tails.plan([file])[1] == {}


@pytest.mark.parametrize('consumer', [
    'import { PAGE_CONST_0 } from "./page";',
    'import { PAGE_CONST_0 as renamed } from "./page";',
    'export { PAGE_CONST_0 } from "./page";',
    'import * as page from "./page";',
    'export * from "./page";',
    'const value = PAGE_CONST_0;',
])
def test_plan_refuses_consumed_symbols(tmp_path, parser_seam, consumer):
    file = tmp_path / 'page.ts'
    other = tmp_path / 'consumer.ts'
    file.write_text('export const PAGE_CONST_0 = 0;\n')
    other.write_text(consumer)
    before = file.read_bytes()
    with pytest.raises(ValueError):
        tails.plan([file, other])
    assert file.read_bytes() == before


def test_declarations_outside_selected_scope_are_not_treated_as_removed(tmp_path, parser_seam):
    file = tmp_path / 'page.ts'
    other = tmp_path / 'other.ts'
    file.write_text('export const PAGE_CONST_0 = 0;\n')
    other.write_text('export const PAGE_CONST_0 = 0;\n')
    with pytest.raises(ValueError):
        tails.plan([file, other], writable=[file])


def test_sources_exclude_dependencies_but_include_tests(tmp_path):
    for name in ['page.tsx', '__tests__/check.test.ts', 'node_modules/library.ts', 'dist/compiled.js']:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('export const retained = 1;')
    assert {str(p.relative_to(tmp_path)) for p in tails.source_files(tmp_path)} == {'page.tsx', '__tests__/check.test.ts'}


def test_new_markers_are_forbidden_without_lowering_ratio_threshold():
    assert inspect_text('export const PAGE_CONST_0 = 0;')
    assert inspect_text('export function page_real_0() {}')
    assert inspect_text('const evidence = { verified: true, real: true };')
    assert inspect_text('\n'.join(f'const field = "value{i}";' for i in range(250)))


def test_apply_plan_removes_only_unsupported_verification_properties(tmp_path, parser_seam):
    file = tmp_path / 'claims.ts'
    file.write_text('export const evidence = { id: 4, verified: true, real: true, title: "Example" };\n')
    _, edits = tails.plan([file])
    assert edits[file] == 'export const evidence = { id: 4,  title: "Example" };\n'


def test_padding_cleanup_handles_single_line_jsdoc_and_repeated_claims():
    source = '/** generated component 1000+ lines */\n// Extended line 234 — production implementation detail: generated claims\nexport const realValue = 42;\n'
    result, count = cleaned(source, '.tsx')
    assert count == 2
    assert result == 'export const realValue = 42;\n'
    assert cleaned(result, '.tsx') == (result, 0)
