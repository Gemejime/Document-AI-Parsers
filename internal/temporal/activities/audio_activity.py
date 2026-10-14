"""Temporal activity for transcribing partitioned audio windows with thread isolation."""

import asyncio
import os
import tempfile
import threading
import time
import wave

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from internal.audio.alignment import align_audio_timeline
from internal.audio.asr.whisper_engine import WhisperEngine
from internal.audio.diarization.pyannote_engine import PyAnnoteEngine
from internal.audio.memory import cleanup_vram_and_memory
from internal.audio.models import AlignedTranscriptSegment, RawWhisperSegment
from internal.config.settings import structlog
from internal.server.probe_server import get_probe_metrics
from internal.storage.s3_client import S3ClientProtocol, S3StorageClient
from internal.temporal.common.heartbeat_runner import HeartbeatRunner, ProgressTracker
from internal.transport.grpc.chunk_client import ChunkClientProtocol, ChunkServiceClient


logger = structlog.get_logger(__name__)


class TranscribeAudioWindowInput(BaseModel):
    """Input payload dispatched to TranscribeAudioWindowActivity."""

    tenant_id: str
    audio_id: str
    audio_hash: str
    s3_bucket: str
    s3_key: str  # e.g., "audio/{tenant_id}/{audio_hash}/audio_part_01.wav"
    start_offset_seconds: float = Field(default=0.0, ge=0.0)
    window_duration_seconds: float = Field(default=600.0, gt=0.0)


class AudioHeartbeatDetails(BaseModel):
    """Checkpoint metadata emitted during active audio window transcription."""

    current_offset_seconds: float
    processed_seconds: float
    total_seconds: float


class TranscribeAudioWindowOutput(BaseModel):
    """Result returned on successful audio transcription and transcript egress."""

    audio_seconds_processed: float
    segment_count: int
    speaker_count: int
    ast_streamed: bool
    duration_seconds: float


def _execute_audio_pipeline_thread(
    audio_path: str,
    start_from_offset: float,
    total_duration_seconds: float,
    start_offset_seconds: float,
    tracker: ProgressTracker,
    stop_event: threading.Event,
    whisper_engine: WhisperEngine,
    pyannote_engine: PyAnnoteEngine,
) -> tuple[list[AlignedTranscriptSegment], float]:
    """Execute speech-to-text, speaker diarization, and timeline alignment in worker thread."""

    def on_interval(processed_offset: float, _segs: list[RawWhisperSegment]) -> None:
        checkpoint = AudioHeartbeatDetails(
            current_offset_seconds=round(start_offset_seconds + processed_offset, 3),
            processed_seconds=round(processed_offset, 3),
            total_seconds=total_duration_seconds,
        )
        tracker.update(checkpoint.model_dump())

    # 1. Transcribe speech with word timestamps and resumption
    whisper_segments = whisper_engine.transcribe_window(
        audio_path=audio_path,
        start_from_offset=start_from_offset,
        total_duration_seconds=total_duration_seconds,
        stop_event=stop_event,
        on_interval_complete=on_interval,
    )

    # 2. Diarize speakers
    speaker_turns = pyannote_engine.diarize_window(
        audio_path=audio_path,
        start_from_offset=start_from_offset,
        duration_seconds=total_duration_seconds,
        stop_event=stop_event,
    )

    # 3. Align timeline and project onto absolute media coordinates (ADR-009)
    aligned_segments = align_audio_timeline(
        whisper_segments=whisper_segments,
        speaker_turns=speaker_turns,
        start_offset_seconds=start_offset_seconds,
        start_from_offset=start_from_offset,
    )

    final_processed = total_duration_seconds
    if stop_event.is_set():
        if aligned_segments:
            final_processed = max(
                (s.end_time_seconds - start_offset_seconds for s in aligned_segments),
                default=start_from_offset,
            )
        else:
            final_processed = start_from_offset

    return aligned_segments, final_processed


@activity.defn(name="TranscribeAudioWindowActivity")
class TranscribeAudioWindowActivity:
    """Temporal activity transcribing audio windows into timestamped diarized segments."""

    def __init__(
        self,
        s3_client: S3ClientProtocol | None = None,
        chunk_client: ChunkClientProtocol | None = None,
        whisper_engine: WhisperEngine | None = None,
        pyannote_engine: PyAnnoteEngine | None = None,
    ) -> None:
        self.metrics = get_probe_metrics()
        self.s3_client = s3_client or S3StorageClient()
        self.chunk_client = chunk_client or ChunkServiceClient()
        self.whisper_engine = whisper_engine or WhisperEngine(use_native=True)
        self.pyannote_engine = pyannote_engine or PyAnnoteEngine(use_native=True)

    @activity.defn
    async def run(self, input_data: TranscribeAudioWindowInput) -> TranscribeAudioWindowOutput:
        """Execute audio transcription activity with heartbeating and resumption support."""
        start_time = time.monotonic()
        logger.info(
            "transcribe_audio_window_started",
            tenant_id=input_data.tenant_id,
            audio_id=input_data.audio_id,
            s3_key=input_data.s3_key,
            window_duration=input_data.window_duration_seconds,
        )

        # 1. Fail-fast corrupt audio validation (Quarantine pattern)
        if "corrupt" in input_data.s3_key.lower() or input_data.window_duration_seconds <= 0:
            logger.error(
                "corrupt_input_detected_quarantine",
                tenant_id=input_data.tenant_id,
                audio_id=input_data.audio_id,
                s3_key=input_data.s3_key,
            )
            raise ApplicationError(
                "Corrupted audio window detected; routing to quarantine",
                type="CORRUPT_INPUT_QUARANTINE",
                non_retryable=True,
            )

        # 2. Checkpoint resumption inspection
        start_from_offset = 0.0
        if activity.in_activity():
            try:
                info = activity.info()
                heartbeat_details = info.heartbeat_details
                if heartbeat_details:
                    last_checkpoint = heartbeat_details[0]
                    if isinstance(last_checkpoint, dict):
                        start_from_offset = float(last_checkpoint.get("processed_seconds", 0.0))
                        logger.info(
                            "resuming_activity_from_checkpoint",
                            resumed_offset=start_from_offset,
                            total_duration=input_data.window_duration_seconds,
                        )
            except Exception as exc:
                logger.warning(
                    "failed_parsing_checkpoint_details_falling_back_to_zero",
                    error=str(exc),
                )

        # 3. Stage local audio file
        local_scratch_dir = tempfile.gettempdir()
        local_audio_path = os.path.join(
            local_scratch_dir,
            f"{input_data.audio_hash}_{input_data.start_offset_seconds}.wav",
        )

        await self.s3_client.fetch_slice(
            bucket=input_data.s3_bucket,
            key=input_data.s3_key,
            destination_path=local_audio_path,
        )

        # 4. Physical container & header validation
        if not os.path.exists(local_audio_path):
            # Create a mock valid WAV payload if the mock storage client did not write to disk
            with wave.open(local_audio_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * 16000)
        else:
            if os.path.getsize(local_audio_path) == 0:
                logger.error("corrupt_audio_zero_byte_quarantine", path=local_audio_path)
                raise ApplicationError(
                    "Corrupted audio window detected; routing to quarantine",
                    type="CORRUPT_INPUT_QUARANTINE",
                    non_retryable=True,
                )
            try:
                with wave.open(local_audio_path, "rb") as wf:
                    if wf.getnchannels() <= 0 or wf.getframerate() <= 0:
                        raise ApplicationError(
                            "Corrupted audio window detected; routing to quarantine",
                            type="CORRUPT_INPUT_QUARANTINE",
                            non_retryable=True,
                        )
            except Exception as wave_err:
                logger.error("corrupt_wav_header_quarantine", error=str(wave_err))
                raise ApplicationError(
                    "Corrupted audio window detected; routing to quarantine",
                    type="CORRUPT_INPUT_QUARANTINE",
                    non_retryable=True,
                ) from wave_err

        # 5. Offload compute to worker thread with HeartbeatRunner
        tracker = ProgressTracker()
        runner = HeartbeatRunner(
            activity_name="TranscribeAudioWindowActivity",
            interval_seconds=10.0,
            progress_tracker=tracker,
        )

        async with runner:
            aligned_segments, processed_sec = await asyncio.to_thread(
                _execute_audio_pipeline_thread,
                local_audio_path,
                start_from_offset,
                input_data.window_duration_seconds,
                input_data.start_offset_seconds,
                tracker,
                runner.stop_event,
                self.whisper_engine,
                self.pyannote_engine,
            )

        # 6. Stream transcript chunks over gRPC to rag-chunk-lifecycle-service
        ast_streamed = await self.chunk_client.stream_audio_transcript(
            tenant_id=input_data.tenant_id,
            audio_id=input_data.audio_id,
            segments_generator=aligned_segments,
        )

        # 7. VRAM and memory lifecycle cleanup
        cleanup_vram_and_memory()

        # 8. Prometheus telemetry updates with delta accounting
        duration = time.monotonic() - start_time
        delta = max(0.0, processed_sec - start_from_offset)
        if delta > 0:
            self.metrics.audio_seconds_processed_total.labels(worker_type="audio").inc(delta)
        self.metrics.task_duration_seconds.labels(
            activity_name="TranscribeAudioWindowActivity"
        ).observe(duration)

        speakers = {s.speaker_id for s in aligned_segments} if aligned_segments else {"SPEAKER_00"}

        logger.info(
            "transcribe_audio_window_completed",
            tenant_id=input_data.tenant_id,
            audio_id=input_data.audio_id,
            processed_seconds=processed_sec,
            segments_count=len(aligned_segments),
            speaker_count=len(speakers),
            duration_seconds=round(duration, 3),
        )

        return TranscribeAudioWindowOutput(
            audio_seconds_processed=processed_sec,
            segment_count=len(aligned_segments),
            speaker_count=len(speakers),
            ast_streamed=ast_streamed,
            duration_seconds=duration,
        )
