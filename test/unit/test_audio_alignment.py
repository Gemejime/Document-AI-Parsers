"""Unit tests for audio timeline alignment, speaker assignment, and absolute timeline mapping."""

from internal.audio.alignment import align_audio_timeline
from internal.audio.models import (
    AlignedTranscriptSegment,
    RawWhisperSegment,
    SpeakerTurn,
    WordTiming,
)


def test_audio_alignment_basic_overlap() -> None:
    """Verify words are assigned to the speaker turn with maximal temporal overlap."""
    whisper_segments = [
        RawWhisperSegment(
            text="hello world",
            start_time=1.0,
            end_time=3.0,
            words=[
                WordTiming(word="hello", start_time=1.0, end_time=1.8, confidence=0.98),
                WordTiming(word="world", start_time=2.0, end_time=2.8, confidence=0.95),
            ],
        ),
        RawWhisperSegment(
            text="how are you",
            start_time=5.0,
            end_time=7.5,
            words=[
                WordTiming(word="how", start_time=5.0, end_time=5.5, confidence=0.92),
                WordTiming(word="are", start_time=5.6, end_time=6.2, confidence=0.94),
                WordTiming(word="you", start_time=6.3, end_time=7.0, confidence=0.96),
            ],
        ),
    ]

    speaker_turns = [
        SpeakerTurn(speaker_id="SPEAKER_00", start_time=0.5, end_time=3.5),
        SpeakerTurn(speaker_id="SPEAKER_01", start_time=4.5, end_time=8.0),
    ]

    aligned = align_audio_timeline(
        whisper_segments=whisper_segments,
        speaker_turns=speaker_turns,
        start_offset_seconds=100.0,
        start_from_offset=0.0,
    )

    assert len(aligned) == 2
    assert aligned[0].speaker_id == "SPEAKER_00"
    assert aligned[0].text == "hello world"
    assert aligned[0].start_time_seconds == 101.0
    assert aligned[0].end_time_seconds == 102.8

    assert aligned[1].speaker_id == "SPEAKER_01"
    assert aligned[1].text == "how are you"
    assert aligned[1].start_time_seconds == 105.0
    assert aligned[1].end_time_seconds == 107.0


def test_audio_alignment_silence_gap_fallback() -> None:
    """Verify words occurring during silence gaps fall back to preceding active speaker."""
    whisper_segments = [
        RawWhisperSegment(
            text="unassigned gap text",
            start_time=10.0,
            end_time=12.0,
            words=[
                WordTiming(word="unassigned", start_time=10.0, end_time=10.8, confidence=0.9),
                WordTiming(word="gap", start_time=10.9, end_time=11.3, confidence=0.9),
                WordTiming(word="text", start_time=11.4, end_time=12.0, confidence=0.9),
            ],
        )
    ]

    # Speaker turns do not overlap with [10.0, 12.0]
    speaker_turns = [
        SpeakerTurn(speaker_id="SPEAKER_01", start_time=2.0, end_time=6.0),
    ]

    aligned = align_audio_timeline(
        whisper_segments=whisper_segments,
        speaker_turns=speaker_turns,
        start_offset_seconds=0.0,
        start_from_offset=0.0,
    )

    assert len(aligned) == 1
    # Fallback to preceding active speaker turn (SPEAKER_01)
    assert aligned[0].speaker_id == "SPEAKER_01"


def test_audio_alignment_split_on_silence_threshold() -> None:
    """Verify segment splits when silence between consecutive words exceeds 1.5 seconds."""
    whisper_segments = [
        RawWhisperSegment(
            text="word1 word2",
            start_time=1.0,
            end_time=5.0,
            words=[
                WordTiming(word="word1", start_time=1.0, end_time=1.5, confidence=0.95),
                # 2.0s gap > 1.5s threshold
                WordTiming(word="word2", start_time=3.5, end_time=4.0, confidence=0.95),
            ],
        )
    ]

    speaker_turns = [
        SpeakerTurn(speaker_id="SPEAKER_00", start_time=0.0, end_time=10.0),
    ]

    aligned = align_audio_timeline(
        whisper_segments=whisper_segments,
        speaker_turns=speaker_turns,
        start_offset_seconds=0.0,
        start_from_offset=0.0,
        max_silence_gap=1.5,
    )

    assert len(aligned) == 2
    assert aligned[0].text == "word1"
    assert aligned[1].text == "word2"
    assert aligned[0].speaker_id == "SPEAKER_00"
    assert aligned[1].speaker_id == "SPEAKER_00"


def test_audio_alignment_absolute_timeline_projection() -> None:
    """Verify ADR-009: all timestamps projected onto absolute media timeline with offset resumption."""
    start_offset = 600.0  # Window 2 begins at 10 minutes
    start_from_offset = 120.0  # Resumed 2 minutes into window

    whisper_segments = [
        RawWhisperSegment(
            text="resumed utterance",
            start_time=121.0,
            end_time=123.0,
            words=[
                WordTiming(word="resumed", start_time=121.0, end_time=121.8, confidence=0.97),
                WordTiming(word="utterance", start_time=122.0, end_time=122.9, confidence=0.98),
            ],
        )
    ]

    speaker_turns = [
        SpeakerTurn(speaker_id="SPEAKER_00", start_time=120.0, end_time=130.0),
    ]

    aligned = align_audio_timeline(
        whisper_segments=whisper_segments,
        speaker_turns=speaker_turns,
        start_offset_seconds=start_offset,
        start_from_offset=start_from_offset,
    )

    assert len(aligned) == 1
    seg: AlignedTranscriptSegment = aligned[0]

    # Absolute timestamp = 600.0 + 121.0 = 721.0
    assert seg.start_time_seconds == 721.0
    assert seg.end_time_seconds == 722.9
    assert seg.words[0].start_time == 721.0
    assert seg.words[0].end_time == 721.8
    assert seg.words[1].start_time == 722.0
    assert seg.words[1].end_time == 722.9
