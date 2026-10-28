"""Brand asset uploads: validation, SVG sanitisation, tenant-scoped storage.

An uploaded logo is untrusted input served back to other people's browsers, on a
domain that carries our certificate. That makes this module a sanitiser, not a
file store with a size check.

* **Type is decided by content, not by the client.** The declared content type has
  to agree with the magic bytes, so a `.png` that is really HTML is refused. SVG is
  the interesting case: it is an XML document that can carry script, so it is
  parsed with DTDs refused (defusedxml), re-serialised from an allowlist of
  elements and attributes, and served from our own origin with a restrictive CSP.
* **Size is bounded** by ``BRANDING_MAX_ASSET_BYTES``, checked before parsing and
  again while parsing (an entity-expansion bomb is small on the wire).
* **Paths are ours.** The tenant never contributes a path segment: the file name is
  a UUID plus an extension derived from the sniffed type, under
  ``<asset dir>/<tenant id>/``. A filename like ``../../etc/passwd`` in the metadata
  is stored for display only and never used to build a path.
* **Deduplication is per tenant** (``uq_wl_asset_content``), so the same logo
  uploaded twice does not become two files.

What this module does *not* do is claim to be antivirus software. The sanitised SVG
is still an image parser's problem; that is why it is served with
``Content-Disposition: inline`` only for the types we allow and the widget's CSP
forbids inline script on the page that embeds it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
import xml.etree.ElementTree as _ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import BrandAsset
from app.platform_v2.errors import NotFound, ValidationFailed
from app.platform_v2.settings import get_plat2_settings

SVG = "image/svg+xml"
PNG = "image/png"
JPEG = "image/jpeg"
WEBP = "image/webp"
logger = logging.getLogger(__name__)

#: What a tenant may upload, and the extension we store it under. The extension
#: comes from this table, never from the uploaded filename.
ALLOWED_TYPES: dict[str, str] = {SVG: ".svg", PNG: ".png", JPEG: ".jpg", WEBP: ".webp"}

ASSET_KINDS = ("logo", "favicon", "hero", "email_logo")

MAX_SVG_ELEMENTS = 5000
MAX_SVG_DEPTH = 32

#: Elements kept in a sanitised SVG. Everything else is dropped — including
#: `<foreignObject>` (which can embed an HTML document), `<script>`, and
#: `<image>` with a data URI. Dropping is silent in the output but recorded in the
#: `removed` list so the API can tell the tenant what it took out.
SVG_ALLOWED_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "title", "desc", "symbol", "use", "clipPath", "mask",
        "linearGradient", "radialGradient", "stop", "pattern",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    }
)
SVG_FORBIDDEN_ELEMENTS = frozenset(
    {
        "script", "foreignObject", "iframe", "object", "embed", "audio", "video",
        "animate", "animateMotion", "animateTransform", "set", "handler", "listener",
        "image", "a", "cursor",
    }
)
SVG_ALLOWED_ATTRIBUTES = frozenset(
    {
        "id", "class", "style", "transform", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy",
        "r", "rx", "ry", "d", "points", "width", "height", "viewBox", "fill", "fill-opacity",
        "fill-rule", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
        "stroke-opacity", "stroke-dasharray", "stroke-dashoffset", "opacity", "offset",
        "stop-color", "stop-opacity", "font-family", "font-size", "font-weight",
        "text-anchor", "dominant-baseline", "letter-spacing", "preserveAspectRatio",
        "gradientUnits", "gradientTransform", "patternUnits", "clipPathUnits",
        "clip-path", "mask", "maskUnits", "display", "visibility", "version", "xmlns",
        "xmlns:xlink", "role", "aria-label", "data-name",
    }
)
#: `href` is allowed only on `<use>` and only for a same-document fragment.
SVG_HREF_ATTRIBUTES = frozenset({"href", "xlink:href"})
SVG_NS = "http://www.w3.org/2000/svg"

# Registered once, at import: without it a re-serialised SVG comes back as
# `<ns0:svg xmlns:ns0="...">`, which renders correctly but breaks the CSS
# selectors and the tenant's own tooling when they download their logo again.
_ET.register_namespace("", SVG_NS)

_CSS_URL_RE = re.compile(r"url\s*\(([^)]*)\)", re.IGNORECASE)
_JS_SCHEME_RE = re.compile(r"^\s*(javascript|vbscript|data)\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class SanitisedAsset:
    """The result of validating an upload.

    ``data`` is what will be stored: identical bytes for raster images, re-serialised
    XML for SVG. ``removed`` is the audit trail of what sanitisation took out.
    """

    kind: str
    filename: str
    content_type: str
    data: bytes
    sha256: str
    size_bytes: int
    sanitized: bool
    removed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "filename": self.filename,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "sanitized": self.sanitized,
            "removed": self.removed,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sniff_type(data: bytes) -> str | None:
    """Identify an image from its first bytes. Returns ``None`` when unknown."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if data[:3] == b"\xff\xd8\xff":
        return JPEG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    head = data[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head[:512]:
        return SVG
    return None


def safe_filename(raw: str) -> str:
    """Display-only filename: no separators, no dotfiles, bounded length."""
    if not raw:
        return "asset"
    name = raw.replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = name.lstrip(".") or "asset"
    return name[:120]


def _check_size(size: int) -> None:
    limit = get_plat2_settings().branding_max_asset_bytes
    if size <= 0:
        raise ValidationFailed("asset is empty")
    if size > limit:
        raise ValidationFailed(f"asset must be at most {limit} bytes (got {size})")


def sanitize_svg(data: bytes) -> tuple[bytes, list[str]]:
    """Re-serialise an SVG from the element/attribute allowlist.

    Parsing refuses DTDs and external entities (defusedxml), so billion-laughs and
    XXE are rejected before the tree exists. The walk then drops anything not on the
    allowlist, which is stricter than trying to detect "bad" markup: an element we
    have never heard of is not kept.
    """
    from defusedxml import ElementTree as DefusedET

    try:
        parser = DefusedET.DefusedXMLParser()
        import xml.etree.ElementTree as ET

        tree = ET.fromstring(data, parser=parser)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a refused asset
        raise ValidationFailed(f"SVG could not be parsed: {exc}") from exc

    removed: list[str] = []
    count = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > MAX_SVG_ELEMENTS:
            raise ValidationFailed(f"SVG is too complex (more than {MAX_SVG_ELEMENTS} elements)")
        if depth > MAX_SVG_DEPTH:
            raise ValidationFailed(f"SVG is nested too deeply (more than {MAX_SVG_DEPTH} levels)")

        tag = _local_name(node.tag)
        if tag in SVG_FORBIDDEN_ELEMENTS or tag not in SVG_ALLOWED_ELEMENTS:
            removed.append(f"element:{tag}")
            # A forbidden element takes its subtree with it, whatever the children are.
            return

        for attribute in list(node.attrib):
            name = _local_name(attribute)
            value = node.attrib[attribute]
            if name.startswith("on"):
                removed.append(f"attribute:{name}")
                del node.attrib[attribute]
                continue
            if name in SVG_HREF_ATTRIBUTES:
                if tag != "use" or not str(value).startswith("#"):
                    removed.append("attribute:href")
                    del node.attrib[attribute]
                continue
            if name == "style":
                cleaned = _clean_style(value)
                if cleaned is None:
                    removed.append("attribute:style")
                    del node.attrib[attribute]
                else:
                    node.attrib[attribute] = cleaned
                continue
            if name not in SVG_ALLOWED_ATTRIBUTES:
                removed.append(f"attribute:{name}")
                del node.attrib[attribute]
                continue
            if _JS_SCHEME_RE.match(str(value)) or "expression(" in str(value).lower():
                removed.append(f"attribute:{name}=url")
                del node.attrib[attribute]

        for child in list(node):
            walk(child, depth + 1)
            if _local_name(child.tag) in SVG_FORBIDDEN_ELEMENTS or _local_name(child.tag) not in SVG_ALLOWED_ELEMENTS:
                node.remove(child)

    walk(tree, 0)
    if _local_name(tree.tag) != "svg":
        raise ValidationFailed("an SVG asset must have <svg> as its root element")
    # The default namespace is registered at import time, so serialisation emits
    # `xmlns="..."` on the root element by itself.
    body = _ET.tostring(tree, encoding="utf-8")
    return body, sorted(set(removed))


def _local_name(tag: Any) -> str:
    text = str(tag)
    return text.split("}", 1)[1] if "}" in text else text


def _clean_style(value: str) -> str | None:
    """Keep a CSS declaration list unless it references anything external."""
    for match in _CSS_URL_RE.finditer(value):
        target = match.group(1).strip().strip("'\"")
        if not target.startswith("#"):
            return None
    if _JS_SCHEME_RE.search(value) or "expression(" in value.lower():
        return None
    return value


def validate_upload(
    *, kind: str, filename: str, content_type: str | None, data: bytes
) -> SanitisedAsset:
    """Validate one upload and return the bytes that should be stored."""
    if kind not in ASSET_KINDS:
        raise ValidationFailed(f"kind must be one of: {', '.join(ASSET_KINDS)}")
    _check_size(len(data))

    declared = (content_type or "").split(";", 1)[0].strip().lower()
    sniffed = sniff_type(data)
    if sniffed is None:
        raise ValidationFailed("asset is not a supported image (PNG, JPEG, WebP or SVG)")
    if declared and declared not in ALLOWED_TYPES:
        raise ValidationFailed(f"content type {declared} is not allowed")
    if declared and declared != sniffed:
        # The declared type may be more specific than the sniff for SVG (`text/xml`);
        # anything else is a mismatch we refuse rather than resolve.
        if not (sniffed == SVG and declared in {"text/xml", "application/xml"}):
            raise ValidationFailed(
                f"declared content type {declared} does not match the file contents ({sniffed})"
            )

    removed: list[str] = []
    stored = data
    sanitized = False
    if sniffed == SVG:
        stored, removed = sanitize_svg(data)
        sanitized = True

    digest = hashlib.sha256(stored).hexdigest()
    return SanitisedAsset(
        kind=kind,
        filename=safe_filename(filename),
        content_type=sniffed,
        data=stored,
        sha256=digest,
        size_bytes=len(stored),
        sanitized=sanitized,
        removed=removed,
    )


def asset_root() -> Path:
    return Path(get_plat2_settings().branding_asset_dir)


def _s3_location(storage_path: str | Path, *, tenant_id: uuid.UUID) -> tuple[str, str] | None:
    """Parse a stored S3 URI and enforce its configured bucket/tenant prefix."""
    raw = str(storage_path)
    if not raw.startswith("s3://"):
        return None
    parsed = urlsplit(raw)
    settings = get_plat2_settings()
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    expected_prefix = f"tenants/{tenant_id}/"
    segments = PurePosixPath(key).parts
    if (
        parsed.scheme != "s3"
        or not settings.branding_asset_s3_bucket
        or bucket != settings.branding_asset_s3_bucket
        or parsed.query
        or parsed.fragment
        or not key.startswith(expected_prefix)
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise ValidationFailed("asset storage path is outside its configured tenant bucket")
    return bucket, key


def storage_path_for(tenant_id: uuid.UUID, content_type: str) -> Path | str:
    """Build a tenant-scoped local path or S3 URI with a random known suffix."""
    extension = ALLOWED_TYPES[content_type]
    filename = f"{uuid.uuid4().hex}{extension}"
    settings = get_plat2_settings()
    if settings.branding_asset_s3_bucket:
        return f"s3://{settings.branding_asset_s3_bucket}/tenants/{tenant_id}/{filename}"
    return asset_root() / str(tenant_id) / filename


def _s3_client():
    import boto3

    settings = get_plat2_settings()
    if not settings.branding_asset_s3_bucket or not settings.branding_asset_s3_region:
        raise ValidationFailed("S3 brand-asset storage is not configured")
    return boto3.client("s3", region_name=settings.branding_asset_s3_region)


def _write_s3_object(bucket: str, key: str, data: bytes, content_type: str) -> None:
    settings = get_plat2_settings()
    request: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": data,
        "ContentType": content_type,
    }
    if settings.branding_asset_s3_kms_key_id:
        request["ServerSideEncryption"] = "aws:kms"
        request["SSEKMSKeyId"] = settings.branding_asset_s3_kms_key_id
    _s3_client().put_object(**request)


def _read_s3_object(bucket: str, key: str) -> bytes:
    try:
        response = _s3_client().get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        from botocore.exceptions import ClientError

        if isinstance(exc, ClientError):
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"NoSuchKey", "NoSuchBucket", "404"}:
                raise NotFound("asset file is missing from storage") from exc
        raise
    maximum = get_plat2_settings().branding_max_asset_bytes
    if int(response.get("ContentLength", 0)) > maximum:
        raise ValidationFailed("stored asset exceeds the configured size limit")
    body = response["Body"]
    try:
        data = body.read(maximum + 1)
    finally:
        body.close()
    if len(data) > maximum:
        raise ValidationFailed("stored asset exceeds the configured size limit")
    return data


def _delete_s3_object(bucket: str, key: str) -> None:
    _s3_client().delete_object(Bucket=bucket, Key=key)


async def _delete_storage_path(storage_path: str | Path, *, tenant_id: uuid.UUID) -> None:
    location = _s3_location(storage_path, tenant_id=tenant_id)
    if location is not None:
        await asyncio.to_thread(_delete_s3_object, *location)
        return
    path = Path(storage_path).resolve()
    root = asset_root().resolve()
    if root not in path.parents and path.parent != root:
        raise ValidationFailed("asset storage path is outside the asset directory")
    try:
        await asyncio.to_thread(path.unlink)
    except FileNotFoundError:
        pass


async def _write_file(path: Path, data: bytes) -> None:
    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o640
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)

    await asyncio.to_thread(write)


async def store(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    asset: SanitisedAsset,
    created_by_user_id: uuid.UUID | None = None,
    commit: bool = False,
) -> tuple[BrandAsset, bool]:
    """Persist a validated asset. Returns ``(row, created)``.

    A duplicate (same tenant, same bytes) returns the existing row instead of
    writing a second file — the unique constraint is the tie-breaker, not a
    read-then-write race.
    """
    existing = await session.execute(
        select(BrandAsset).where(BrandAsset.tenant_id == tenant_id, BrandAsset.sha256 == asset.sha256)
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return found, False

    storage_path = storage_path_for(tenant_id, asset.content_type)
    location = _s3_location(storage_path, tenant_id=tenant_id)
    if location is None:
        await _write_file(Path(storage_path), asset.data)
    else:
        await asyncio.to_thread(
            _write_s3_object,
            location[0],
            location[1],
            asset.data,
            asset.content_type,
        )
    row = BrandAsset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind=asset.kind,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        storage_path=str(storage_path),
        sanitized=asset.sanitized,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await _delete_storage_path(storage_path, tenant_id=tenant_id)
        await session.rollback()
        retry = await session.execute(
            select(BrandAsset).where(BrandAsset.tenant_id == tenant_id, BrandAsset.sha256 == asset.sha256)
        )
        found = retry.scalar_one_or_none()
        if found is None:
            raise
        return found, False
    except Exception:
        await _delete_storage_path(storage_path, tenant_id=tenant_id)
        raise
    if commit:
        try:
            await session.commit()
        except Exception:
            await _delete_storage_path(storage_path, tenant_id=tenant_id)
            await session.rollback()
            raise
    return row, True


async def get(session: AsyncSession, *, tenant_id: uuid.UUID, asset_id: uuid.UUID) -> BrandAsset:
    row = await session.execute(
        select(BrandAsset).where(BrandAsset.id == asset_id, BrandAsset.tenant_id == tenant_id)
    )
    found = row.scalar_one_or_none()
    if found is None:
        raise NotFound("asset not found")
    return found


async def list_for_tenant(session: AsyncSession, *, tenant_id: uuid.UUID, limit: int = 100) -> list[BrandAsset]:
    rows = await session.execute(
        select(BrandAsset).where(BrandAsset.tenant_id == tenant_id).order_by(BrandAsset.created_at.desc()).limit(limit)
    )
    return list(rows.scalars().all())


async def read_bytes(asset: BrandAsset) -> bytes:
    """Read the stored file, enforcing its root/bucket and recorded digest."""
    location = _s3_location(asset.storage_path, tenant_id=asset.tenant_id)
    if location is not None:
        data = await asyncio.to_thread(_read_s3_object, *location)
    else:
        path = Path(asset.storage_path).resolve()
        root = asset_root().resolve()
        if root not in path.parents and path.parent != root:
            raise ValidationFailed("asset storage path is outside the asset directory")
        if not path.exists():
            raise NotFound("asset file is missing from storage")
        data = await asyncio.to_thread(path.read_bytes)
    if len(data) != asset.size_bytes or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValidationFailed("asset bytes do not match their recorded size and digest")
    return data


async def delete_storage_path(storage_path: str | Path, *, tenant_id: uuid.UUID) -> None:
    """Remove a previously stored asset after its database transaction commits."""
    await _delete_storage_path(storage_path, tenant_id=tenant_id)


async def delete(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    commit: bool = False,
) -> str:
    """Stage asset deletion and return its storage path for post-commit cleanup.

    With ``commit=True``, this function commits before deleting the object. Callers
    that own a larger audit/idempotency transaction must clean up the returned path
    only after their own commit succeeds.
    """
    row = await get(session, tenant_id=tenant_id, asset_id=asset_id)
    storage_path = row.storage_path
    await session.delete(row)
    if commit:
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await _delete_storage_path(storage_path, tenant_id=tenant_id)
    return storage_path


def describe(row: BrandAsset) -> dict[str, Any]:
    """Serialise an asset. ``storage_path`` never leaves the server."""
    return {
        "id": str(row.id),
        "kind": row.kind,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "sanitized": row.sanitized,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "url": f"/api/whitelabel/assets/{row.id}/content",
    }


__all__ = [
    "ALLOWED_TYPES",
    "ASSET_KINDS",
    "JPEG",
    "PNG",
    "SVG",
    "WEBP",
    "SanitisedAsset",
    "asset_root",
    "delete",
    "delete_storage_path",
    "describe",
    "get",
    "list_for_tenant",
    "read_bytes",
    "safe_filename",
    "sanitize_svg",
    "sniff_type",
    "storage_path_for",
    "store",
    "validate_upload",
]
