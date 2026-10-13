"""Temporal activity for transcribing partitioned audio windows with thread isolation."""

import asyncio
import threading
import time

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from internal.config.settings import structlog
from internal.server.probe_server import get_probe_metrics
from internal.temporal.common.heartbeat_runner import HeartbeatRunner, ProgressTracker


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


def _simulate_audio_window_worker(
    input_data: TranscribeAudioWindowInput,
    start_from_offset: float,
    tracker: ProgressTracker,
    stop_event: threading.Event,
) -> tuple[float, int, int]:
    """Worker thread execution simulating heavy Whisper/PyAnnote processing."""
    step_duration = 30.0  # 30-second audio intervals
    processed = start_from_offset
    segment_count = 0
    speaker_count = 1

    while processed < input_data.window_duration_seconds:
        if stop_event.is_set():
            logger.info(
                "audio_worker_thread_aborting_on_stop_event",
                processed_seconds=processed,
            )
            break

        time.sleep(0.01)

        processed = min(processed + step_duration, input_data.window_duration_seconds)
        segment_count += 2
        checkpoint = AudioHeartbeatDetails(
            current_offset_seconds=input_data.start_offset_seconds + processed,
            processed_seconds=processed,
            total_seconds=input_data.window_duration_seconds,
        )
        tracker.update(checkpoint.model_dump())

    return processed, segment_count, speaker_count


@activity.defn(name="TranscribeAudioWindowActivity")
class TranscribeAudioWindowActivity:
    """Temporal activity transcribing audio windows into timestamped diarized segments."""

    def __init__(self) -> None:
        self.metrics = get_probe_metrics()

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

        # 3. Offload compute to worker thread with HeartbeatRunner
        tracker = ProgressTracker()
        runner = HeartbeatRunner(
            activity_name="TranscribeAudioWindowActivity",
            interval_seconds=10.0,
            progress_tracker=tracker,
        )

        async with runner:
            processed_sec, segments, speakers = await asyncio.to_thread(
                _simulate_audio_window_worker,
                input_data,
                start_from_offset,
                tracker,
                runner.stop_event,
            )

        duration = time.monotonic() - start_time
        self.metrics.audio_seconds_processed_total.labels(worker_type="audio").inc(processed_sec)
        self.metrics.task_duration_seconds.labels(
            activity_name="TranscribeAudioWindowActivity"
        ).observe(duration)

        logger.info(
            "transcribe_audio_window_completed",
            tenant_id=input_data.tenant_id,
            audio_id=input_data.audio_id,
            processed_seconds=processed_sec,
            duration_seconds=round(duration, 3),
        )

        return TranscribeAudioWindowOutput(
            audio_seconds_processed=processed_sec,
            segment_count=segments,
            speaker_count=speakers,
            ast_streamed=True,
            duration_seconds=duration,
        )
