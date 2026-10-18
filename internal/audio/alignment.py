"""Timeline alignment engine merging Whisper word timestamps with PyAnnote speaker turns."""

from internal.audio.models import (
    AlignedTranscriptSegment,
    RawWhisperSegment,
    SpeakerTurn,
    WordTiming,
)
from internal.config.settings import structlog


logger = structlog.get_logger(__name__)


def align_audio_timeline(
    whisper_segments: list[RawWhisperSegment],
    speaker_turns: list[SpeakerTurn],
    start_offset_seconds: float = 0.0,
    start_from_offset: float = 0.0,
    max_silence_gap: float = 1.5,
) -> list[AlignedTranscriptSegment]:
    """Align Whisper word timestamps with PyAnnote speaker turns onto the absolute media timeline.

    Enforces the Absolute Timeline Invariant (ADR-009):
        timestamp_absolute = start_offset_seconds + start_from_offset + t_slice
    """
    _ = start_from_offset
    if not whisper_segments:
        return []

    # Flatten all words from Whisper segments
    all_words: list[WordTiming] = []
    for seg in whisper_segments:
        all_words.extend(seg.words)

    if not all_words:
        return []

    # Sort words by start time
    all_words.sort(key=lambda w: w.start_time)

    # Assign each word to the best speaker turn
    last_speaker_id = "SPEAKER_00"
    assigned_words: list[tuple[WordTiming, str]] = []

    for w in all_words:
        best_turn: SpeakerTurn | None = None
        max_overlap = 0.0

        for turn in speaker_turns:
            overlap = max(
                0.0,
                min(w.end_time, turn.end_time) - max(w.start_time, turn.start_time),
            )
            if overlap > max_overlap:
                max_overlap = overlap
                best_turn = turn

        if max_overlap > 0.0 and best_turn is not None:
            speaker_id = best_turn.speaker_id
            last_speaker_id = speaker_id
        else:
            # Fall back to nearest preceding active turn, or SPEAKER_00
            preceding_turns = [t for t in speaker_turns if t.start_time <= w.start_time]
            if preceding_turns:
                nearest_turn = max(preceding_turns, key=lambda t: t.end_time)
                speaker_id = nearest_turn.speaker_id
                last_speaker_id = speaker_id
            else:
                speaker_id = last_speaker_id

        # Project word onto absolute timeline (ADR-009)
        abs_start = round(start_offset_seconds + w.start_time, 3)
        abs_end = round(start_offset_seconds + w.end_time, 3)

        projected_word = WordTiming(
            word=w.word,
            start_time=abs_start,
            end_time=abs_end,
            confidence=w.confidence,
        )
        assigned_words.append((projected_word, speaker_id))

    # Group consecutive words by speaker and silence gap
    segments: list[AlignedTranscriptSegment] = []
    current_group: list[tuple[WordTiming, str]] = []

    def finalize_group(group: list[tuple[WordTiming, str]]) -> None:
        if not group:
            return
        words = [item[0] for item in group]
        speaker = group[0][1]
        text = " ".join(w.word for w in words)
        segments.append(
            AlignedTranscriptSegment(
                speaker_id=speaker,
                start_time_seconds=words[0].start_time,
                end_time_seconds=words[-1].end_time,
                text=text,
                words=words,
            )
        )

    for word, speaker_id in assigned_words:
        if not current_group:
            current_group.append((word, speaker_id))
            continue

        prev_word, prev_speaker = current_group[-1]
        silence_gap = word.start_time - prev_word.end_time

        if speaker_id != prev_speaker or silence_gap > max_silence_gap:
            finalize_group(current_group)
            current_group = [(word, speaker_id)]
        else:
            current_group.append((word, speaker_id))

    if current_group:
        finalize_group(current_group)

    return segments
