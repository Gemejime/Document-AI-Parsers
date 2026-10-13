# Architecture Context - RAG Document AI Worker

## System Stack & Specifications

| Layer | Technology | Role & Operational Characteristics |
| :--- | :--- | :--- |
| **Language Runtime** | Python 3.11+ / PyTorch 2.3+ | Compute runtime with GIL isolation via thread pools |
| **GPU Acceleration** | CUDA 12.2+ / Tensor Core (L4 / A10G 24GB) | Dedicated worker pod GPU allocations, strictly isolated |
| **Memory Allocator** | `jemalloc` (`LD_PRELOAD=/usr/lib/libjemalloc.so`) | Eliminates host/CPU heap fragmentation & stabilizes glibc memory |
| **Orchestration** | Temporal Python SDK (`temporalio`) | Activity workers with heartbeat checkpointing & retries |
| **Vision & OCR** | Docling Layout Engine + PaddleOCR Fallback | High-fidelity structural parsing & multi-pass OCR |
| **Table Extraction** | Table-Transformer + PyArrow / Polars | Table detection & serialization to columnar Parquet |
| **Audio Processing** | Whisper Large-v3 / Faster-Whisper + PyAnnote | Speech-to-text transcription & speaker diarization |
| **API & Probes** | FastAPI + Uvicorn (ASGI) | OpenAPI 3.1 endpoints (`/docs`, `/api/docs`), `/healthz`, `/ready` |
| **Data Egress & IPC** | gRPC over HTTP/2 (Protobuf v3) | Low-latency streaming of structured ASTs |
| **Storage Ingress/Egress**| AWS S3 via `aiobotocore` | S3 slice reads and content-addressed Parquet writes |
| **Autoscaling** | KEDA (Kubernetes Event-driven Autoscaling) | Queue-depth autoscaling based on Temporal activity lag |

---

## Production-Grade Layer Architecture (Aligned with Backend Concepts)

### Layer 1: API & Entry Points
- **Entry Points (`cmd/`)**: Separate bootstrapping entry points:
  - `cmd/vision_worker/main.py`: Bootstraps the `doc-ai-vision-worker` process.
  - `cmd/audio_worker/main.py`: Bootstraps the `doc-ai-audio-worker` process.
- **RPC Contracts (`api/proto/v1/`)**: Strongly typed Protobuf v3 interfaces:
  - `document_ai.proto`: Extracted AST, layout nodes, paragraph text, and S3 table URIs.
  - `common.proto`: Normalized unit coordinates $[0.0, 1.0]$ and provenance definitions.
- **Input Validation**: Strict validation of task parameters (S3 bucket, key patterns, page bounds) before processing starts.
- **Idempotency**: Activities are idempotent based on upstream segment hashes (`doc_hash` / `audio_hash`); retries safely overwrite or reuse deterministic S3 keys.

### Layer 2: Data Persistence & Storage Model
- **Storage Boundaries**:
  - Workers **never** hold permanent local database state; workers are completely stateless.
  - Ingress: Fetches discrete physical slice objects (`doc_part_*.pdf`, `audio_part_*.wav`) via scoped S3 credentials.
  - Egress: Direct writes of normalized Parquet files to content-addressed S3 paths:
    `s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet`
- **Memory Boundary Safety**: POSIX shared memory (`/dev/shm`) is mounted locally per pod (2 GiB `emptyDir`) to prevent container crashes, avoiding cross-pod memory sharing.

### Layer 3: Architecture & Scaling
- **Asynchronous Task Queues & Workers**:
  - `doc-ai-vision-queue`: Dedicated Temporal task queue for vision worker pods.
  - `doc-ai-audio-queue`: Dedicated Temporal task queue for audio worker pods.
- **Autoscaling via KEDA**:
  - Vision Worker: Scales on `doc-ai-vision-queue` depth (Target: 5 tasks/pod, Min: 2, Max: 20).
  - Audio Worker: Scales on `doc-ai-audio-queue` depth (Target: 3 tasks/pod, Min: 1, Max: 10).
- **Concurrency & Resource Limits**:
  - CPU: 4,000m request / 8,000m limit per pod.
  - Memory: 8 GiB request / 16 GiB limit per pod.
  - `OMP_NUM_THREADS=2` and `OPENBLAS_NUM_THREADS=2` to prevent CPU thread thrashing.

### Layer 4: Security & Identity
- **Credential Decoupling**: S3 and Temporal connection credentials are dynamically injected via Kubernetes secrets and IAM roles (IRSA / IAM roles for service accounts).
- **Input Quarantine**: Truncated or corrupt PDFs/audio codecs fail fast with non-retryable Temporal `ApplicationError` exceptions, sending the item to quarantine without crash loops.
- **Secured Logging**: Strictly redacts all PII, authorization tokens, presigned S3 URLs, and confidential document excerpts before logging.

### Layer 5: Observability, Quality & Probes
- **Health / Liveness / Readiness Probes**: HTTP `/healthz` and `/ready` endpoints ensure Kubernetes safely routes traffic only when worker loops and GPU caches are initialized.
- **Temporal Heartbeat Telemetry**: Active background heartbeats emitted every 10 seconds with current page/offset checkpoints.
- **Prometheus Telemetry Metrics**:
  - `doc_ai_pages_processed_total`: Pages processed by vision worker.
  - `doc_ai_audio_seconds_processed_total`: Seconds of audio transcribed.
  - `doc_ai_task_duration_seconds`: Histogram measuring execution duration per segment.
  - `doc_ai_vram_allocated_bytes`: Gauge tracking active GPU memory allocations.
  - `doc_ai_heartbeat_emissions_total`: Counter tracking successful Temporal heartbeats.
  - `doc_ai_heartbeat_delay_seconds`: Histogram/Gauge tracking delay between heartbeat intervals.
  - `doc_ai_cuda_oom_errors_total`: Counter tracking CUDA out-of-memory errors.

---

## Pragmatic Monorepo Directory Boundaries

```
rag-document-ai-worker/
├── cmd/
│   ├── vision_worker/main.py      # Entry point for doc-ai-vision-worker process
│   └── audio_worker/main.py       # Entry point for doc-ai-audio-worker process
├── api/
│   └── proto/v1/
│       ├── document_ai.proto      # Extracted AST, layout nodes, and S3 table URIs
│       └── common.proto           # Normalized coordinates and provenance definitions
├── internal/
│   ├── config/settings.py         # Queue names, VRAM thresholds, S3 buckets
│   ├── server/probe_server.py     # FastAPI ASGI app (/docs, /healthz, /ready, /metrics)
│   ├── vision/
│   │   ├── layout/docling_engine.py  # Standardized Docling parsing adapter (page streaming)
│   │   ├── ocr/fallback_ocr.py       # Multi-pass OCR for low-confidence regions
│   │   ├── table/table_extractor.py  # Table detection and bounding extraction
│   │   ├── table/parquet_s3_writer.py# Direct Parquet serialization to S3
│   │   └── coordinates.py            # Coordinate normalization to [0.0, 1.0] (top-left origin)
│   ├── audio/
│   │   ├── asr/whisper_engine.py     # Whisper transcription runner (chunk streaming)
│   │   └── diarization/pyannote_engine.py # Speaker clustering and boundary alignment
│   ├── temporal/
│   │   ├── activities/vision_activity.py  # Thread-isolated PDF parsing activity with page heartbeats
│   │   ├── activities/audio_activity.py   # Thread-isolated audio activity with offset heartbeats
│   │   └── common/heartbeat_runner.py     # Background heartbeat emission utility
│   ├── storage/s3_client.py       # S3 slice reader and Parquet writer
│   └── transport/grpc/chunk_client.py # Streaming client to rag-chunk-lifecycle-service
├── deployments/
│   ├── docker/Dockerfile.vision   # Vision runtime image (CUDA + Docling)
│   ├── docker/Dockerfile.audio    # Audio runtime image (CUDA + Whisper)
│   └── helm/                      # KEDA scaling & Helm charts
├── test/
│   ├── unit/                      # Coordinate math, AST protobuf serialization
│   ├── integration/               # Checkpoint resumption, mock S3 writes
│   └── performance/               # VRAM leak soak testing under jemalloc
├── pyproject.toml                 # Ruff, mypy, pytest, and dependencies
└── Makefile
```

---

## Architectural Invariants (Non-Negotiable)

1. **GIL Isolation & Progressive Progress Streaming**: Long-running PyTorch, Docling, and Whisper inference MUST run inside `asyncio.to_thread()` or dedicated executor pools without blocking the main event loop. Crucially, inference engines must yield progressive per-page or per-offset progress updates across the thread boundary (via generators or thread-safe progress states) so the background heartbeat runner continually updates checkpoint details (`activity.get_heartbeat_details()`) allowing seamless mid-slice resumption (e.g., resuming at page 51 after SIGKILL at page 52).
2. **Heartbeat Continuity & Tolerances**: Temporal heartbeats MUST emit at least once every 10 seconds with a configured `heartbeat_timeout` of 30 seconds to absorb GC/defragmentation jitter. Checkpoint details must contain the last processed page index or audio timestamp.
3. **Normalized Top-Left Coordinates**: All bounding box coordinates MUST be normalized float values between $0.0$ and $1.0$ anchored to the **top-left origin** $(0.0, 0.0)$, where $(x_1, y_1)$ is the top-left coordinate and $(x_2, y_2)$ is the bottom-right coordinate.
4. **Absolute Timeline Timestamps**: Audio segments emitted over gRPC MUST contain absolute media timeline offsets (calculated as `start_offset_seconds + local_offset`) so downstream consumers can assemble transcripts without manual offset reconstruction.
5. **Direct S3 Parquet Egress**: Tabular extractions must write directly to S3 as Parquet datasets under deterministic content-addressed keys (`s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet`). Raw table row dictionaries must never be transported via gRPC; the AST table node carries only the Markdown summary and S3 Parquet URI.
6. **No Shared Host Memory**: Containers must not mount host `/dev/shm` across pods. Use local pod-scoped `emptyDir` memory mounts (2 GiB).
7. **FastAPI Operational Server**: Each worker process hosts an ASGI FastAPI server (`internal/server/probe_server.py`) exposing OpenAPI 3.1 endpoints (`/docs`, `/redoc`, `/api/docs`, `/openapi.json`), health probes (`/healthz`, `/ready`), and Prometheus telemetry (`/metrics`).
