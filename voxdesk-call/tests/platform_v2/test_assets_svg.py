"""Brand-asset sanitising.

Logos and favicons are served from the tenant's own domain, so an uploaded SVG is
attacker-controlled markup in a trusted origin. The sanitiser is an allowlist: an element
nobody has heard of is dropped, not investigated.
"""

from __future__ import annotations

import pytest

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.whitelabel import assets

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def svg(body: str) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{body}</svg>').encode("utf-8")


def test_a_clean_svg_is_kept_and_marked_sanitized():
    clean, removed = assets.sanitize_svg(svg('<rect x="1" y="1" width="8" height="8" fill="#1d4ed8"/>'))
    assert removed == []
    text = clean.decode("utf-8")
    assert "<rect" in text
    assert 'xmlns="http://www.w3.org/2000/svg"' in text


def test_script_elements_and_event_handlers_are_removed():
    dirty = svg('<script>alert(1)</script><rect onclick="alert(2)" width="1" height="1"/>')
    clean, removed = assets.sanitize_svg(dirty)
    text = clean.decode("utf-8")
    assert "<script" not in text
    assert "onclick" not in text
    assert "element:script" in removed
    assert "attribute:onclick" in removed


def test_foreign_object_takes_its_children_with_it():
    dirty = svg('<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>')
    clean, removed = assets.sanitize_svg(dirty)
    assert b"foreignObject" not in clean
    assert b"<div" not in clean
    assert "element:foreignObject" in removed


def test_external_references_are_dropped_but_fragment_uses_are_kept():
    dirty = svg('<use href="#icon"/><use href="https://evil.test/sprite.svg#icon"/>')
    clean, removed = assets.sanitize_svg(dirty)
    text = clean.decode("utf-8")
    assert 'href="#icon"' in text
    assert "evil.test" not in text
    assert "attribute:href" in removed


def test_javascript_urls_and_css_expressions_are_removed():
    dirty = svg('<image href="javascript:alert(1)"/><rect style="fill:url(http://evil.test/x)"/>')
    clean, removed = assets.sanitize_svg(dirty)
    text = clean.decode("utf-8").lower()
    assert "javascript:" not in text
    assert "evil.test" not in text
    assert "expression(" not in text
    assert any(item.startswith("attribute:") for item in removed)


def test_a_doctype_is_refused_before_the_tree_exists():
    with pytest.raises(ValidationFailed):
        assets.sanitize_svg(
            b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>'
        )


def test_deep_nesting_and_huge_trees_are_refused():
    deep = "<g>" * (assets.MAX_SVG_DEPTH + 5) + "</g>" * (assets.MAX_SVG_DEPTH + 5)
    with pytest.raises(ValidationFailed):
        assets.sanitize_svg(svg(deep))

    many = "<rect/>" * (assets.MAX_SVG_ELEMENTS + 10)
    with pytest.raises(ValidationFailed):
        assets.sanitize_svg(svg(many))


def test_the_root_must_be_svg():
    with pytest.raises(ValidationFailed):
        assets.sanitize_svg(b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>')


def test_sniffing_identifies_the_formats_we_accept():
    assert assets.sniff_type(PNG_BYTES) == assets.PNG
    assert assets.sniff_type(JPEG_BYTES) == assets.JPEG
    assert assets.sniff_type(svg("<rect/>")) == assets.SVG
    assert assets.sniff_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == assets.WEBP
    assert assets.sniff_type(b"just text") is None


def test_safe_filename_strips_paths_and_dotfiles():
    assert assets.safe_filename("../../etc/passwd") == "passwd"
    assert assets.safe_filename("..hidden") == "hidden"
    assert assets.safe_filename("logo (1).png") == "logo__1_.png"
    assert assets.safe_filename("") == "asset"
    assert len(assets.safe_filename("a" * 400)) == 120


def test_validate_upload_accepts_a_png_and_records_its_digest():
    result = assets.validate_upload(kind="logo", filename="logo.png", content_type="image/png", data=PNG_BYTES)
    assert result.content_type == assets.PNG
    assert result.sanitized is False
    assert result.size_bytes == len(PNG_BYTES)
    assert len(result.sha256) == 64


def test_validate_upload_sanitises_an_svg_and_reports_what_it_removed():
    dirty = svg('<script>x</script><rect width="1" height="1"/>')
    result = assets.validate_upload(
        kind="logo", filename="logo.svg", content_type="image/svg+xml", data=dirty
    )
    assert result.sanitized is True
    assert "element:script" in result.removed
    assert b"script" not in result.data


def test_a_declared_type_that_contradicts_the_bytes_is_refused():
    with pytest.raises(ValidationFailed) as caught:
        assets.validate_upload(kind="logo", filename="logo.png", content_type="image/png", data=svg("<rect/>"))
    assert "does not match" in caught.value.message


def test_unsupported_types_and_kinds_are_refused():
    with pytest.raises(ValidationFailed):
        assets.validate_upload(kind="logo", filename="x.txt", content_type="text/plain", data=b"hello")
    with pytest.raises(ValidationFailed):
        assets.validate_upload(kind="not-a-kind", filename="x.png", content_type="image/png", data=PNG_BYTES)


def test_an_empty_asset_is_refused():
    with pytest.raises(ValidationFailed):
        assets.validate_upload(kind="logo", filename="x.png", content_type="image/png", data=b"")


def test_an_oversized_asset_is_refused_with_the_limit_named(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    settings_mod.reset_plat2_settings_cache()
    monkeypatch.setenv("BRANDING_MAX_ASSET_BYTES", "10")
    try:
        with pytest.raises(ValidationFailed) as caught:
            assets.validate_upload(kind="logo", filename="x.png", content_type="image/png", data=PNG_BYTES)
        assert "at most 10 bytes" in caught.value.message
    finally:
        settings_mod.reset_plat2_settings_cache()


def test_storage_paths_are_built_from_a_tenant_id_and_a_random_name(tmp_path, monkeypatch):
    from app.platform_v2 import settings as settings_mod
    import uuid

    settings_mod.reset_plat2_settings_cache()
    monkeypatch.setenv("BRANDING_ASSET_DIR", str(tmp_path))
    try:
        tenant_id = uuid.uuid4()
        first = assets.storage_path_for(tenant_id, assets.PNG)
        second = assets.storage_path_for(tenant_id, assets.PNG)
        assert first != second
        assert first.parent == tmp_path / str(tenant_id)
        assert first.suffix == ".png"
    finally:
        settings_mod.reset_plat2_settings_cache()
