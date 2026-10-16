"""Unit tests for TableExtractor, Markdown generation, deterministic hash, and PyArrow schemas."""

import pyarrow as pa

from internal.vision.coordinates import normalize_bbox
from internal.vision.table.table_extractor import TableData


def test_table_data_markdown_formatting() -> None:
    """Verify clean GitHub-Flavored Markdown generation."""
    headers = ["ID", "Name", "Score"]
    rows = [
        [1, "Alice", 95.5],
        [2, "Bob | Jr", 88.0],
    ]
    bbox = normalize_bbox(50, 50, 500, 200, 612, 792)
    table_data = TableData(
        headers=headers, rows=rows, bbox=bbox, page_number=1, caption="Test Table"
    )

    md = table_data.to_markdown()

    assert "**Table: Test Table**" in md
    assert "| ID | Name | Score |" in md
    assert "| --- | --- | --- |" in md
    assert "| 1 | Alice | 95.5 |" in md
    assert "Bob \\| Jr" in md  # Pipe escaped


def test_table_data_deterministic_hash() -> None:
    """Verify table hash is deterministic across identical contents."""
    headers = ["A", "B"]
    rows = [["1", "2"], ["3", "4"]]
    bbox = normalize_bbox(10, 10, 100, 100, 612, 792)

    table1 = TableData(headers=headers, rows=rows, bbox=bbox, page_number=1)
    table2 = TableData(headers=headers, rows=rows, bbox=bbox, page_number=2)

    # Identical headers and rows produce identical canonical SHA-256 hash
    assert table1.compute_table_hash() == table2.compute_table_hash()

    # Changing a cell alters the hash
    table3 = TableData(headers=headers, rows=[["1", "99"], ["3", "4"]], bbox=bbox, page_number=1)
    assert table1.compute_table_hash() != table3.compute_table_hash()


def test_table_data_pyarrow_conversion() -> None:
    """Verify conversion to PyArrow Table with strongly typed columns."""
    headers = ["IntCol", "FloatCol", "BoolCol", "StrCol"]
    rows = [
        [10, 1.5, True, "Alpha"],
        [20, 2.5, False, "Beta"],
        [30, 3.5, True, "Gamma"],
    ]
    bbox = normalize_bbox(0, 0, 100, 100, 100, 100)
    table_data = TableData(headers=headers, rows=rows, bbox=bbox, page_number=1)

    pa_table = table_data.to_pyarrow_table()

    assert isinstance(pa_table, pa.Table)
    assert pa_table.num_rows == 3
    assert pa_table.num_columns == 4

    schema = pa_table.schema
    assert pa.types.is_int64(schema.field("IntCol").type)
    assert pa.types.is_float64(schema.field("FloatCol").type)
    assert pa.types.is_boolean(schema.field("BoolCol").type)
    assert pa.types.is_string(schema.field("StrCol").type)


def test_table_data_pyarrow_duplicate_headers() -> None:
    """Verify that duplicate header names receive unique suffix and preserve all column values."""
    headers = ["Score", "Score", "Score"]
    rows = [
        [10, 20, 30],
        [40, 50, 60],
    ]
    bbox = normalize_bbox(0, 0, 100, 100, 100, 100)
    table_data = TableData(headers=headers, rows=rows, bbox=bbox, page_number=1)

    pa_table = table_data.to_pyarrow_table()

    assert pa_table.num_columns == 3
    assert pa_table.column_names == ["Score", "Score_1", "Score_2"]
    assert pa_table.column("Score").to_pylist() == [10, 40]
    assert pa_table.column("Score_1").to_pylist() == [20, 50]
    assert pa_table.column("Score_2").to_pylist() == [30, 60]
