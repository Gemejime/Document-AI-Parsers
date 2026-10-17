import sys
import threading

import pytest

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


def test_whisper_engine_native_error_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify WhisperEngine re-raises errors when use_native=True."""
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises((ModuleNotFoundError, ImportError)):
        WhisperEngine(use_native=True)

    engine = WhisperEngine(use_native=False)
    engine.use_native = True
    engine._model = None
    with pytest.raises(RuntimeError):
        list(engine.transcribe_intervals(audio_path="/fake/path.wav"))


def test_whisper_engine_native_resumption_clamping_and_word_filtering() -> None:
    """Verify native transcription clamps segment start time and excludes words before checkpoint."""
    from unittest.mock import MagicMock

    engine = WhisperEngine(use_native=False)
    engine.use_native = True

    # Construct mock words and segment
    w1 = MagicMock(word="before", start=28.0, end=29.5, probability=0.98)
    w2 = MagicMock(word="mid", start=29.8, end=30.4, probability=0.92)
    w3 = MagicMock(word="after", start=30.5, end=31.2, probability=0.95)

    seg_prior = MagicMock(start=10.0, end=20.0, text="prior segment", words=[])
    seg_resumed = MagicMock(
        start=28.0,
        end=32.0,
        text="before mid after",
        words=[w1, w2, w3],
    )

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([seg_prior, seg_resumed], MagicMock())
    engine._model = mock_model

    # 1. Resuming at offset 30.0
    intervals = list(
        engine.transcribe_intervals(
            audio_path="/fake/path.wav",
            start_from_offset=30.0,
            total_duration_seconds=60.0,
            step_seconds=30.0,
        )
    )

    assert len(intervals) == 1
    segments = intervals[0]
    assert len(segments) == 1
    resumed_seg = segments[0]

    # Start time clamped to 30.0
    assert resumed_seg.start_time == 30.0
    assert resumed_seg.end_time == 32.0

    # Word 1 (start 28.0) and Word 2 (start 29.8) precede 30.0 and are excluded
    # Only Word 3 (start 30.5) has start >= 30.0
    assert len(resumed_seg.words) == 1
    assert resumed_seg.words[0].word == "after"
    assert resumed_seg.words[0].start_time == 30.5

    # 2. Starting at offset 0.0 - preserves existing unclamped start and word filtering
    intervals_zero = list(
        engine.transcribe_intervals(
            audio_path="/fake/path.wav",
            start_from_offset=0.0,
            total_duration_seconds=60.0,
            step_seconds=30.0,
        )
    )
    assert len(intervals_zero) == 1
    all_segs = intervals_zero[0]
    assert len(all_segs) == 2
    # Prior segment included
    assert all_segs[0].start_time == 10.0
    # Second segment start time unclamped
    assert all_segs[1].start_time == 28.0
    assert len(all_segs[1].words) == 3
