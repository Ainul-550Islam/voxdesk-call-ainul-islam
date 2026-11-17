"""Drive mime types -> knowledge ingestion."""

from __future__ import annotations

from dataclasses import dataclass

FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class Export:
    #: What the file really is.
    source_mime: str
    #: What we can ingest, or None when we cannot.
    ingest_mime: str | None
    #: The mime type to request from Drive's export endpoint, if any.
    export_as: str | None = None

    @property
    def ingestable(self) -> bool:
        return self.ingest_mime is not None


NATIVE_EXPORTS: dict[str, Export] = {
    "application/vnd.google-apps.document": Export(
        "application/vnd.google-apps.document", "text/plain", "text/plain"
    ),
    "application/vnd.google-apps.presentation": Export(
        "application/vnd.google-apps.presentation", "text/plain", "text/plain"
    ),
    "application/vnd.google-apps.spreadsheet": Export(
        "application/vnd.google-apps.spreadsheet", "text/csv", "text/csv"
    ),
}

BINARY_MIMES = {
    "text/plain": "text/plain",
    "text/markdown": "text/markdown",
    "text/html": "text/html",
    "application/pdf": "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def plan(source_mime: str) -> Export:
    """How to ingest a Drive item. Folders and unknown types are refused."""
    if source_mime == FOLDER_MIME:
        raise ValueError("a folder is walked, not ingested")
    if source_mime in NATIVE_EXPORTS:
        return NATIVE_EXPORTS[source_mime]
    if source_mime in BINARY_MIMES:
        return Export(source_mime, BINARY_MIMES[source_mime])
    return Export(source_mime, None)


def ingestable_count(mimes: list[str]) -> int:
    total = 0
    for mime in mimes:
        if mime == FOLDER_MIME:
            continue
        if plan(mime).ingestable:
            total += 1
    return total
