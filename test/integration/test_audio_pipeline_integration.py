"""End-to-end integration tests for audio transcription activity and pipeline."""

import os
import wave
from unittest.mock import AsyncMock

import pytest

from internal.audio.asr.whisper_engine import WhisperEngine
from internal.audio.diarization.pyannote_engine import PyAnnoteEngine
from internal.storage.s3_client import S3ClientProtocol
from internal.temporal.activities.audio_activity import (
    TranscribeAudioWindowActivity,
    TranscribeAudioWindowInput,
)
from internal.transport.grpc.chunk_client import ChunkClientProtocol


class DummyS3Client(S3ClientProtocol):
    """Mock S3 client writing a valid dummy WAV file to destination path."""

    async def fetch_slice(self, bucket: str, key: str, destination_path: str) -> str:
        _ = (bucket, key)
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        with wave.open(destination_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000)
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


class DummyChunkClient(ChunkClientProtocol):
    """Mock chunk client verifying gRPC audio transcript calls."""

    def __init__(self) -> None:
        self.mock_audio = AsyncMock(return_value=True)
        self.mock_ast = AsyncMock(return_value=True)

    async def stream_audio_transcript(
        self,
        tenant_id: str,
        audio_id: str,
        segments_generator: object,
    ) -> bool:
        return bool(
            await self.mock_audio(
                tenant_id=tenant_id,
                audio_id=audio_id,
                segments_generator=segments_generator,
            )
        )

    async def stream_document_ast(
        self,
        tenant_id: str,
        document_id: str,
        chunks_generator: object,
    ) -> bool:
        return bool(
            await self.mock_ast(
                tenant_id=tenant_id,
                document_id=document_id,
                chunks_generator=chunks_generator,
            )
        )


@pytest.mark.asyncio
async def test_transcribe_audio_window_activity_end_to_end() -> None:
    """Verify full execution of TranscribeAudioWindowActivity with thread isolation."""
    s3_client = DummyS3Client()
    chunk_client = DummyChunkClient()
    whisper_engine = WhisperEngine(use_native=False)
    pyannote_engine = PyAnnoteEngine(use_native=False)

    activity = TranscribeAudioWindowActivity(
        s3_client=s3_client,
        chunk_client=chunk_client,
        whisper_engine=whisper_engine,
        pyannote_engine=pyannote_engine,
    )

    input_data = TranscribeAudioWindowInput(
        tenant_id="tenant-e2e",
        audio_id="audio-e2e",
        audio_hash="hash-e2e",
        s3_bucket="my-audio-bucket",
        s3_key="audio/tenant-e2e/hash-e2e/audio_part_01.wav",
        start_offset_seconds=0.0,
        window_duration_seconds=60.0,
    )

    output = await activity.run(input_data)

    assert output.audio_seconds_processed == 60.0
    assert output.segment_count > 0
    assert output.speaker_count >= 1
    assert output.ast_streamed is True
    assert output.duration_seconds > 0.0

    # Verify gRPC transport was invoked
    assert chunk_client.mock_audio.called
    call_args = chunk_client.mock_audio.call_args[1]
    assert call_args["tenant_id"] == "tenant-e2e"
    assert call_args["audio_id"] == "audio-e2e"
    assert len(call_args["segments_generator"]) == output.segment_count
