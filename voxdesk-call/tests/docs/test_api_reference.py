"""Documentation guard for the buyer-facing OpenAPI contract."""

from __future__ import annotations

from scripts.build_api_reference import audit, is_published
from scripts.build_postman_collection import build_collection


def test_published_api_operations_have_summaries_and_descriptions() -> None:
    """A missing operation description must fail CI before publishing docs."""
    from app.main import app

    report = audit(app.openapi())
    assert report["published_operations"] == 93
    assert report["undocumented"] == []
    assert report["ok"] is True


def test_published_prefixes_are_boundary_matched() -> None:
    """Do not accidentally publish similarly named internal route families."""
    assert is_published("/api/v1/public/widget/config")
    assert is_published("/api/v1/public-keys")
    assert is_published("/api/v1/public-keys/abc")
    assert not is_published("/api/v1/publicity/preview")
    assert not is_published("/api/v1/public-site/manifest")


def test_postman_collection_covers_all_published_operations_and_file_upload() -> None:
    from app.main import app

    collection = build_collection(app.openapi())
    folders = {folder["name"]: folder["item"] for folder in collection["item"]}
    assert sum(len(items) for items in folders.values()) == 93
    upload = next(
        request
        for request in folders["/api/whitelabel"]
        if request["request"]["method"] == "POST"
        and request["request"]["url"]["raw"] == "{{baseUrl}}/api/whitelabel/assets"
    )
    assert upload["request"]["body"]["mode"] == "formdata"
    file_field = next(field for field in upload["request"]["body"]["formdata"] if field["key"] == "file")
    assert file_field["type"] == "file"
    assert all(header["key"].lower() != "content-type" for header in upload["request"]["header"])


def test_audit_fails_if_a_published_summary_or_description_is_missing() -> None:
    spec = {
        "paths": {
            "/api/billing/example": {
                "get": {"summary": "Read example", "description": "A useful description."},
                "post": {"summary": "Write example"},
                "delete": {"description": "A summary is deliberately absent."},
            }
        }
    }
    report = audit(spec)
    assert report["ok"] is False
    assert report["undocumented"] == [
        {"method": "DELETE", "path": "/api/billing/example", "missing": "summary"},
        {"method": "POST", "path": "/api/billing/example", "missing": "description"},
    ]
