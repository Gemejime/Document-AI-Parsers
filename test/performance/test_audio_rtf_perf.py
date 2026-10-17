"""Performance test asserting audio processing Real-Time Factor (RTF <= 0.15)."""

import time

import pytest

from internal.audio.alignment import align_audio_timeline
from internal.audio.asr.whisper_engine import WhisperEngine
from internal.audio.diarization.pyannote_engine import PyAnnoteEngine
from internal.transport.grpc.chunk_client import ChunkServiceClient


@pytest.mark.asyncio
async def test_audio_pipeline_rtf_performance() -> None:
    """Assert audio pipeline achieves Real-Time Factor RTF <= 0.15 for a 600s window."""
    audio_duration_seconds = 600.0  # 10 minute window

    whisper = WhisperEngine(use_native=False)
    pyannote = PyAnnoteEngine(use_native=False)
    client = ChunkServiceClient(target_endpoint="mock")

    start_time = time.perf_counter()

    # 1. Speech-to-text
    whisper_segments = whisper.transcribe_window(
        audio_path="perf_audio.wav",
        start_from_offset=0.0,
        total_duration_seconds=audio_duration_seconds,
        step_seconds=30.0,
    )

    # 2. Diarization
    speaker_turns = pyannote.diarize_window(
        audio_path="perf_audio.wav",
        start_from_offset=0.0,
        duration_seconds=audio_duration_seconds,
    )

    # 3. Alignment
    aligned_segments = align_audio_timeline(
        whisper_segments=whisper_segments,
        speaker_turns=speaker_turns,
        start_offset_seconds=0.0,
        start_from_offset=0.0,
    )

    # 4. Streaming gRPC egress
    stream_success = await client.stream_audio_transcript(
        tenant_id="perf-tenant",
        audio_id="perf-audio-600s",
        segments_generator=aligned_segments,
    )

    elapsed_time = time.perf_counter() - start_time
    rtf = elapsed_time / audio_duration_seconds

    assert stream_success is True
    assert len(aligned_segments) > 0
    # SLO: Real-Time Factor (RTF) <= 0.15
    assert rtf <= 0.15, (
        f"Audio RTF {rtf:.4f} exceeded threshold 0.15 (elapsed: {elapsed_time:.3f}s)"
    )
