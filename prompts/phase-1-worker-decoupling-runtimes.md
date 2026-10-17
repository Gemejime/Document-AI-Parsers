# Phase 1: Worker Decoupling & Runtimes - Implementation Plan & Prompt

## 1. Executive Summary & Objective

Phase 1 establishes the core runtime decoupling, containerization environments, and asynchronous Temporal safety primitives for the `rag-document-ai-worker` microservice, strictly adhering to:
- [`tasks/RAG Document AI Worker Implementation Plan.pdf`](tasks/RAG%20Document%20AI%20Worker%20Implementation%20Plan.pdf)
- [`tasks/Go and Python Microservices Architecture.pdf`](tasks/Go%20and%20Python%20Microservices%20Architecture.pdf)
- [`tasks/Production-Grade Multimodal RAG Architectural Specification.pdf`](tasks/Production-Grade%20Multimodal%20RAG%20Architectural%20Specification.pdf)
- [`tasks/Disaggregated Hybrid Model Architecture.pdf`](tasks/Disaggregated%20Hybrid%20Model%20Architecture.pdf)
- [`tasks/Production Grade Backend Concepts.pdf`](tasks/Production%20Grade%20Backend%20Concepts.pdf)
- [`GEMINI.md`](GEMINI.md) & [`.ai/architecture.md`](.ai/architecture.md)
- [`.ai/doc-api.md`](.ai/doc-api.md)
- [`.ai/security.md`](.ai/security.md)
- [`.ai/code-standards.md`](.ai/code-standards.md)

### Core Deliverables for Phase 1
1. **Container & Runtime Isolation**:
   - `deployments/docker/Dockerfile.vision`: CUDA 12.2+ runtime, Docling dependencies, `libjemalloc2` (with symlink `/usr/lib/libjemalloc.so`), non-root UID 1000, read-only cache mount `/models/cache/`, preconfigured cache variables (`HF_HOME`, `TORCH_HOME`, `DOCLING_CACHE_DIR`), allocator tuning (`PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128`), and thread limits (`OMP_NUM_THREADS=2`, `OPENBLAS_NUM_THREADS=2`).
   - `deployments/docker/Dockerfile.audio`: CUDA 12.2+ runtime, Faster-Whisper and PyAnnote dependencies, `libjemalloc2`, non-root UID 1000, read-only cache mount `/models/cache/`, preconfigured cache variables (`HF_HOME`, `TORCH_HOME`, `WHISPER_CACHE_DIR`), allocator tuning, and thread limits.
   - `deployments/helm/values-vision.yaml` & `deployments/helm/values-audio.yaml`: Pod sizing (L4/A10G 24GB GPUs, 8Gi/16Gi RAM, 4000m/8000m CPU), 2 GiB `/dev/shm` emptyDir memory mount, and KEDA `ScaledObject` triggers on `doc-ai-vision-queue` (Target: 5, Min: 2, Max: 20) and `doc-ai-audio-queue` (Target: 3, Min: 1, Max: 10).
2. **Temporal Worker Framework & GIL-Safe Concurrency**:
   - Background heartbeat runner (`internal/temporal/common/heartbeat_runner.py`) decoupling heavy PyTorch/C++ inference from the Temporal event loop via `asyncio.to_thread()`.
   - Heartbeat continuity emitting every 10 seconds (with 30-second timeout buffer) with progressive per-page and per-offset progress checkpoint details (`activity.get_heartbeat_details()`).
   - Cooperative cancellation using `threading.Event` (`stop_event`) passed into background worker threads to abort work immediately when an activity is cancelled or timed out.
   - Resumption support allowing interrupted activities to resume mid-slice without reprocessing (e.g., resuming from page 51 after termination during page 52).
   - Non-retryable fail-fast quarantine exceptions: `ApplicationError("...", type="CORRUPT_INPUT_QUARANTINE", non_retryable=True)`.
3. **Core Scaffolding & Shared Modules**:
   - `internal/config/settings.py`: Pydantic settings (`BaseSettings`) loading queue names, VRAM thresholds, S3 buckets, Temporal coordinates, model cache paths, and structured JSON logging configuration via `structlog`.
   - `internal/server/probe_server.py`: ASGI FastAPI server exposing interactive OpenAPI 3.1 documentation (`/docs`, `/redoc`), machine-readable specifications (`/api/docs`, `/openapi.json`), health probes (`/healthz`, `/ready`), and Prometheus telemetry (`/metrics`).
   - `internal/vision/coordinates.py`: Strict coordinate normalization to $[0.0, 1.0]$ with support for coordinate origin translation (`top-left` vs `bottom-left` for raw PDF points), bounds clamping, and validation.
   - `internal/storage/s3_client.py`: Abstract protocol and client stub for S3 slice retrieval and content-addressed Parquet writes.
   - `internal/transport/grpc/chunk_client.py`: Abstract protocol and client stub for streaming AST and transcript payloads to `rag-chunk-lifecycle-service`.
   - Activity stubs:
     - `internal/temporal/activities/vision_activity.py` (`ParseDocumentSliceActivity`)
     - `internal/temporal/activities/audio_activity.py` (`TranscribeAudioWindowActivity`)
   - Worker entry points:
     - `cmd/vision_worker/main.py`: Concurrently runs `temporalio.worker.Worker` and the Uvicorn ASGI probe server within the same asyncio event loop via `asyncio.gather()`, linking worker polling status to `/ready`.
     - `cmd/audio_worker/main.py`: Concurrently runs audio worker and probe server.

---

## 2. Monorepo File Structure & Additions

```
rag-document-ai-worker/
├── api/
│   └── proto/v1/
│       ├── common.proto                  # [Existing] Normalized BoundingBox message
│       └── document_ai.proto              # [Existing] ASTNode and gRPC streaming service
├── cmd/
│   ├── vision_worker/
│   │   ├── __init__.py
│   │   └── main.py                       # Bootstrap: Temporal Worker + ASGI Probe Server co-host
│   └── audio_worker/
│       ├── __init__.py
│       └── main.py                       # Bootstrap: Temporal Worker + ASGI Probe Server co-host
├── internal/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                   # Pydantic BaseSettings & structlog configuration
│   ├── server/
│   │   ├── __init__.py
│   │   └── probe_server.py               # FastAPI OpenAPI 3.1, health probes & Prometheus metrics
│   ├── vision/
│   │   ├── __init__.py
│   │   └── coordinates.py                # Coordinate normalization [0.0, 1.0] with origin adapter
│   ├── audio/
│   │   └── __init__.py
│   ├── storage/
│   │   ├── __init__.py
│   │   └── s3_client.py                  # S3 client protocol & stub for slices/tables
│   ├── transport/
│   │   ├── __init__.py
│   │   └── grpc/
│   │       ├── __init__.py
│   │       └── chunk_client.py           # gRPC client protocol & stub for AST streaming
│   ├── temporal/
│   │   ├── __init__.py
│   │   ├── common/
│   │   │   ├── __init__.py
│   │   │   └── heartbeat_runner.py       # Thread-isolated heartbeat loop with cooperative cancellation
│   │   └── activities/
│   │       ├── __init__.py
│   │       ├── vision_activity.py        # ParseDocumentSliceActivity scaffold
│   │       └── audio_activity.py         # TranscribeAudioWindowActivity scaffold
├── deployments/
│   ├── docker/
│   │   ├── Dockerfile.vision             # CUDA 12.2 + jemalloc + Docling deps + cache paths
│   │   └── Dockerfile.audio              # CUDA 12.2 + jemalloc + Whisper deps + cache paths
│   └── helm/
│       ├── values-vision.yaml            # L4/A10G sizing, /dev/shm emptyDir, KEDA autoscaling
│       └── values-audio.yaml             # L4/A10G sizing, /dev/shm emptyDir, KEDA autoscaling
└── test/
    ├── unit/
    │   ├── test_coordinates.py           # Unit tests for coordinate math, origins & bounds
    │   ├── test_settings.py              # Unit tests for Pydantic configuration & defaults
    │   ├── test_probe_server.py          # Unit tests for /healthz, /ready, /metrics, /api/docs
    │   └── test_heartbeat_runner.py      # Unit tests for heartbeat emission & cancellation
    └── integration/
        └── test_temporal_activities.py   # Integration tests for checkpoint resumption & quarantine
```

---

## 3. Detailed Component Designs

### 3.1. Settings & Configuration (`internal/config/settings.py`)
- Pydantic Settings model `WorkerSettings` reading from environment variables:
  - `ENV` / `ENVIRONMENT`: `development` | `staging` | `production`.
  - `TEMPORAL_HOST`: default `localhost:7233`.
  - `TEMPORAL_NAMESPACE`: default `default`.
  - `VISION_TASK_QUEUE`: `doc-ai-vision-queue`.
  - `AUDIO_TASK_QUEUE`: `doc-ai-audio-queue`.
  - `PROBE_PORT`: `8080`, `PROBE_HOST`: `0.0.0.0`.
  - `MODEL_CACHE_DIR`: `/models/cache`.
  - `HF_HOME`: `/models/cache/huggingface`.
  - `TORCH_HOME`: `/models/cache/torch`.
  - `HEARTBEAT_INTERVAL_SECONDS`: `10.0`.
  - `HEARTBEAT_TIMEOUT_SECONDS`: `30.0`.
  - `VRAM_THRESHOLD_BYTES`: `20 * 1024 * 1024 * 1024` (20 GiB).
  - `S3_ENDPOINT_URL`: Optional custom endpoint for MinIO/LocalStack in testing.
- Structured logging configuration via `structlog`:
  - JSON format in production, console renderer in development.
  - Contextual log enrichment: binds `tenant_id`, `document_id`, `task_queue`, `activity_id`, `page_number`.
  - Redaction filter masking tokens, Authorization headers, and presigned S3 query strings.
  - Invariant: Zero customer document text, OCR blocks, or speech transcripts logged.

### 3.2. Probe Server & OpenAPI 3.1 (`internal/server/probe_server.py`)
- FastAPI ASGI application configured with:
  - Title: `"RAG Document AI Worker API"`, Version: `"0.1.0"`.
  - `GET /docs`: Swagger UI.
  - `GET /redoc`: ReDoc UI.
  - `GET /api/docs` and `GET /openapi.json`: OpenAPI 3.1 JSON schema.
  - `GET /healthz`: Returns `{"status": "healthy"}`.
  - `GET /ready`: Returns 200 `{"status": "ready"}` when initialized and worker is active, or 503 during startup/disconnection.
  - `GET /metrics`: Standard Prometheus registry endpoint.
- Prometheus metrics registered:
  - `doc_ai_pages_processed_total` (Counter)
  - `doc_ai_audio_seconds_processed_total` (Counter)
  - `doc_ai_task_duration_seconds` (Histogram)
  - `doc_ai_vram_allocated_bytes` (Gauge)
  - `doc_ai_heartbeat_emissions_total` (Counter)
  - `doc_ai_heartbeat_delay_seconds` (Histogram)
  - `doc_ai_cuda_oom_errors_total` (Counter)

### 3.3. Thread-Safe Heartbeat Runner & Cooperative Cancellation (`internal/temporal/common/heartbeat_runner.py`)
- Asynchronous context manager / background task:
  - Runs in the main asyncio event loop while long-running inference runs in a worker thread via `asyncio.to_thread()`.
  - Maintains a thread-safe `ProgressTracker` updated by the background thread.
  - Exposes a `threading.Event` (`stop_event`) passed to worker functions to abort execution when an activity is cancelled or timed out.
  - Ticks every 10 seconds, calculating elapsed intervals and recording `doc_ai_heartbeat_delay_seconds`.
  - Invokes `activity.heartbeat(*details)` safely inside Temporal activity context.
  - Gracefully stops on activity completion or handles `temporalio.exceptions.ActivityCancelledError`.

### 3.4. Temporal Activity Framework & Resumption
- Models:
  - `ParseDocumentSliceInput` & `ParseDocumentSliceOutput`.
  - `TranscribeAudioWindowInput` & `TranscribeAudioWindowOutput`.
  - `VisionHeartbeatDetails` & `AudioHeartbeatDetails`.
- Checkpoint Recovery:
  - On activity start, `activity.get_heartbeat_details()` is inspected. If a previous worker was killed (e.g., at page 52), parsing resumes from page 51 without reprocessing earlier pages.
- Fail-Fast Input Quarantine:
  - Truncated PDF slices or malformed audio files trigger `ApplicationError("Corrupted input slice", type="CORRUPT_INPUT_QUARANTINE", non_retryable=True)` preventing retry loops.

### 3.5. Coordinate Normalization Math (`internal/vision/coordinates.py`)
- `normalize_bbox(x1: float, y1: float, x2: float, y2: float, width: float, height: float, origin: str = "top-left") -> tuple[float, float, float, float]`:
  - Validates positive width and height.
  - Converts arbitrary resolutions (72 DPI vector PDF vs 300 DPI OCR bitmap) to normalized floats $[0.0, 1.0]$.
  - Supports origin translation:
    - `"top-left"`: standard computer vision origin where $(0, 0)$ is top-left and $y$ increases downward.
    - `"bottom-left"`: standard PDF/PostScript origin where $(0, 0)$ is bottom-left and $y$ increases upward (flips Y axis: $y_{\text{norm}} = 1.0 - y / \text{height}$).
  - Enforces $x_1 \le x_2$ and $y_1 \le y_2$ with bounding box clamping to $[0.0, 1.0]$.

### 3.6. Worker Process Bootstrap (`cmd/vision_worker/main.py` & `cmd/audio_worker/main.py`)
- Single process co-hosting:
  - Initializes settings, structured logging, and Prometheus registry.
  - Connects to Temporal client (`temporalio.client.Client.connect(settings.TEMPORAL_HOST, namespace=settings.TEMPORAL_NAMESPACE)`).
  - Instantiates `temporalio.worker.Worker` with registered activities.
  - Configures `uvicorn.Server(uvicorn.Config(probe_app, host=settings.PROBE_HOST, port=settings.PROBE_PORT, access_log=False))`.
  - Executes both concurrently via `asyncio.gather(worker.run(), uvicorn_server.serve())`.
  - Updates probe server readiness state to `True` upon successful worker startup.
  - Handles `SIGTERM` and `SIGINT` signals for graceful teardown.

### 3.7. Container & Kubernetes Packaging (`deployments/`)
- Multi-stage Dockerfiles leveraging Ubuntu 22.04 + CUDA 12.2 runtime:
  - System dependencies: `libjemalloc2`, `poppler-utils` (vision), `libsndfile1`, `ffmpeg` (audio).
  - Canonical symlink: `ln -s /usr/lib/*-linux-gnu/libjemalloc.so.2 /usr/lib/libjemalloc.so`.
  - Environment variables:
    ```dockerfile
    ENV LD_PRELOAD=/usr/lib/libjemalloc.so \
        PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128 \
        OMP_NUM_THREADS=2 \
        OPENBLAS_NUM_THREADS=2 \
        HF_HOME=/models/cache/huggingface \
        TORCH_HOME=/models/cache/torch
    ```
  - Security: Non-root user (UID 1000), read-only rootfs (`readOnlyRootFilesystem: true`), scoped `/tmp` and 2 GiB `/dev/shm` mounts.
- Helm values (`values-vision.yaml`, `values-audio.yaml`):
  - Dedicated NVIDIA L4 / A10G 24GB GPU allocation.
  - CPU request: 4000m, CPU limit: 8000m.
  - Memory request: 8 GiB, Memory limit: 16 GiB.
  - EmptyDir memory mount for `/dev/shm` (2 GiB).
  - KEDA `ScaledObject` trigger definitions listening to Temporal queue depths (`doc-ai-vision-queue` target 5, `doc-ai-audio-queue` target 3).

---

## 4. Verification & Testing Strategy

1. **Unit Testing (`test/unit/`)**:
   - `test_coordinates.py`: Assert normalized coordinates lie within $[0.0, 1.0]$, verify top-left vs bottom-left origin conversion, zero-division guarding, and bounding box clamping.
   - `test_settings.py`: Verify Pydantic settings defaults, environment overrides, cache path configurations, and structured logging redaction filters.
   - `test_probe_server.py`: Verify FastAPI test client calls to `/healthz`, `/ready`, `/metrics`, `/docs`, `/redoc`, and `/api/docs`.
   - `test_heartbeat_runner.py`: Verify background runner emission intervals (10.0s), thread-safe progress updates, synthetic GIL-stress workloads running inside `asyncio.to_thread()`, and cooperative cancellation via `stop_event`.
2. **Integration Testing (`test/integration/`)**:
   - `test_temporal_activities.py`: Verify activity execution with mocked worker threads, heartbeat details emission, checkpoint resumption logic (resuming at page 51), and `CORRUPT_INPUT_QUARANTINE` non-retryable exception raising.
3. **Quality & Pre-Commit Gates**:
   - `python -m ruff check --fix .` (clean, 0 warnings/errors)
   - `python -m ruff format .` (formatting applied)
   - `python -m mypy cmd internal test` (strict static typing)
   - `python -m pytest test/unit test/integration` (all tests passing)
   - Sync `.ai/doc-api.md` and `.ai/progress-tracker.md`.
   - Ensure at least 2 git commits with conventional prefixes (`feat:`, `tests:`, etc.).

---

## 5. Implementation Execution Steps

Execution will proceed in strict order:
1. **Core Settings & Structured Logging**: Implement `internal/config/settings.py` with redaction filters and cache directories.
2. **FastAPI Probe & Documentation Server**: Implement `internal/server/probe_server.py` exposing OpenAPI 3.1 endpoints, health probes, and Prometheus metrics.
3. **Coordinate Normalization Math**: Implement `internal/vision/coordinates.py` with multi-origin and clamping support.
4. **Storage & Transport Scaffolding**: Implement client protocols in `internal/storage/s3_client.py` and `internal/transport/grpc/chunk_client.py`.
5. **Thread-Safe Heartbeat Runner & Cooperative Cancellation**: Implement `internal/temporal/common/heartbeat_runner.py`.
6. **Temporal Activities Scaffolding**: Implement `internal/temporal/activities/vision_activity.py` and `audio_activity.py` with checkpoint resumption and quarantine handling.
7. **Worker Bootstrap Entry Points**: Implement `cmd/vision_worker/main.py` and `cmd/audio_worker/main.py` co-hosting worker and probe server.
8. **Containerization & Deployment Specs**: Create `deployments/docker/Dockerfile.vision`, `Dockerfile.audio`, and Helm values files.
9. **Unit & Integration Test Suite**: Implement comprehensive tests across `test/unit/` and `test/integration/`.
10. **Pre-Commit Verification & Synchronization**: Run Ruff, Mypy, and Pytest; synchronize `.ai/doc-api.md` and `.ai/progress-tracker.md`; commit with conventional commit messages.
