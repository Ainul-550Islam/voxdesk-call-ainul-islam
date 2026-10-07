"""Archives may describe removed code; active services must never refer to it."""
from scripts.verify_retired_references import RETIRED, scan


def test_active_surface_has_no_retired_dependencies():
    assert scan() == []


def test_archives_do_not_mask_a_live_compose_reference(tmp_path):
    archive = tmp_path / 'docs/archive'
    archive.mkdir(parents=True)
    (archive / 'history.md').write_text(RETIRED[0])
    assert scan(tmp_path) == []
    (tmp_path / 'docker-compose.full.yml').write_text('services:\n  wrong:\n    build: ./' + RETIRED[0])
    assert scan(tmp_path)[0]['file'] == 'docker-compose.full.yml'


def test_denylist_declaration_exemption_is_not_a_whole_file_exemption(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    path = scripts / 'verify_retired_references.py'
    fragments = [value.split('-') for value in RETIRED]
    expression = ', '.join(' + '.join(repr(part) for part in pieces) for pieces in fragments)
    path.write_text('RETIRED = (' + expression + ')\n')
    assert scan(tmp_path) == []
    path.write_text(path.read_text() + 'build_path = ' + repr(RETIRED[0]) + '\n')
    assert scan(tmp_path)[0]['line'] == 2
