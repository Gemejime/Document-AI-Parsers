"""Unit tests for gRPC audio transcript streaming and Protobuf serialization."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from internal.audio.models import AlignedTranscriptSegment, WordTiming
from internal.transport.grpc.chunk_client import (
    ChunkServiceClient,
    audio_segments_to_chunk_proto,
    transcript_segment_to_proto,
)


def test_transcript_segment_to_proto() -> None:
    """Verify AlignedTranscriptSegment converts into document_ai.TranscriptSegment."""
    seg = AlignedTranscriptSegment(
        speaker_id="SPEAKER_00",
        start_time_seconds=12.5,
        end_time_seconds=14.8,
        text="speech recognition test",
        words=[
            WordTiming(word="speech", start_time=12.5, end_time=13.1, confidence=0.96),
            WordTiming(word="recognition", start_time=13.2, end_time=14.0, confidence=0.97),
            WordTiming(word="test", start_time=14.1, end_time=14.8, confidence=0.95),
        ],
    )

    proto = transcript_segment_to_proto(seg)
    assert proto.speaker_id == "SPEAKER_00"
    assert proto.start_time_seconds == 12.5
    assert proto.end_time_seconds == 14.8
    assert proto.text == "speech recognition test"
    assert len(proto.words) == 3
    assert proto.words[0].word == "speech"
    assert proto.words[0].start_time == 12.5


def test_audio_segments_to_chunk_proto() -> None:
    """Verify batch of segments converts into AudioTranscriptChunk."""
    seg = AlignedTranscriptSegment(
        speaker_id="SPEAKER_01",
        start_time_seconds=0.0,
        end_time_seconds=2.0,
        text="chunk proto test",
        words=[WordTiming(word="test", start_time=0.0, end_time=2.0, confidence=0.9)],
    )

    chunk_proto = audio_segments_to_chunk_proto(
        tenant_id="tenant-audio-1",
        audio_id="audio-123",
        segments=[seg],
    )

    assert chunk_proto.tenant_id == "tenant-audio-1"
    assert chunk_proto.audio_id == "audio-123"
    assert len(chunk_proto.segments) == 1


@pytest.mark.asyncio
async def test_stream_audio_transcript_with_stub() -> None:
    """Verify stream_audio_transcript sends batches and verifies acknowledgment."""
    mock_stub = MagicMock()
    mock_response = MagicMock()
    mock_response.acknowledged = True
    mock_response.chunks_received = 1
    mock_stub.StreamAudioTranscript = AsyncMock(return_value=mock_response)

    client = ChunkServiceClient(stub=mock_stub)
    seg = AlignedTranscriptSegment(
        speaker_id="SPEAKER_00",
        start_time_seconds=1.0,
        end_time_seconds=2.0,
        text="hello",
        words=[],
    )

    success = await client.stream_audio_transcript(
        tenant_id="tenant-test",
        audio_id="audio-test",
        segments_generator=[seg],
    )

    assert success is True
    assert mock_stub.StreamAudioTranscript.called


@pytest.mark.asyncio
async def test_stream_audio_transcript_offline_fallback() -> None:
    """Verify stream_audio_transcript succeeds under offline/mock target endpoint."""
    client = ChunkServiceClient(target_endpoint="mock")
    seg = AlignedTranscriptSegment(
        speaker_id="SPEAKER_00",
        start_time_seconds=0.0,
        end_time_seconds=1.0,
        text="mock stream",
        words=[],
    )

    success = await client.stream_audio_transcript(
        tenant_id="tenant-mock",
        audio_id="audio-mock",
        segments_generator=[seg],
    )

    assert success is True
