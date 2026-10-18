"""Faster-Whisper Large-v3 speech-to-text engine with word timestamps and resumption."""

import os
import threading
from collections.abc import Callable, Iterator
from typing import Any

from internal.audio.models import RawWhisperSegment, WordTiming
from internal.config.settings import settings, structlog


logger = structlog.get_logger(__name__)


class WhisperEngine:
    """CTranslate2-based Faster-Whisper ASR engine adapter with progressive resumption."""

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        beam_size: int | None = None,
        cache_dir: str | None = None,
        use_native: bool = True,
    ) -> None:
        self.model_size = model_size or settings.WHISPER_MODEL_SIZE
        self.device = device or settings.WHISPER_DEVICE
        self.compute_type = compute_type or settings.WHISPER_COMPUTE_TYPE
        self.beam_size = beam_size if beam_size is not None else settings.WHISPER_BEAM_SIZE
        self.cache_dir = cache_dir or settings.WHISPER_CACHE_DIR
        self.use_native = use_native
        self._model: Any = None

        if self.use_native:
            try:
                from faster_whisper import WhisperModel

                os.makedirs(self.cache_dir, exist_ok=True)
                self._model = WhisperModel(
                    model_size_or_path=self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=self.cache_dir,
                )
                logger.info(
                    "faster_whisper_native_initialized",
                    model_size=self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                )
            except Exception as exc:
                logger.error(
                    "faster_whisper_native_initialization_failed",
                    error=str(exc),
                )
                self._model = None
                raise

    def _generate_simulated_interval(
        self,
        interval_start: float,
        interval_end: float,
    ) -> list[RawWhisperSegment]:
        """Generate high-fidelity simulated ASR segments with word timings."""
        duration = interval_end - interval_start
        if duration <= 0:
            return []

        # Divide interval into short conversational utterances (e.g., 5-8s segments)
        sample_words = [
            "the",
            "quick",
            "brown",
            "fox",
            "jumps",
            "over",
            "the",
            "lazy",
            "dog",
            "in",
            "the",
            "multimodal",
            "rag",
            "pipeline",
        ]

        segments: list[RawWhisperSegment] = []
        cur_t = interval_start + 0.5

        segment_idx = 0
        while cur_t + 2.0 <= interval_end:
            seg_words: list[WordTiming] = []
            word_count = min(6, len(sample_words))
            w_start = cur_t

            for i in range(word_count):
                word_str = sample_words[(segment_idx * 4 + i) % len(sample_words)]
                w_end = round(w_start + 0.35, 3)
                seg_words.append(
                    WordTiming(
                        word=word_str,
                        start_time=w_start,
                        end_time=w_end,
                        confidence=0.96,
                    )
                )
                w_start = round(w_end + 0.1, 3)

            seg_text = " ".join(w.word for w in seg_words)
            segments.append(
                RawWhisperSegment(
                    text=seg_text,
                    start_time=cur_t,
                    end_time=seg_words[-1].end_time,
                    words=seg_words,
                )
            )
            cur_t = round(seg_words[-1].end_time + 1.2, 3)
            segment_idx += 1

        return segments

    def transcribe_intervals(
        self,
        audio_path: str,
        start_from_offset: float = 0.0,
        total_duration_seconds: float = 600.0,
        step_seconds: float = 30.0,
        stop_event: threading.Event | None = None,
        on_interval_complete: Callable[[float, list[RawWhisperSegment]], None] | None = None,
    ) -> Iterator[list[RawWhisperSegment]]:
        """Yield transcribed speech segments interval-by-interval with resumption support."""
        if start_from_offset >= total_duration_seconds:
            logger.info(
                "whisper_transcription_already_complete",
                start_from_offset=start_from_offset,
                total_duration=total_duration_seconds,
            )
            return

        current_offset = start_from_offset

        # Check native execution availability
        if self.use_native:
            if self._model is None:
                raise RuntimeError("Native Faster-Whisper model is not initialized")
            try:
                # Transcribe audio file using native Faster-Whisper
                segments_iter, _info = self._model.transcribe(
                    audio_path,
                    beam_size=self.beam_size,
                    word_timestamps=True,
                )

                buffered_segments: list[RawWhisperSegment] = []
                next_checkpoint = current_offset + step_seconds

                for seg in segments_iter:
                    if stop_event is not None and stop_event.is_set():
                        logger.info(
                            "whisper_worker_aborting_on_stop_event",
                            current_offset=current_offset,
                        )
                        break

                    # Skip segments occurring before resumption checkpoint
                    if start_from_offset > 0.0:
                        if seg.end <= start_from_offset:
                            continue
                    elif seg.end < start_from_offset:
                        continue

                    words = [
                        WordTiming(
                            word=w.word.strip(),
                            start_time=float(w.start),
                            end_time=float(w.end),
                            confidence=float(getattr(w, "probability", 0.95)),
                        )
                        for w in getattr(seg, "words", [])
                        if (
                            getattr(w, "start", 0.0) >= start_from_offset
                            if start_from_offset > 0.0
                            else getattr(w, "end", 0.0) >= start_from_offset
                        )
                    ]

                    seg_start = (
                        max(float(seg.start), start_from_offset)
                        if start_from_offset > 0.0
                        else float(seg.start)
                    )

                    raw_seg = RawWhisperSegment(
                        text=seg.text.strip(),
                        start_time=seg_start,
                        end_time=float(seg.end),
                        words=words,
                    )
                    buffered_segments.append(raw_seg)

                    if seg.end >= next_checkpoint:
                        current_offset = seg.end
                        if on_interval_complete:
                            on_interval_complete(current_offset, buffered_segments)
                        yield buffered_segments
                        buffered_segments = []
                        next_checkpoint = current_offset + step_seconds

                if buffered_segments:
                    current_offset = max(current_offset, buffered_segments[-1].end_time)
                    if on_interval_complete:
                        on_interval_complete(current_offset, buffered_segments)
                    yield buffered_segments
                return
            except Exception as exc:
                logger.error(
                    "native_whisper_transcription_failed",
                    error=str(exc),
                )
                raise

        # High-fidelity simulated progressive iteration (used when use_native=False)
        while current_offset < total_duration_seconds:
            if stop_event is not None and stop_event.is_set():
                logger.info(
                    "whisper_simulated_aborting_on_stop_event",
                    current_offset=current_offset,
                )
                break

            interval_end = min(current_offset + step_seconds, total_duration_seconds)
            interval_segments = self._generate_simulated_interval(
                interval_start=current_offset,
                interval_end=interval_end,
            )

            current_offset = interval_end
            if on_interval_complete:
                on_interval_complete(current_offset, interval_segments)

            yield interval_segments

    def transcribe_window(
        self,
        audio_path: str,
        start_from_offset: float = 0.0,
        total_duration_seconds: float = 600.0,
        step_seconds: float = 30.0,
        stop_event: threading.Event | None = None,
        on_interval_complete: Callable[[float, list[RawWhisperSegment]], None] | None = None,
    ) -> list[RawWhisperSegment]:
        """Transcribe entire audio window collecting all interval segments."""
        all_segments: list[RawWhisperSegment] = []
        for interval_segs in self.transcribe_intervals(
            audio_path=audio_path,
            start_from_offset=start_from_offset,
            total_duration_seconds=total_duration_seconds,
            step_seconds=step_seconds,
            stop_event=stop_event,
            on_interval_complete=on_interval_complete,
        ):
            all_segments.extend(interval_segs)
        return all_segments
