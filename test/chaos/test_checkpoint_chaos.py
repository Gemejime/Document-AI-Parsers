"""Chaos testing suite: forced process interruption, SIGKILL recovery & idempotent resumption."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from internal.audio.asr.whisper_engine import WhisperEngine
from internal.audio.diarization.pyannote_engine import PyAnnoteEngine
from internal.storage.s3_client import S3StorageClient
from internal.temporal.activities.audio_activity import (
    TranscribeAudioWindowActivity,
    TranscribeAudioWindowInput,
)
from internal.temporal.activities.vision_activity import (
    ParseDocumentSliceActivity,
    ParseDocumentSliceInput,
)
from internal.transport.grpc.chunk_client import ChunkServiceClient
from internal.vision.layout.docling_engine import DoclingEngine
from internal.vision.ocr.fallback_ocr import FallbackOCREngine
from internal.vision.table.parquet_s3_writer import ParquetS3Writer
from internal.vision.table.table_extractor import TableData


@pytest.mark.asyncio
async def test_vision_sigkill_chaos_recovery_at_page_51() -> None:
    """Simulate abrupt SIGKILL during vision processing at page 52 and verify recovery from page 51."""
    # Track S3 Parquet writes to verify idempotency and non-duplication
    persisted_keys: list[str] = []

    mock_s3 = AsyncMock(spec=S3StorageClient)
    mock_s3.fetch_slice.return_value = "slices/chaos_slice_100p.pdf"

    async def fake_write_parquet(bucket: str, key: str, *_args: object, **_kwargs: object) -> str:
        persisted_keys.append(f"s3://{bucket}/{key}")
        return f"s3://{bucket}/{key}"

    mock_s3.write_parquet_table.side_effect = fake_write_parquet

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

    input_payload = ParseDocumentSliceInput(
        tenant_id="tenant-chaos",
        document_id="doc-sigkill-vision",
        doc_hash="hash-vision-chaos-100",
        s3_bucket="doc-ai-storage",
        s3_key="slices/tenant-chaos/hash-vision-chaos-100/part_01.pdf",
        start_page=0,
        page_count=100,
    )

    # 1. State recorded prior to SIGKILL: 51 pages completed (indices 0..50)
    # Next page to be processed was index 51 (page 52 in 1-indexed terms)
    mock_heartbeat_checkpoint = [
        {
            "current_page": 50,
            "pages_completed": 51,
            "total_pages": 100,
            "last_checkpoint_timestamp_utc": "2026-10-15T00:00:00Z",
        }
    ]

    mock_activity_info = MagicMock()
    mock_activity_info.heartbeat_details = mock_heartbeat_checkpoint

    with (
        patch("temporalio.activity.in_activity", return_value=True),
        patch("temporalio.activity.info", return_value=mock_activity_info),
    ):
        result = await activity.run(input_payload)

    # Invariant: exactly remaining 49 pages (51..99) are processed
    assert result.pages_processed == 49
    assert result.ast_streamed is True
    assert result.table_count == 49
    assert len(persisted_keys) == 49


@pytest.mark.asyncio
async def test_audio_sigkill_chaos_recovery_at_offset_45() -> None:
    """Simulate abrupt SIGKILL during audio processing at 45s and verify delta resumption."""
    mock_grpc = AsyncMock(spec=ChunkServiceClient)
    mock_grpc.stream_audio_transcript.return_value = True

    activity = TranscribeAudioWindowActivity(
        whisper_engine=WhisperEngine(use_native=False),
        pyannote_engine=PyAnnoteEngine(use_native=False),
        chunk_client=mock_grpc,
    )

    input_payload = TranscribeAudioWindowInput(
        tenant_id="tenant-chaos-audio",
        audio_id="audio-sigkill-window",
        audio_hash="hash-audio-chaos-120",
        s3_bucket="doc-ai-storage",
        s3_key="audio/valid_window.wav",
        start_offset_seconds=0.0,
        window_duration_seconds=120.0,
    )

    # 1. Checkpoint recorded prior to SIGKILL at 45 seconds
    mock_checkpoint = [
        {
            "current_offset_seconds": 45.0,
            "processed_seconds": 45.0,
            "total_seconds": 120.0,
        }
    ]

    mock_activity_info = MagicMock()
    mock_activity_info.heartbeat_details = mock_checkpoint

    with (
        patch("temporalio.activity.in_activity", return_value=True),
        patch("temporalio.activity.info", return_value=mock_activity_info),
    ):
        output = await activity.run(input_payload)

    assert output.audio_seconds_processed == 120.0
    assert output.ast_streamed is True


@pytest.mark.asyncio
async def test_transactional_s3_parquet_writing_idempotency() -> None:
    """Validate that identical tables written concurrently or repeatedly generate idempotent S3 paths."""
    mock_s3 = AsyncMock(spec=S3StorageClient)
    written_uris: set[str] = set()

    async def idempotent_write(bucket: str, key: str, *_args: object, **_kwargs: object) -> str:
        uri = f"s3://{bucket}/{key}"
        written_uris.add(uri)
        return uri

    mock_s3.write_parquet_table.side_effect = idempotent_write
    writer = ParquetS3Writer(s3_client=mock_s3)

    from internal.vision.coordinates import normalize_bbox

    bbox = normalize_bbox(0, 0, 100, 100, 100, 100)
    table = TableData(
        headers=["Invoice ID", "Amount", "Currency"],
        rows=[
            ["INV-1001", "450.00", "USD"],
            ["INV-1002", "1250.50", "EUR"],
        ],
        bbox=bbox,
        page_number=1,
    )

    # Simulate write before crash
    uri_1, _ = await writer.write_table_to_s3(
        table_data=table,
        bucket="my-data-lake",
        tenant_id="tenant-chaos-idempotent",
        doc_hash="hash-idempotent-doc",
    )

    # Simulate retry/resumption write of same table after crash
    uri_2, _ = await writer.write_table_to_s3(
        table_data=table,
        bucket="my-data-lake",
        tenant_id="tenant-chaos-idempotent",
        doc_hash="hash-idempotent-doc",
    )

    assert uri_1 == uri_2
    assert len(written_uris) == 1
    assert "artifacts/tenant-chaos-idempotent/hash-idempotent-doc/tables/" in uri_1
    assert uri_1.endswith(f"{table.compute_table_hash()}.parquet")
