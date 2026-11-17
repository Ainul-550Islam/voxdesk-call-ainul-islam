"""Drive listing and delta sync with an injected transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlencode

LIST_URL = "https://www.googleapis.com/drive/v3/files"
CHANGES_URL = "https://www.googleapis.com/drive/v3/changes"
EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{file_id}/export"

HttpGet = Callable[[str, Mapping[str, str]], Awaitable[Mapping[str, Any]]]


class DriveError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str
    md5: str | None = None
    size: int | None = None

    @classmethod
    def from_api(cls, row: Mapping[str, Any]) -> "DriveFile":
        return cls(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            mime_type=str(row.get("mimeType") or ""),
            modified_time=str(row.get("modifiedTime") or ""),
            md5=row.get("md5Checksum"),
            size=int(row["size"]) if row.get("size") else None,
        )


@dataclass
class DeltaResult:
    files: list[DriveFile] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    next_page_token: str | None = None
    #: The token to store for the next run.
    start_token: str | None = None


def list_query(*, folder_id: str | None = None, page_token: str | None = None) -> str:
    clauses = ["trashed = false"]
    if folder_id:
        clauses.append(f"'{folder_id}' in parents")
    params: dict[str, str] = {
        "q": " and ".join(clauses),
        "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum,size)",
        "pageSize": "100",
    }
    if page_token:
        params["pageToken"] = page_token
    return f"{LIST_URL}?{urlencode(params)}"


def changes_query(page_token: str) -> str:
    return f"{CHANGES_URL}?{urlencode({'pageToken': page_token, 'pageSize': '100', 'fields': 'nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,modifiedTime,md5Checksum,size))'})}"


def export_query(file_id: str, mime_type: str) -> str:
    return EXPORT_URL.format(file_id=file_id) + "?" + urlencode({"mimeType": mime_type})


async def list_folder(
    *, token: str, get: HttpGet, folder_id: str | None = None, page_token: str | None = None
) -> tuple[list[DriveFile], str | None]:
    response = await get(list_query(folder_id=folder_id, page_token=page_token), _auth(token))
    _raise_for_error(response)
    return [DriveFile.from_api(row) for row in response.get("files", [])], response.get(
        "nextPageToken"
    )


async def delta(*, token: str, get: HttpGet, page_token: str) -> DeltaResult:
    """Everything changed since `page_token`. Deletions are reported as ids."""
    if not page_token:
        raise DriveError("no_token", "a delta sync needs a stored page token")
    response = await get(changes_query(page_token), _auth(token))
    _raise_for_error(response)
    result = DeltaResult(next_page_token=response.get("newStartPageToken"))
    for change in response.get("changes", []):
        if change.get("removed"):
            result.removed.append(str(change.get("fileId") or ""))
            continue
        payload = change.get("file")
        if payload:
            result.files.append(DriveFile.from_api(payload))
    return result


def _auth(token: str) -> dict[str, str]:
    if not token:
        raise DriveError("no_token", "no Drive access token is configured")
    return {"Authorization": f"Bearer {token}"}


def _raise_for_error(response: Mapping[str, Any]) -> None:
    error = response.get("error")
    if error:
        if isinstance(error, Mapping):
            message = str(error.get("message") or error.get("status") or "drive_error")
        else:
            message = str(error)
        raise DriveError("drive_error", message[:300])
