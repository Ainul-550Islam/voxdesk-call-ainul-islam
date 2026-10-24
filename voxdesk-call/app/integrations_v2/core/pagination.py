"""Cursor, page-token, OData ``@odata.nextLink``, and offset pagination iterators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class PageBatch(Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False
    page_number: int = 1


async def iterate_cursor_pages(
    fetch_page: Callable[[str | None], Awaitable[PageBatch[T]]],
    *,
    initial_cursor: str | None = None,
    max_pages: int = 50,
) -> AsyncIterator[PageBatch[T]]:
    """Yield pages until ``has_more`` is False, ``next_cursor`` is empty/unchanged, or ``max_pages`` reached."""
    cursor = initial_cursor
    seen_cursors: set[str] = set()
    for page_idx in range(1, max_pages + 1):
        batch = await fetch_page(cursor)
        yield PageBatch(
            items=batch.items,
            next_cursor=batch.next_cursor,
            has_more=batch.has_more,
            page_number=page_idx,
        )
        if not batch.has_more or not batch.next_cursor:
            break
        if batch.next_cursor in seen_cursors:
            break
        seen_cursors.add(batch.next_cursor)
        cursor = batch.next_cursor


async def iterate_offset_pages(
    fetch_offset_page: Callable[[int, int], Awaitable[list[T]]],
    *,
    page_size: int = 100,
    start_offset: int = 0,
    max_pages: int = 50,
) -> AsyncIterator[PageBatch[T]]:
    """Yield offset-based pages (ServiceNow ``sysparm_offset``, Zoho Desk ``from``)."""
    offset = max(0, start_offset)
    size = max(1, page_size)
    for page_idx in range(1, max_pages + 1):
        items = await fetch_offset_page(offset, size)
        has_more = len(items) >= size
        next_offset = offset + len(items)
        yield PageBatch(
            items=items,
            next_cursor=str(next_offset) if has_more else None,
            has_more=has_more,
            page_number=page_idx,
        )
        if not has_more or not items:
            break
        offset = next_offset
