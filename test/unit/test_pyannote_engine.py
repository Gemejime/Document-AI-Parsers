import sys
import threading

import pytest

from internal.audio.diarization.pyannote_engine import PyAnnoteEngine
from internal.audio.models import SpeakerTurn


def test_pyannote_engine_initialization() -> None:
    """Verify PyAnnoteEngine initializes with configured settings."""
    engine = PyAnnoteEngine(
        model_id="pyannote/speaker-diarization-3.1",
        hf_token="fake_hf_token",
        cache_dir="/fake/cache",
        min_speakers=2,
        max_speakers=4,
        use_native=False,
    )
    assert engine.model_id == "pyannote/speaker-diarization-3.1"
    assert engine.hf_token == "fake_hf_token"
    assert engine.cache_dir == "/fake/cache"
    assert engine.min_speakers == 2
    assert engine.max_speakers == 4
    assert engine.use_native is False


def test_pyannote_engine_diarize_window() -> None:
    """Verify speaker diarization produces alternating speaker turns."""
    engine = PyAnnoteEngine(use_native=False)
    turns = engine.diarize_window(
        audio_path="/fake/path/audio.wav",
        start_from_offset=0.0,
        duration_seconds=60.0,
    )

    assert len(turns) > 0
    unique_speakers: set[str] = set()
    for turn in turns:
        assert isinstance(turn, SpeakerTurn)
        assert turn.speaker_id.startswith("SPEAKER_")
        unique_speakers.add(turn.speaker_id)
        assert turn.start_time >= 0.0
        assert turn.end_time > turn.start_time
        assert turn.end_time <= 60.0

    assert len(unique_speakers) >= 2


def test_pyannote_engine_speaker_constraints() -> None:
    """Verify min_speakers and max_speakers bounds are respected."""
    engine = PyAnnoteEngine(min_speakers=1, max_speakers=1, use_native=False)
    turns = engine.diarize_window(
        audio_path="/fake/path/audio.wav",
        start_from_offset=0.0,
        duration_seconds=60.0,
    )

    assert len(turns) > 0
    unique_speakers = {t.speaker_id for t in turns}
    assert len(unique_speakers) == 1
    assert "SPEAKER_00" in unique_speakers


def test_pyannote_engine_resumption() -> None:
    """Verify diarization respects start_from_offset."""
    engine = PyAnnoteEngine(use_native=False)
    turns = engine.diarize_window(
        audio_path="/fake/path/audio.wav",
        start_from_offset=30.0,
        duration_seconds=60.0,
    )

    assert len(turns) > 0
    for turn in turns:
        assert turn.start_time >= 30.0


def test_pyannote_engine_cooperative_cancellation() -> None:
    """Verify diarization terminates cleanly when stop_event is set."""
    engine = PyAnnoteEngine(use_native=False)
    stop_event = threading.Event()
    stop_event.set()

    turns = engine.diarize_window(
        audio_path="/fake/path/audio.wav",
        start_from_offset=0.0,
        duration_seconds=120.0,
        stop_event=stop_event,
    )

    assert len(turns) == 0


def test_pyannote_engine_native_error_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify PyAnnoteEngine re-raises errors when use_native=True."""
    monkeypatch.setitem(sys.modules, "pyannote.audio", None)
    with pytest.raises((ModuleNotFoundError, ImportError)):
        PyAnnoteEngine(use_native=True)

    engine = PyAnnoteEngine(use_native=False)
    engine.use_native = True
    engine._pipeline = None
    with pytest.raises(RuntimeError):
        engine.diarize_window(audio_path="/fake/path.wav")
