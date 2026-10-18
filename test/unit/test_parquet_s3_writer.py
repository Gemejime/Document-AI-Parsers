"""Unit tests for Parquet serialization and direct S3 uploads."""

import io
from unittest.mock import AsyncMock

import pyarrow.parquet as pq
import pytest

from internal.vision.coordinates import normalize_bbox
from internal.vision.table.parquet_s3_writer import ParquetS3Writer
from internal.vision.table.table_extractor import TableData


def test_parquet_serialization_snappy() -> None:
    """Verify PyArrow Parquet serialization produces valid Snappy-compressed Parquet."""
    writer = ParquetS3Writer()
    headers = ["ColA", "ColB"]
    rows = [[1, "TextA"], [2, "TextB"]]
    bbox = normalize_bbox(0, 0, 100, 100, 100, 100)
    table_data = TableData(headers=headers, rows=rows, bbox=bbox, page_number=1)

    parquet_bytes = writer.serialize_to_parquet_bytes(table_data)

    assert len(parquet_bytes) > 0
    # Read back bytes with pyarrow
    reader = pq.ParquetFile(io.BytesIO(parquet_bytes))
    assert reader.metadata.num_rows == 2
    assert reader.metadata.num_columns == 2


@pytest.mark.asyncio
async def test_parquet_s3_write_table_metadata() -> None:
    """Verify write_table_to_s3 constructs content-addressed path and metadata headers."""
    mock_s3 = AsyncMock()
    mock_s3.write_parquet_table.return_value = (
        "s3://my-bucket/artifacts/tenant-1/hash-abc/tables/test.parquet"
    )

    writer = ParquetS3Writer(s3_client=mock_s3)
    headers = ["X", "Y"]
    rows = [["a", "b"]]
    bbox = normalize_bbox(0, 0, 100, 100, 100, 100)
    table_data = TableData(headers=headers, rows=rows, bbox=bbox, page_number=3)

    s3_uri, table_hash = await writer.write_table_to_s3(
        table_data=table_data,
        bucket="my-bucket",
        tenant_id="tenant-1",
        doc_hash="hash-abc",
    )

    assert s3_uri == "s3://my-bucket/artifacts/tenant-1/hash-abc/tables/test.parquet"
    assert len(table_hash) == 64

    # Assert S3 client was called with mandatory headers
    mock_s3.write_parquet_table.assert_awaited_once()
    call_kwargs = mock_s3.write_parquet_table.call_args.kwargs
    assert call_kwargs["bucket"] == "my-bucket"
    assert call_kwargs["key"] == f"artifacts/tenant-1/hash-abc/tables/{table_hash}.parquet"
    metadata = call_kwargs["metadata"]
    assert metadata["x-amz-meta-tenant-id"] == "tenant-1"
    assert metadata["x-amz-meta-doc-hash"] == "hash-abc"
    assert metadata["x-amz-meta-table-hash"] == table_hash
    assert metadata["x-amz-meta-page-number"] == "3"
    assert metadata["Content-Type"] == "application/vnd.apache.parquet"
