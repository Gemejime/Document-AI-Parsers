"""Integration test for full end-to-end vision activity pipeline."""

from unittest.mock import AsyncMock

import pytest

from internal.storage.s3_client import S3StorageClient
from internal.temporal.activities.vision_activity import (
    ParseDocumentSliceActivity,
    ParseDocumentSliceInput,
    ParseDocumentSliceOutput,
)
from internal.transport.grpc.chunk_client import ChunkServiceClient
from internal.vision.layout.docling_engine import DoclingEngine
from internal.vision.ocr.fallback_ocr import FallbackOCREngine
from internal.vision.table.parquet_s3_writer import ParquetS3Writer


@pytest.mark.asyncio
async def test_full_vision_pipeline_end_to_end() -> None:
    """Verify end-to-end execution of ParseDocumentSliceActivity with real pipeline components."""
    # Mock S3 client to avoid cloud connection while exercising real activity wiring
    mock_s3 = AsyncMock(spec=S3StorageClient)
    mock_s3.fetch_slice.return_value = "slices/test_slice.pdf"
    mock_s3.write_parquet_table.side_effect = lambda bucket, key, *_args, **_kwargs: (
        f"s3://{bucket}/{key}"
    )

    # Mock gRPC client acknowledging AST stream
    mock_grpc = AsyncMock(spec=ChunkServiceClient)
    mock_grpc.stream_document_ast.return_value = True

    docling = DoclingEngine(use_native=False)
    ocr = FallbackOCREngine(use_native=False)
    writer = ParquetS3Writer(s3_client=mock_s3)

    activity = ParseDocumentSliceActivity(
        s3_client=mock_s3,
        chunk_client=mock_grpc,
        docling_engine=docling,
        ocr_engine=ocr,
        parquet_writer=writer,
    )

    payload = ParseDocumentSliceInput(
        tenant_id="tenant-prod-1",
        document_id="doc-prod-999",
        doc_hash="abc123def456",
        s3_bucket="doc-ai-artifacts",
        s3_key="slices/tenant-prod-1/abc123def456/doc_part_01.pdf",
        start_page=0,
        page_count=3,
    )

    result = await activity.run(payload)

    assert isinstance(result, ParseDocumentSliceOutput)
    assert result.pages_processed == 3
    assert result.table_count >= 3  # Each page generated has a table
    assert len(result.parquet_s3_uris) == result.table_count
    for uri in result.parquet_s3_uris:
        assert uri.startswith("s3://doc-ai-artifacts/artifacts/tenant-prod-1/abc123def456/tables/")
        assert uri.endswith(".parquet")
    assert result.ast_streamed is True
    assert result.duration_seconds > 0.0

    # Verify gRPC client was called
    mock_grpc.stream_document_ast.assert_awaited_once()
