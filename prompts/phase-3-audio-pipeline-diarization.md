# Phase 3: Dedicated Audio Pipeline & Speaker Diarization - Implementation Plan & Prompt

## 1. Executive Summary & Objective

Phase 3 implements the **Dedicated Audio Transcription & Speaker Diarization Pipeline** for the `rag-document-ai-worker` microservice, strictly adhering to:
- [`tasks/RAG Document AI Worker Implementation Plan.pdf`](tasks/RAG%20Document%20AI%20Worker%20Implementation%20Plan.pdf)
- [`tasks/Production-Grade Multimodal RAG Architectural Specification.pdf`](tasks/Production-Grade%20Multimodal%20RAG%20Architectural%20Specification.pdf)
- [`tasks/Go and Python Microservices Architecture.pdf`](tasks/Go%20and%20Python%20Microservices%20Architecture.pdf)
- [`tasks/Disaggregated Hybrid Model Architecture.pdf`](tasks/Disaggregated%20Hybrid%20Model%20Architecture.pdf)
- [`tasks/Production Grade Backend Concepts.pdf`](tasks/Production%20Grade%20Backend%20Concepts.pdf)
- [`GEMINI.md`](GEMINI.md) & [`.ai/architecture.md`](.ai/architecture.md)
- [`.ai/doc-api.md`](.ai/doc-api.md)
- [`.ai/security.md`](.ai/security.md)
- [`.ai/code-standards.md`](.ai/code-standards.md)
- [`.ai/progress-tracker.md`](.ai/progress-tracker.md)

---

### Core Deliverables for Phase 3:

1. **Audio Models & Data Structures (`internal/audio/models.py`)**:
   - `WordTiming`: Normalized word timing containing `word: str`, `start_time: float`, `end_time: float`, `confidence: float`. All timestamps are projected onto the **absolute media timeline**.
   - `RawWhisperSegment`: Output from Whisper with text, relative start/end, and word timings.
   - `SpeakerTurn`: Output from PyAnnote with speaker ID (e.g. `SPEAKER_00`), start time, end time.
   - `AlignedTranscriptSegment`: Complete transcript segment aligning speaker turn with text and word timestamps mapped to the **absolute media timeline** (`start_offset_seconds + start_from_offset + t_slice`).

2. **Faster-Whisper Large-v3 Speech-to-Text Engine (`internal/audio/asr/whisper_engine.py`)**:
   - High-throughput CTranslate2-based Whisper engine supporting model caching from `settings.WHISPER_CACHE_DIR`.
   - Word-level timestamp generation (`word_timestamps=True`).
   - Resumption support: skips audio prior to `start_from_offset` for mid-window recovery.
   - Progressive audio interval processing (e.g. 15–30s strides) yielding progress across the thread boundary for active heartbeat reporting.
   - Cooperative cancellation via `threading.Event` (`stop_event`).
   - Mock/simulated fallback layer for testing in environments without compiled CUDA/CTranslate2 binaries or GPU hardware, with seamless integration for native `faster_whisper.WhisperModel`.

3. **PyAnnote Speaker Diarization Engine (`internal/audio/diarization/pyannote_engine.py`)**:
   - Speaker diarization runner clustering voice prints into discrete speaker identities (`SPEAKER_00`, `SPEAKER_01`, etc.).
   - Support for Hugging Face cache and model weights from `settings.HF_HOME`.
   - Hugging Face token support via `settings.HF_TOKEN` (required for gated PyAnnote 3.1 models on HF Hub) with secured logging redaction.
   - Configurable speaker count constraints (`min_speakers`, `max_speakers`).
   - Cooperative cancellation and progressive interval processing.
   - Mock/simulated fallback layer for testing without downloaded neural checkpoints.

4. **Timeline Alignment & Merging Engine (`internal/audio/alignment.py`)**:
   - Aligns Whisper word-level timestamps with PyAnnote speaker diarization intervals.
   - For each word, computes overlap against speaker turns:
     $$\text{overlap}(w, s) = \max(0.0, \min(w.\text{end}, s.\text{end}) - \max(w.\text{start}, s.\text{start}))$$
   - Assigns word to the speaker with $\max(\text{overlap})$.
   - Handles silence gaps: if a word has zero overlap with any speaker turn, assigns it to the nearest preceding active speaker turn (or `"SPEAKER_00"` if at window start).
   - Groups consecutive words assigned to the same speaker into an `AlignedTranscriptSegment` (segment split occurs on speaker switch or when silence gap exceeds $1.5\text{s}$).
   - Enforces the **Absolute Timeline Invariant (ADR-009)**: All segment and word timestamps are projected onto the global media timeline taking into account mid-window resumption:
     $$\text{timestamp}_{\text{absolute}} = \text{start\_offset\_seconds} + \text{start\_from\_offset} + t_{\text{slice}}$$
   - Both `TranscriptSegment.start_time_seconds` / `end_time_seconds` AND `WordTimestamp.start_time` / `end_time` MUST be projected onto this absolute timeline.

5. **Streaming gRPC Audio Transport Client (`internal/transport/grpc/chunk_client.py`)**:
   - Completes implementation of `stream_audio_transcript` adhering to `DocumentAIChunkService.StreamAudioTranscript` in `api/proto/v1/document_ai.proto`.
   - Converts `AlignedTranscriptSegment` into Protobuf `AudioTranscriptChunk`, `TranscriptSegment`, and `WordTimestamp`.
   - Streams chunks in batches (e.g., 20–50 segments or progressive intervals) over HTTP/2 gRPC channel to `rag-chunk-lifecycle-service`.
   - Verifies server acknowledgment (`ASTStreamResponse.acknowledged == True`).
   - Resilient fallback for testing and offline environments matching `stream_document_ast`.

6. **VRAM & Memory Lifecycle Management (`internal/audio/memory.py`)**:
   - Wraps PyTorch CUDA empty cache and garbage collection after each audio window execution.
   - Prevents memory leaks and fragmentation under `jemalloc` (`LD_PRELOAD=/usr/lib/libjemalloc.so`).

7. **Temporal Activity Integration (`internal/temporal/activities/audio_activity.py`)**:
   - Connects `TranscribeAudioWindowActivity` to real audio pipeline components:
     - S3 audio fetch (`audio_part_*.wav`).
     - Physical audio container & header validation: uses standard `wave` or `soundfile` to inspect RIFF headers, channel counts, and sample rates.
     - Fail-fast non-retryable `ApplicationError("Corrupted audio window detected; routing to quarantine", type="CORRUPT_INPUT_QUARANTINE", non_retryable=True)` on corrupted headers, truncated files, zero-byte payloads, or non-audio content.
     - Thread-isolated execution via `asyncio.to_thread()`.
     - 10-second heartbeat emissions via `HeartbeatRunner` and `ProgressTracker` with `AudioHeartbeatDetails`.
     - Mid-window checkpoint recovery (resuming from `start_from_offset`).
     - gRPC streaming to `rag-chunk-lifecycle-service`.
     - Prometheus telemetry updates with delta accounting:
       $$\Delta = \text{processed\_seconds} - \text{start\_from\_offset}$$
       incrementing `doc_ai_audio_seconds_processed_total` strictly by $\Delta$ to eliminate double-counting on activity retries.

8. **Configuration Settings (`internal/config/settings.py`)**:
   - Add audio configuration parameters:
     - `WHISPER_MODEL_SIZE: str = "large-v3"`
     - `WHISPER_DEVICE: str = "cuda"`
     - `WHISPER_COMPUTE_TYPE: str = "float16"`
     - `WHISPER_BEAM_SIZE: int = 5`
     - `PYANNOTE_MODEL_ID: str = "pyannote/speaker-diarization-3.1"`
     - `HF_TOKEN: str | None = None` (Hugging Face Hub authentication token for gated PyAnnote 3.1 models)
     - `AUDIO_SAMPLE_RATE: int = 16000`
     - `AUDIO_PROGRESS_STEP_SECONDS: float = 30.0`

9. **Comprehensive Verification & Quality Suite**:
   - Unit tests covering Whisper transcription, PyAnnote diarization, word-level alignment, absolute timestamps, and gRPC transcript protobuf serialization.
   - Integration tests covering `TranscribeAudioWindowActivity`, checkpoint resumption (mid-window offset recovery), and physical corrupt input quarantine.
   - Performance test validating real-time factor (RTF $\le 0.15$) and streaming latency.
   - Synchronize `.ai/doc-api.md` and `.ai/progress-tracker.md`.
   - Strict Ruff linting, formatting, and strict Mypy typing.
   - Conventional git commits (minimum 2 commits).

---

## 2. Directory Structure & Files to Modify / Create

```
rag-document-ai-worker/
├── internal/
│   ├── config/
│   │   └── settings.py               # [Modify] Add Whisper, PyAnnote, HF_TOKEN audio settings
│   ├── audio/
│   │   ├── __init__.py               # [New]
│   │   ├── models.py                 # [New] Audio data models (WordTiming, SpeakerTurn, AlignedTranscriptSegment)
│   │   ├── memory.py                 # [New] Audio VRAM cleanup protocol
│   │   ├── asr/
│   │   │   ├── __init__.py           # [New]
│   │   │   └── whisper_engine.py     # [New] Faster-Whisper Large-v3 runner with word-level timestamps & streaming
│   │   ├── diarization/
│   │   │   ├── __init__.py           # [New]
│   │   │   └── pyannote_engine.py    # [New] PyAnnote speaker diarization runner with HF_TOKEN support
│   │   └── alignment.py              # [New] Timeline alignment & absolute offset projection
│   ├── transport/
│   │   └── grpc/
│   │       └── chunk_client.py       # [Modify] Implement StreamAudioTranscript RPC client
│   └── temporal/
│       └── activities/
│           └── audio_activity.py     # [Modify] Production TranscribeAudioWindowActivity implementation
├── test/
│   ├── unit/
│   │   ├── test_whisper_engine.py    # [New] Whisper transcription & word timestamps unit tests
│   │   ├── test_pyannote_engine.py   # [New] PyAnnote speaker diarization unit tests
│   │   ├── test_audio_alignment.py   # [New] Audio timeline alignment & absolute offsets unit tests
│   │   └── test_audio_grpc_stream.py # [New] gRPC audio transcript streaming tests
│   ├── integration/
│   │   ├── test_audio_pipeline_integration.py # [New] End-to-end audio activity integration
│   │   └── test_audio_checkpoint_recovery.py  # [New] Mid-window checkpoint recovery tests
│   └── performance/
│       └── test_audio_rtf_perf.py    # [New] Audio RTF <= 0.15 performance assertion
├── .ai/
│   ├── doc-api.md                    # [Modify] Synchronize with audio components & schemas
│   └── progress-tracker.md           # [Modify] Mark Phase 3 complete
└── prompts/
    └── phase-3-audio-pipeline-diarization.md # [Updated] Fully hardened implementation plan
```

---

## 3. Detailed Component Architecture & Protocols

### A. Audio Data Models (`internal/audio/models.py`)
```python
@dataclass
class WordTiming:
    word: str
    start_time: float  # Absolute media timeline timestamp
    end_time: float  # Absolute media timeline timestamp
    confidence: float


@dataclass
class SpeakerTurn:
    speaker_id: str
    start_time: float  # Relative to processed slice
    end_time: float  # Relative to processed slice


@dataclass
class RawAudioChunk:
    text: str
    start_time: float
    end_time: float
    words: list[WordTiming]


@dataclass
class AlignedTranscriptSegment:
    speaker_id: str
    start_time_seconds: float  # Absolute timeline timestamp
    end_time_seconds: float  # Absolute timeline timestamp
    text: str
    words: list[WordTiming]
```

### B. Whisper Engine (`internal/audio/asr/whisper_engine.py`)
- Standardizes on **Faster-Whisper (Large-v3)** using CTranslate2.
- Supports progressive window slicing and yielding across the thread boundary.
- Supports `start_from_offset` to skip previously processed audio on activity resumption.
- Cooperative cancellation via `stop_event.is_set()`.
- Implements dual execution: native `faster_whisper` when available, lightweight high-fidelity mock when libraries/GPU are absent in unit tests.

### C. PyAnnote Diarization Engine (`internal/audio/diarization/pyannote_engine.py`)
- Standardizes on **PyAnnote-Audio 3.1**.
- Extracts speaker turns with start/end boundaries and assigned speaker labels (`SPEAKER_00`, `SPEAKER_01`).
- Supports Hugging Face authentication token via `settings.HF_TOKEN` for accessing gated weights.
- Dual execution: native `pyannote.audio.Pipeline` when available, structured mock for test suites.

### D. Timeline Alignment & Absolute Invariant (`internal/audio/alignment.py`)
- Merges raw Whisper chunks and PyAnnote speaker turns.
- For each word and phrase, calculates overlap with speaker turns:
  $$\text{overlap} = \max(0.0, \min(\text{word.end}, \text{turn.end}) - \max(\text{word.start}, \text{turn.start}))$$
- Assigns the segment to the dominant speaker. For silence gaps with zero overlap, falls back to the preceding active speaker turn.
- Groups consecutive words for the same speaker into an `AlignedTranscriptSegment` (splitting on speaker change or silence $> 1.5\text{s}$).
- Projects all local offsets to the absolute media timeline accounting for resumption offset:
  $$\text{start}_{\text{abs}} = \text{start\_offset\_seconds} + \text{start\_from\_offset} + \text{start}_{\text{slice}}$$
  $$\text{end}_{\text{abs}} = \text{start\_offset\_seconds} + \text{start\_from\_offset} + \text{end}_{\text{slice}}$$

### E. gRPC Transport Client (`internal/transport/grpc/chunk_client.py`)
- Maps `AlignedTranscriptSegment` list to `document_ai_pb2.AudioTranscriptChunk` with repeated `TranscriptSegment` and `WordTimestamp`.
- Emits chunks to `StreamAudioTranscript` RPC stub.
- Validates response acknowledgment (`ASTStreamResponse.acknowledged == True`).
- Provides resilient offline fallback mode for testing identical to `stream_document_ast`.

### F. Temporal Activity (`internal/temporal/activities/audio_activity.py`)
- Physical audio container & header validation catching `wave.Error`, truncated payloads, zero-byte payloads, or invalid channels/rates, failing fast with non-retryable `ApplicationError("Corrupted audio window detected; routing to quarantine", type="CORRUPT_INPUT_QUARANTINE", non_retryable=True)`.
- S3 audio download using `s3_client.fetch_slice`.
- Inspection of `activity.info().heartbeat_details` for offset resumption (`start_from_offset`).
- `asyncio.to_thread` execution wrapping Whisper + PyAnnote + Alignment.
- Emits `AudioHeartbeatDetails` every 10 seconds via `HeartbeatRunner`.
- Streams transcript over gRPC via `chunk_client.stream_audio_transcript`.
- Updates Prometheus metrics with delta accounting:
  $$\Delta = \text{processed\_seconds} - \text{start\_from\_offset}$$
  `audio_seconds_processed_total.labels(worker_type="audio").inc(delta)`.
- Invokes `cleanup_vram_and_memory`.

---

## 4. Verification & Testing Strategy

1. **Unit Tests**:
   - `test_whisper_engine.py`: Test transcription, word timestamps, offset resumption, and cancellation.
   - `test_pyannote_engine.py`: Test speaker clustering, speaker bounds, HF_TOKEN support, and speaker count constraints.
   - `test_audio_alignment.py`: Test word-to-speaker matching, silence gap handling, consecutive word merging, and absolute timeline projection.
   - `test_audio_grpc_stream.py`: Test Protobuf conversion and gRPC streaming acknowledgment.
2. **Integration Tests**:
   - `test_audio_pipeline_integration.py`: End-to-end `TranscribeAudioWindowActivity` execution with mock S3 and gRPC clients.
   - `test_audio_checkpoint_recovery.py`: Checkpoint resumption test verifying recovery from mid-window offset without reprocessing earlier audio.
   - `test_audio_corrupt_quarantine.py`: Verifies physical WAV header corruption, zero-byte files, and invalid formats route to non-retryable quarantine.
3. **Performance Tests**:
   - `test_audio_rtf_perf.py`: Asserts Real-Time Factor (RTF $\le 0.15$) and streaming latency.
4. **Pre-Commit Checks**:
   - `python -m ruff check --fix .`
   - `python -m ruff format .`
   - `python -m mypy internal cmd test`
   - `python -m pytest test/unit test/integration test/performance`
