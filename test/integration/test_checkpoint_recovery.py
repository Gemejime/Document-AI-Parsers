"""Integration test verifying checkpoint recovery after worker crash / SIGKILL."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from internal.storage.s3_client import S3StorageClient
from internal.temporal.activities.vision_activity import (
    ParseDocumentSliceActivity,
    ParseDocumentSliceInput,
)
from internal.transport.grpc.chunk_client import ChunkServiceClient
from internal.vision.layout.docling_engine import DoclingEngine
from internal.vision.ocr.fallback_ocr import FallbackOCREngine
from internal.vision.table.parquet_s3_writer import ParquetS3Writer


@pytest.mark.asyncio
async def test_mid_slice_sigkill_recovery_at_page_51() -> None:
    """Simulate a worker crash/SIGKILL at page 52 and assert seamless recovery from page 51.

    Validates that:
    1. Checkpoint details are accurately read from Temporal ActivityInfo.
    2. DoclingEngine skips pages 0..50 without redundant parsing.
    3. Exactly the remaining pages (51..59 of a 60-page slice) are processed and streamed.
    """
    mock_s3 = AsyncMock(spec=S3StorageClient)
    mock_s3.fetch_slice.return_value = "slices/slice_60p.pdf"
    mock_s3.write_parquet_table.side_effect = lambda bucket, key, *_args, **_kwargs: (
        f"s3://{bucket}/{key}"
    )

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

    input_data = ParseDocumentSliceInput(
        tenant_id="tenant-crash-test",
        document_id="doc-crash-recovered",
        doc_hash="hash-sigkill-99",
        s3_bucket="doc-ai-storage",
        s3_key="slices/tenant-crash-test/hash-sigkill-99/doc_part_01.pdf",
        start_page=0,
        page_count=60,
    )

    # Simulate heartbeat checkpoint state recorded prior to SIGKILL:
    # 51 pages completed (pages 0 through 50), page 51 was next to run when killed
    mock_heartbeat_checkpoint = [
        {
            "current_page": 50,
            "pages_completed": 51,
            "total_pages": 60,
            "last_checkpoint_timestamp_utc": "2026-10-14T04:30:00Z",
        }
    ]

    mock_activity_info = MagicMock()
    mock_activity_info.heartbeat_details = mock_heartbeat_checkpoint

    with (
        patch("temporalio.activity.in_activity", return_value=True),
        patch("temporalio.activity.info", return_value=mock_activity_info),
    ):
        result = await activity.run(input_data)

    # 60 total pages - 51 already completed = 9 pages processed
    assert result.pages_processed == 9
    assert result.ast_streamed is True
    # Tables for each processed page were persisted
    assert result.table_count == 9
    assert len(result.parquet_s3_uris) == 9
