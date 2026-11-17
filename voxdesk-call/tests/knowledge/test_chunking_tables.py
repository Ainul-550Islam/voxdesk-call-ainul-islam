"""PART 14 / P14-08 — table-aware chunking keeps a row with its header."""

from __future__ import annotations

from app.knowledge.chunking import chunk_csv, chunk_table, rows_from_csv, table_row_text

PRICE_CSV = """Item,Monthly price,Notes
Basic,500,Two agents
Pro,1500,Most popular
Business,4000,SLA
"""


def test_csv_parsing_keeps_headers_and_skips_blank_rows() -> None:
    headers, rows = rows_from_csv(PRICE_CSV + "\n,,,\n")
    assert headers == ["Item", "Monthly price", "Notes"]
    assert len(rows) == 3
    assert rows[1]["Item"] == "Pro"


def test_quoted_fields_with_commas_survive() -> None:
    headers, rows = rows_from_csv('Name,Note\n"Acme, Ltd","Says ""hi"""\n')
    assert headers == ["Name", "Note"]
    assert rows[0]["Name"] == "Acme, Ltd"
    assert rows[0]["Note"] == 'Says "hi"'


def test_a_missing_cell_is_omitted_rather_than_faked() -> None:
    headers = ["Item", "Price", "Note"]
    assert table_row_text(headers, {"Item": "Basic", "Price": "500"}) == "Item: Basic; Price: 500"


def test_every_chunk_carries_the_caption_and_the_columns() -> None:
    chunks = chunk_csv(PRICE_CSV, caption="Price list")
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.text.startswith("Price list")
    assert chunk.metadata["caption"] == "Price list"
    assert chunk.metadata["columns"] == ["Item", "Monthly price", "Notes"]
    assert chunk.metadata["table"] is True


def test_a_long_table_splits_on_row_boundaries_and_each_part_is_self_describing() -> None:
    rows = [{"Item": f"Plan {index}", "Price": str(index * 100)} for index in range(60)]
    chunks = chunk_table(rows, headers=["Item", "Price"], caption="All plans", max_chars=400)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.startswith("All plans")
        # Every chunk starts with a complete `Item:` pair, never mid-value.
        assert chunk.text.splitlines()[1].startswith("Item: Plan")
    assert {chunk.metadata["part_count"] for chunk in chunks} == {len(chunks)}


def test_no_row_is_lost_across_the_split() -> None:
    rows = [{"Item": f"Plan {index}", "Price": str(index)} for index in range(40)]
    chunks = chunk_table(rows, headers=["Item", "Price"], max_chars=300)
    seen = sum(chunk.text.count("Item:") for chunk in chunks)
    assert seen == 40


def test_an_empty_table_produces_no_chunks() -> None:
    assert chunk_table([], headers=["a"]) == []
    assert chunk_csv("") == []


def test_indexes_are_contiguous_and_offsettable() -> None:
    rows = [{"Item": f"P{index}"} for index in range(30)]
    chunks = chunk_table(rows, headers=["Item"], max_chars=120, index_start=7)
    assert [chunk.index for chunk in chunks] == list(range(7, 7 + len(chunks)))
