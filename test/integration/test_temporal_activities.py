"""Integration tests for Temporal activity execution, resumption, and quarantine."""

from unittest.mock import MagicMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from internal.temporal.activities.audio_activity import (
    TranscribeAudioWindowActivity,
    TranscribeAudioWindowInput,
)
from internal.temporal.activities.vision_activity import (
    ParseDocumentSliceActivity,
    ParseDocumentSliceInput,
)
from internal.transport.grpc.chunk_client import ChunkServiceClient


@pytest.mark.asyncio
async def test_vision_activity_corrupt_input_quarantine() -> None:
    """Verify that corrupt PDF inputs fail-fast with non-retryable quarantine ApplicationError."""
    activity = ParseDocumentSliceActivity()
    corrupt_input = ParseDocumentSliceInput(
        tenant_id="tenant-123",
        document_id="doc-corrupt",
        doc_hash="hash-123",
        s3_bucket="my-bucket",
        s3_key="slices/tenant-123/corrupted_slice.pdf",
        start_page=0,
        page_count=10,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await activity.run(corrupt_input)

    assert exc_info.value.type == "CORRUPT_INPUT_QUARANTINE"
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_audio_activity_corrupt_input_quarantine() -> None:
    """Verify that corrupt audio inputs fail-fast with non-retryable quarantine ApplicationError."""
    activity = TranscribeAudioWindowActivity()
    corrupt_input = TranscribeAudioWindowInput(
        tenant_id="tenant-123",
        audio_id="audio-corrupt",
        audio_hash="hash-123",
        s3_bucket="my-bucket",
        s3_key="audio/corrupted_audio_file.wav",
        start_offset_seconds=0.0,
        window_duration_seconds=600.0,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await activity.run(corrupt_input)

    assert exc_info.value.type == "CORRUPT_INPUT_QUARANTINE"
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_vision_activity_checkpoint_resumption() -> None:
    """Verify activity resumes from committed checkpoint (e.g., killed at page 52, resumes at 51)."""
    activity = ParseDocumentSliceActivity(chunk_client=ChunkServiceClient(target_endpoint="mock"))
    input_data = ParseDocumentSliceInput(
        tenant_id="tenant-456",
        document_id="doc-resume",
        doc_hash="hash-456",
        s3_bucket="my-bucket",
        s3_key="slices/valid_slice.pdf",
        start_page=0,
        page_count=60,
    )

    # Mock previous heartbeat details indicating 51 pages completed
    mock_checkpoint_details = [
        {
            "current_page": 50,
            "pages_completed": 51,
            "total_pages": 60,
            "last_checkpoint_timestamp_utc": "2026-10-13T10:00:00Z",
        }
    ]

    mock_info = MagicMock()
    mock_info.heartbeat_details = mock_checkpoint_details

    with (
        patch("temporalio.activity.in_activity", return_value=True),
        patch("temporalio.activity.info", return_value=mock_info),
    ):
        output = await activity.run(input_data)

    # Only remaining 9 pages (60 - 51) should have been processed
    assert output.pages_processed == 9
    assert output.ast_streamed is True


@pytest.mark.asyncio
async def test_audio_activity_checkpoint_resumption() -> None:
    """Verify audio activity resumes from committed offset checkpoint."""
    activity = TranscribeAudioWindowActivity(
        chunk_client=ChunkServiceClient(target_endpoint="mock")
    )
    input_data = TranscribeAudioWindowInput(
        tenant_id="tenant-456",
        audio_id="audio-resume",
        audio_hash="hash-456",
        s3_bucket="my-bucket",
        s3_key="audio/valid_window.wav",
        start_offset_seconds=0.0,
        window_duration_seconds=600.0,
    )

    mock_checkpoint_details = [
        {
            "current_offset_seconds": 450.0,
            "processed_seconds": 450.0,
            "total_seconds": 600.0,
        }
    ]

    mock_info = MagicMock()
    mock_info.heartbeat_details = mock_checkpoint_details

    with (
        patch("temporalio.activity.in_activity", return_value=True),
        patch("temporalio.activity.info", return_value=mock_info),
    ):
        output = await activity.run(input_data)

    assert output.audio_seconds_processed == 600.0
    assert output.ast_streamed is True
