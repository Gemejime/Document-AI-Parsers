"""Unit tests for Faster-Whisper ASR engine adapter."""

import threading

from internal.audio.asr.whisper_engine import WhisperEngine
from internal.audio.models import RawWhisperSegment


def test_whisper_engine_initialization() -> None:
    """Verify WhisperEngine initializes with configured settings."""
    engine = WhisperEngine(
        model_size="base",
        device="cpu",
        compute_type="float32",
        beam_size=3,
        use_native=False,
    )
    assert engine.model_size == "base"
    assert engine.device == "cpu"
    assert engine.compute_type == "float32"
    assert engine.beam_size == 3
    assert engine.use_native is False


def test_whisper_engine_transcribe_window() -> None:
    """Verify speech-to-text window transcription generates segments with word timings."""
    engine = WhisperEngine(use_native=False)
    segments = engine.transcribe_window(
        audio_path="/fake/path/audio.wav",
        start_from_offset=0.0,
        total_duration_seconds=60.0,
        step_seconds=30.0,
    )

    assert len(segments) > 0
    for seg in segments:
        assert isinstance(seg, RawWhisperSegment)
        assert seg.text
        assert seg.start_time >= 0.0
        assert seg.end_time > seg.start_time
        assert len(seg.words) > 0
        for w in seg.words:
            assert w.word
            assert w.start_time >= 0.0
            assert w.end_time >= w.start_time
            assert 0.0 <= w.confidence <= 1.0


def test_whisper_engine_resumption() -> None:
    """Verify transcription correctly skips audio prior to start_from_offset."""
    engine = WhisperEngine(use_native=False)
    resumed_offset = 30.0
    segments = engine.transcribe_window(
        audio_path="/fake/path/audio.wav",
        start_from_offset=resumed_offset,
        total_duration_seconds=60.0,
        step_seconds=15.0,
    )

    assert len(segments) > 0
    for seg in segments:
        assert seg.start_time >= resumed_offset


def test_whisper_engine_cooperative_cancellation() -> None:
    """Verify transcription stops when stop_event is set."""
    engine = WhisperEngine(use_native=False)
    stop_event = threading.Event()
    stop_event.set()

    segments = engine.transcribe_window(
        audio_path="/fake/path/audio.wav",
        start_from_offset=0.0,
        total_duration_seconds=120.0,
        step_seconds=30.0,
        stop_event=stop_event,
    )

    assert len(segments) == 0


def test_whisper_engine_native_error_propagation() -> None:
    """Verify WhisperEngine re-raises errors when use_native=True."""
    import pytest

    with pytest.raises((ModuleNotFoundError, ImportError)):
        WhisperEngine(use_native=True)

    engine = WhisperEngine(use_native=False)
    engine.use_native = True
    engine._model = None
    with pytest.raises(RuntimeError):
        list(engine.transcribe_intervals(audio_path="/fake/path.wav"))
