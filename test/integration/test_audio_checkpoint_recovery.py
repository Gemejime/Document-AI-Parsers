"""Integration tests for mid-window checkpoint recovery and corrupt input quarantine."""

import os
from unittest.mock import MagicMock, patch

import pytest
from temporalio.exceptions import ApplicationError

from internal.audio.asr.whisper_engine import WhisperEngine
from internal.audio.diarization.pyannote_engine import PyAnnoteEngine
from internal.storage.s3_client import S3ClientProtocol
from internal.temporal.activities.audio_activity import (
    TranscribeAudioWindowActivity,
    TranscribeAudioWindowInput,
)
from internal.transport.grpc.chunk_client import ChunkServiceClient


class MockQuarantineS3Client(S3ClientProtocol):
    """S3 client simulating corrupted or zero-byte files for quarantine validation."""

    def __init__(self, mode: str = "zero_byte") -> None:
        self.mode = mode

    async def fetch_slice(self, bucket: str, key: str, destination_path: str) -> str:
        _ = (bucket, key)
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        if self.mode == "zero_byte":
            with open(destination_path, "wb") as f:
                f.write(b"")
        elif self.mode == "corrupt_header":
            with open(destination_path, "wb") as f:
                f.write(b"NOT_A_VALID_RIFF_WAV_HEADER_DATA_12345")
        return destination_path

    async def write_parquet_table(
        self,
        bucket: str,
        key: str,
        data: bytes,
        metadata: dict[str, str] | None = None,
    ) -> str:
        _ = (data, metadata)
        return f"s3://{bucket}/{key}"


@pytest.mark.asyncio
async def test_audio_checkpoint_resumption_delta_accounting() -> None:
    """Verify activity resumes from mid-window offset and accounts metrics with delta."""
    activity = TranscribeAudioWindowActivity(
        whisper_engine=WhisperEngine(use_native=False),
        pyannote_engine=PyAnnoteEngine(use_native=False),
        chunk_client=ChunkServiceClient(target_endpoint="mock"),
    )

    input_data = TranscribeAudioWindowInput(
        tenant_id="tenant-delta",
        audio_id="audio-delta",
        audio_hash="hash-delta",
        s3_bucket="my-bucket",
        s3_key="audio/valid_window.wav",
        start_offset_seconds=0.0,
        window_duration_seconds=600.0,
    )

    # Simulate Temporal heartbeat resumption at 180s
    mock_checkpoint_details = [
        {
            "current_offset_seconds": 180.0,
            "processed_seconds": 180.0,
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


@pytest.mark.asyncio
async def test_audio_corrupt_zero_byte_quarantine() -> None:
    """Verify physical zero-byte audio payload triggers CORRUPT_INPUT_QUARANTINE."""
    activity = TranscribeAudioWindowActivity(
        s3_client=MockQuarantineS3Client(mode="zero_byte"),
    )

    input_data = TranscribeAudioWindowInput(
        tenant_id="tenant-quarantine",
        audio_id="audio-zero",
        audio_hash="hash-zero",
        s3_bucket="my-bucket",
        s3_key="audio/zero_byte_file.wav",
        start_offset_seconds=0.0,
        window_duration_seconds=60.0,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await activity.run(input_data)

    assert exc_info.value.type == "CORRUPT_INPUT_QUARANTINE"
    assert exc_info.value.non_retryable is True


@pytest.mark.asyncio
async def test_audio_corrupt_header_quarantine() -> None:
    """Verify physical corrupt header payload triggers CORRUPT_INPUT_QUARANTINE."""
    activity = TranscribeAudioWindowActivity(
        s3_client=MockQuarantineS3Client(mode="corrupt_header"),
    )

    input_data = TranscribeAudioWindowInput(
        tenant_id="tenant-quarantine",
        audio_id="audio-corrupt-header",
        audio_hash="hash-corrupt-header",
        s3_bucket="my-bucket",
        s3_key="audio/corrupt_header_file.wav",
        start_offset_seconds=0.0,
        window_duration_seconds=60.0,
    )

    with pytest.raises(ApplicationError) as exc_info:
        await activity.run(input_data)

    assert exc_info.value.type == "CORRUPT_INPUT_QUARANTINE"
    assert exc_info.value.non_retryable is True
