# Progress Tracker - RAG Document AI Worker

## Current Phase

- **Phase 2: Vision & Tabular Pipeline** (Completed)

## Current Goal

- Phase 3: Audio Pipeline & Speaker Diarization.

## Completed

- **Phase 2: Vision & Tabular Pipeline (Completed)**:
  - **Docling Layout Engine Adapter** (`internal/vision/layout/docling_engine.py`): Progressive page streaming across the thread boundary with yield-based iterator, cooperative cancellation via `threading.Event`, normalized bounding boxes in $[0.0, 1.0]$ with top-left origin convention, hierarchical heading tags (`heading_h1` .. `heading_h6` + markdown `#`), and mid-slice resumption skipping pages prior to `start_from_page`.
  - **Multi-Pass Fallback OCR Adapter** (`internal/vision/ocr/fallback_ocr.py`): Integrates PaddleOCR with automatic trigger when native text confidence falls below `settings.OCR_CONFIDENCE_THRESHOLD` (default 0.80) or when scanned image regions contain empty text streams, preserving reading order and unit bounding boxes.
  - **Table Structure Extraction Engine** (`internal/vision/table/table_extractor.py`): Extracts tabular datasets, produces clean GitHub-Flavored Markdown (GFM) summaries, computes deterministic SHA-256 canonical table hashes (`table_hash`), and converts tables into strongly-typed columnar PyArrow `Table` schemas (inferred boolean, int64, float64, string).
  - **Direct S3 Parquet Serializer** (`internal/vision/table/parquet_s3_writer.py`): Serializes tables directly to Snappy Parquet bytes and uploads to content-addressed paths `s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet` with mandatory S3 headers (`x-amz-meta-tenant-id`, `x-amz-meta-doc-hash`, `x-amz-meta-table-hash`, `x-amz-meta-page-number`, and `Content-Type: application/vnd.apache.parquet`).
  - **Streaming gRPC AST Client** (`internal/transport/grpc/chunk_client.py`): Client-streaming gRPC transport implementing `DocumentAIChunkService.StreamDocumentAST`, transporting Parquet S3 URIs in `ASTNode.table_parquet_s3_uri` without binary leaks (ADR-003) and meeting the $\le 1.5$s manifest egress SLO.
  - **VRAM & Memory Lifecycle Protocol** (`internal/vision/memory.py`): `cleanup_vram_and_memory()` flushing `torch.cuda.empty_cache()` and `gc.collect()` after each page/slice iteration to prevent GPU fragmentation and OOM errors.
  - **Temporal Activity Integration** (`internal/temporal/activities/vision_activity.py`): Rewired `ParseDocumentSliceActivity` connecting layout parsing, OCR enhancement, direct S3 Parquet uploads, and gRPC AST chunk streaming while preserving 10s heartbeats, checkpoint resumption, and fail-fast `CORRUPT_INPUT_QUARANTINE`.
  - **Comprehensive Test Suite** (`test/unit/`, `test/integration/`, `test/performance/`): 39 tests passing across coordinate normalization, layout streaming, fallback OCR, table hashing, Snappy Parquet serialization, S3 metadata, crash recovery at page 51, full pipeline integration, and $\le 1.5$s manifest egress SLO.

## Next Up

1. Phase 3: Audio Pipeline & Diarization (`internal/audio/`).
2. Faster-Whisper Large-v3 speech-to-text integration with word-level timestamps.
3. PyAnnote-Audio speaker diarization and absolute media timeline alignment.

- **Environment & Tooling**:
  - Installed and configured **Ruff** for linting, formatting, and import sorting in `pyproject.toml`.
  - Configured `mypy` strict typing settings and `pytest` paths.
  - Setup `.gitignore` excluding `tasks/`, binary checkpoints, Parquet slices, and virtual environments.
- **CI/CD Workflows**:
  - Created `.github/workflows/ci.yml` (linting, proto checking, tests, Helm lint, and Docker smoke build).
  - Created `.github/workflows/docker-publish.yml` (multi-image build & push for decoupled workers).
- **Architecture & AI Rules**:
  - Synchronized `.ai/` documentation with `tasks/RAG Document AI Worker Implementation Plan.pdf` and `Production Grade Backend Concepts.pdf`.
  - Created `.ai/doc-api.md` covering Temporal activities, S3 Parquet paths, and gRPC Protobuf contracts.
  - Defined Git Commit Standards and Secured Logging policies across context files.
  - Created `GEMINI.md` pairing agent instructions with repository context.
  - Conducted rigorous pressure test against all `tasks/` architectural specifications and resolved critical edge cases (thread progress streaming, heartbeat timeout buffer, fail-fast quarantine exceptions, top-left coordinate normalization, absolute audio timeline alignment, and removed missing `ui-context.md` reference).

- **Phase 1: Worker Decoupling & Runtimes (Completed)**:
  - **Core Settings & Secured Logging** (`internal/config/settings.py`): Pydantic `WorkerSettings` reading environment variables, model cache directories, structured JSON logging via `structlog`, and security filters redacting tokens, keys, presigned URLs, and document/transcript text.
  - **FastAPI Operational Probe Server** (`internal/server/probe_server.py`): Interactive OpenAPI 3.1 (`/docs`, `/redoc`), schema specs (`/api/docs`, `/openapi.json`), health probes (`/healthz`, `/ready` tied to worker readiness), and Prometheus metrics (`/metrics`).
  - **Coordinate Normalization Math** (`internal/vision/coordinates.py`): Normalized bounding boxes strictly bounded to $[0.0, 1.0]$ with top-left vs bottom-left origin adapters (handling PostScript/PDF coordinate flipping), clamping, and validation.
  - **Storage & Transport Protocols** (`internal/storage/s3_client.py`, `internal/transport/grpc/chunk_client.py`): Asynchronous client protocols and stubs for S3 slice reads/Parquet writes and gRPC AST streaming.
  - **Thread-Isolated Heartbeat Runner & Cooperative Cancellation** (`internal/temporal/common/heartbeat_runner.py`): `HeartbeatRunner` with `ProgressTracker`, non-blocking 10s interval emissions, latency telemetry, and `threading.Event` cooperative abort.
  - **Temporal Activities Framework** (`internal/temporal/activities/vision_activity.py`, `audio_activity.py`): `ParseDocumentSliceActivity` and `TranscribeAudioWindowActivity` supporting checkpoint resumption (recovering from previous page/offset) and non-retryable fail-fast quarantine (`CORRUPT_INPUT_QUARANTINE`).
  - **Worker Bootstrap Entry Points** (`cmd/vision_worker/main.py`, `cmd/audio_worker/main.py`): Co-hosting Temporal Worker and Uvicorn probe server concurrently in the main asyncio event loop via `asyncio.gather()`.
  - **Containerization & Helm Deployments** (`deployments/docker/Dockerfile.vision`, `Dockerfile.audio`, `deployments/helm/values-vision.yaml`, `values-audio.yaml`): Multi-stage CUDA 12.2 images with `libjemalloc2`, model cache env vars, non-root UID 1000, read-only rootfs, 2 GiB `/dev/shm`, and KEDA `ScaledObject` queue depth triggers.
  - **Comprehensive Verification Suite** (`test/unit/`, `test/integration/`): 24 unit and integration tests passing covering coordinate math, settings redaction, probe endpoints, GIL thread isolation, and Temporal checkpoint resumption.

## In Progress

- Phase 2: Vision & Tabular Pipeline Preparation & Implementation.

## Completed (Phase 2 Prep & Pressure Testing)

- **Comprehensive Architectural Pressure Testing**:
  - Pressure tested `prompts/phase-2-vision-tabular-pipeline.md` against all five architectural specifications in `tasks/`, specifically `tasks/RAG Document AI Worker Implementation Plan.pdf`.
  - Hardened specifications for Table-Transformer boundary detection, canonical SHA-256 table hash calculation, PaddleOCR fallback integration, VRAM cache flushing hooks (`torch.cuda.empty_cache()` + `gc.collect()`), heading hierarchy tagging (`heading_h1` .. `heading_h6` + markdown `#`), and client-streaming gRPC manifest egress ($\le 1.5$s SLO).
  - Updated `prompts/phase-2-vision-tabular-pipeline.md` with complete implementation plan.

## Next Up

1. Implement Docling layout parser with OCR fallback in `internal/vision/layout/docling_engine.py`.
2. Implement Table-Transformer tabular dataset extraction and direct S3 Parquet serializer in `internal/vision/table/`.
3. Implement unary/streaming gRPC AST client pushing to `rag-chunk-lifecycle-service`.

## Architecture Decisions

- **ADR-001**: Decoupled worker compute pools (`doc-ai-vision-worker` and `doc-ai-audio-worker`).
- **ADR-002**: Upstream pre-split physical document slicing to avoid byte-range PDF reads.
- **ADR-003**: Direct S3 Parquet serialization under content-addressed paths, deprecating Arrow Flight for this microservice.
- **ADR-004**: Thread-isolated activity execution (`asyncio.to_thread()`) to keep Python's GIL from blocking Temporal heartbeats.
- **ADR-005**: Vision engine standardization on Docling for initial rollout.
- **ADR-006**: OpenAPI 3.1 integration via FastAPI for internal HTTP probes, live interactive documentation (`/docs`, `/redoc`), and machine-readable schema (`/api/docs`, `/openapi.json`).
- **ADR-007**: Progressive thread-boundary progress streaming to ensure Temporal checkpoint details support mid-slice resumption (e.g. page 51 recovery).
- **ADR-008**: Fail-fast input quarantine via non-retryable `ApplicationError("CORRUPT_INPUT_QUARANTINE")` rather than return models.
- **ADR-009**: Standardized top-left origin for normalized bounding boxes $[0.0, 1.0]$ and absolute media timelines for audio segments.

## Session Notes

- Branch `rag-document-ai-worker` active and clean.
- Python 3.11+ compatibility target with Ruff 0.16.6 installed.
- Pressure test recommendations executed and specifications hardened against `tasks/` blueprints.
- Code review feedback addressed: standardized portable relative documentation paths across `.ai.md`, `GEMINI.md`, and `.ai/ai-workflow-rules.md`; clarified host-heap memory role of `jemalloc`; scoped container capabilities and read-only rootfs mounts in `.ai/security.md`; locked `/api/docs` to raw OpenAPI 3.1 schema; standardized pytest testpaths to `test/unit` and `test/integration`.
- Implemented and verified complete GitHub Actions CI/CD automation suite:
  - `.github/workflows/ci.yml`: Ruff lint/format checks, strict Mypy type validation, Pytest unit/integration test suite with coverage upload, Protobuf v1 syntax & schema validation, Helm values YAML linting, Gitleaks security scanning, and multi-stage Dockerfile smoke builds.
  - `.github/workflows/docker-publish.yml`: Multi-image matrix build and publish for `doc-ai-vision-worker` and `doc-ai-audio-worker` to GitHub Container Registry (`ghcr.io`) with Docker Buildx and GitHub Actions layer caching.
  - `Makefile`: Standardized automation entry points (`make lint`, `make format`, `make typecheck`, `make test`, `make proto`, `make check`).
  - `api/proto/v1/`: Implemented Protobuf v3 contracts (`common.proto` and `document_ai.proto`) for normalized bounding boxes and gRPC streaming to `rag-chunk-lifecycle-service`.
