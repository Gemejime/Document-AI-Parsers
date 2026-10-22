# rag-document-ai-worker

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Temporal.io](https://img.shields.io/badge/temporal-orchestration-black.svg)](https://temporal.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-OpenAPI%203.1-009688.svg)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.3+-ee4c2c.svg)](https://pytorch.org/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Type Checked: mypy](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)

Horizontally scalable, stateless multimodal document and audio AI parser worker microservice for enterprise Retrieval-Augmented Generation (RAG) ingestion pipelines.

---

## Table of Contents

- [Overview](#overview)
- [Decoupled Compute Worker Pools](#decoupled-compute-worker-pools)
- [System Architecture](#system-architecture)
- [Environment Configuration](#environment-configuration)
- [HTTP Documentation, Health & Telemetry API](#http-documentation-health--telemetry-api)
- [Model Weights & Caching Strategy](#model-weights--caching-strategy)
- [Security & Secured Structured Logging](#security--secured-structured-logging)
- [Local Development Setup](#local-development-setup)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Running Workers Locally](#running-workers-locally)
  - [Code Quality & Testing](#code-quality--testing)
- [Containerization & Kubernetes Deployment](#containerization--kubernetes-deployment)
  - [Docker Builds](#docker-builds)
  - [Helm 3 Deployment](#helm-3-deployment)
  - [GitOps with ArgoCD](#gitops-with-argocd)

---

## Overview

`rag-document-ai-worker` is a decoupled compute worker microservice built on **Temporal.io**, **FastAPI**, **PyTorch**, **Docling**, **PaddleOCR**, and **Faster-Whisper**. It handles the compute-intensive, GPU-accelerated stages of document and multimedia processing:

1. **Document Slicing & Layout Analysis**: Ingests multi-page PDF slices, analyzes complex document layout hierarchies, falls back to OCR on low-confidence spans, and extracts structured tables into columnar Parquet format.
2. **Audio Transcription & Speaker Diarization**: Transcribes progressive audio chunks with word-level timestamps using Faster-Whisper, clusters speaker identities with PyAnnote 3.1, and projects all timestamps onto the global media timeline.
3. **Stateless Resiliency & Streaming**: Emits active Temporal activity heartbeats with fine-grained page/offset checkpoints to survive pod preemption, and streams AST manifests and transcript chunks to downstream services via gRPC.

---

## Decoupled Compute Worker Pools

To eliminate VRAM contention, memory leaks, and dependency conflicts between CUDA vision kernels and audio speech models, the workers run as two completely independent compute pools:

| Worker Pool | Container Image / Target | Task Queue | Primary Workload & Libraries |
| :--- | :--- | :--- | :--- |
| **Vision Worker** | `doc-ai-vision-worker` (`cmd/vision_worker`) | `doc-ai-vision-queue` | PDF slice fetching, Docling layout analysis, PaddleOCR secondary pass, PyArrow Parquet table export. |
| **Audio Worker** | `doc-ai-audio-worker` (`cmd/audio_worker`) | `doc-ai-audio-queue` | RIFF audio validation, Faster-Whisper large-v3 ASR, PyAnnote 3.1 diarization, absolute timeline alignment, gRPC streaming. |

---

## System Architecture

```text
                               +----------------------------------------+
                               |     Temporal Orchestrator Cluster      |
                               +----------------------------------------+
                                         |                    |
                 doc-ai-vision-queue     |                    |    doc-ai-audio-queue
                                         v                    v
                      +----------------------+    +----------------------+
                      | doc-ai-vision-worker |    | doc-ai-audio-worker  |
                      |  (GPU / CUDA 12.2)   |    |  (GPU / CUDA 12.2)   |
                      +----------------------+    +----------------------+
                             |            |              |            |
                             | S3 Get     | S3 Put       | S3 Get     | gRPC Stream
                             v            v              v            v
                      +------------+  +---------+  +------------+  +-------------------+
                      | S3 Storage |  | Parquet |  | S3 Storage |  | rag-chunk-service |
                      | (PDF Slice)|  | Tables  |  | (Audio WAV)|  | (Lifecycle gRPC)  |
                      +------------+  +---------+  +------------+  +-------------------+
```

Each worker runs:
- **Temporal Worker Loop**: Listens to its assigned task queue, offloads synchronous inference to worker threads via `asyncio.to_thread()`, and emits heartbeats every 10 seconds.
- **FastAPI ASGI Server (port 8080)**: Serves OpenAPI 3.1 documentation (`/docs`, `/redoc`), Kubernetes liveness/readiness probes (`/healthz`, `/ready`), and Prometheus telemetry (`/metrics`).

---

## Environment Configuration

The application loads runtime configuration from environment variables via **Pydantic Settings** (`WorkerSettings`). All variables come with production-ready built-in defaults:

### Key Environment Variables

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| **`ENVIRONMENT`** | `development` | Deployment environment: `development`, `staging`, or `production`. Controls log formatting. |
| **`TEMPORAL_HOST`** | `localhost:7233` | Host and port for the Temporal frontend cluster service. |
| **`TEMPORAL_NAMESPACE`** | `default` | Temporal namespace for workflow and activity execution. |
| **`VISION_TASK_QUEUE`** | `doc-ai-vision-queue` | Task queue for vision and document layout activities. |
| **`AUDIO_TASK_QUEUE`** | `doc-ai-audio-queue` | Task queue for audio transcription and diarization activities. |
| **`PROBE_HOST`** | `0.0.0.0` | Bind interface for the FastAPI documentation and probe server. |
| **`PROBE_PORT`** | `8080` | Port for the FastAPI probe server. |
| **`MODEL_CACHE_DIR`** | `/models/cache` | Base directory for pre-baked model weights. |
| **`HF_HOME`** | `/models/cache/huggingface` | Hugging Face cache directory for transformer weights and PyAnnote models. |
| **`TORCH_HOME`** | `/models/cache/torch` | PyTorch cache directory. |
| **`DOCLING_CACHE_DIR`** | `/models/cache/docling` | Docling layout model artifacts cache. |
| **`WHISPER_CACHE_DIR`** | `/models/cache/whisper` | Faster-Whisper / CTranslate2 model cache. |
| **`HF_TOKEN`** | *None* | Hugging Face user access token (required for gated `pyannote/speaker-diarization-3.1`). |
| **`HEARTBEAT_INTERVAL_SECONDS`**| `10.0` | Interval in seconds between activity heartbeat checkpoints. |
| **`HEARTBEAT_TIMEOUT_SECONDS`** | `30.0` | Heartbeat timeout buffer against GC or GPU batch jitter. |
| **`OCR_CONFIDENCE_THRESHOLD`** | `0.80` | Confidence floor below which secondary PaddleOCR fallback is triggered. |
| **`TABLE_PARQUET_COMPRESSION`** | `snappy` | Codec for Parquet table exports (`snappy`, `zstd`, `gzip`). |
| **`S3_ENDPOINT_URL`** | *None* | Optional custom S3 endpoint URL (e.g. `http://localhost:9000` for MinIO). |
| **`AWS_REGION`** | `us-east-1` | AWS region for S3 operations. |
| **`AWS_ACCESS_KEY_ID`** | *None* | Optional AWS access key ID for S3 operations. |
| **`AWS_SECRET_ACCESS_KEY`** | *None* | Optional AWS secret access key for S3 operations. |
| **`WHISPER_MODEL_SIZE`** | `large-v3` | Model size variant: `tiny`, `base`, `small`, `medium`, `large-v3`. |
| **`WHISPER_DEVICE`** | `cuda` | Inference device: `cuda` or `cpu`. |
| **`WHISPER_COMPUTE_TYPE`** | `float16` | Precision / quantization: `float16`, `int8_float16`, `float32`. |
| **`WHISPER_BEAM_SIZE`** | `5` | Beam search width for Whisper decoding. |
| **`PYANNOTE_MODEL_ID`** | `pyannote/speaker-diarization-3.1` | Hugging Face model repository ID for PyAnnote speaker diarization. |
| **`AUDIO_SAMPLE_RATE`** | `16000` | Native audio sample rate in Hz for Whisper and PyAnnote. |
| **`AUDIO_PROGRESS_STEP_SECONDS`** | `30.0` | Progressive window interval chunk size in seconds. |
| **`CHUNK_SERVICE_ENDPOINT`** | `rag-chunk-lifecycle-service:50051` | Downstream gRPC chunk lifecycle endpoint (`mock` for offline testing). |
| **`VRAM_THRESHOLD_BYTES`** | `21474836480` | GPU VRAM threshold ceiling (20 GiB). |

---

## HTTP Documentation, Health & Telemetry API

Each worker exposes an internal HTTP server (default port `8080`) providing live OpenAPI documentation, Kubernetes health probes, and Prometheus metrics:

- **Swagger UI**: [`http://localhost:8080/docs`](http://localhost:8080/docs)
- **ReDoc UI**: [`http://localhost:8080/redoc`](http://localhost:8080/redoc)
- **OpenAPI 3.1 JSON Schema**: [`http://localhost:8080/api/docs`](http://localhost:8080/api/docs) or `/openapi.json`
- **Liveness Probe**: `GET /healthz` (returns `{"status": "healthy"}`)
- **Readiness Probe**: `GET /ready` (verifies Temporal connection and model cache availability)
- **Prometheus Metrics**: `GET /metrics`

### Monitored Prometheus Metrics

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `doc_ai_pages_processed_total` | Counter | Total document PDF pages processed by vision workers. |
| `doc_ai_vision_tables_extracted_total` | Counter | Total tabular elements extracted and written to Parquet. |
| `doc_ai_ocr_fallbacks_total` | Counter | Total low-confidence text blocks requiring secondary OCR pass. |
| `doc_ai_audio_seconds_transcribed_total` | Counter | Cumulative audio duration (in seconds) transcribed. |
| `doc_ai_diarization_speakers_detected` | Histogram | Distribution of distinct speaker clusters identified per window. |
| `doc_ai_task_duration_seconds` | Histogram | End-to-end execution latency for Temporal activities. |
| `doc_ai_vram_usage_bytes` | Gauge | Active CUDA VRAM allocation tracked across worker execution. |
| `doc_ai_activity_failures_total` | Counter | Activity execution errors labeled by failure reason. |

---

## Model Weights & Caching Strategy

In production Kubernetes clusters, model weights are mounted as a **read-only NVMe PersistentVolumeClaim** at `/models/cache` to prevent redundant internet downloads across autoscaling pods:

- Hugging Face weights (`HF_HOME`): `/models/cache/huggingface`
- PyTorch hub (`TORCH_HOME`): `/models/cache/torch`
- Docling models (`DOCLING_CACHE_DIR`): `/models/cache/docling`
- Faster-Whisper models (`WHISPER_CACHE_DIR`): `/models/cache/whisper`

For PyAnnote speaker diarization, supply a valid Hugging Face access token via `HF_TOKEN` after accepting user agreements on the Hugging Face model repository ([`pyannote/speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1)).

---

## Security & Secured Structured Logging

1. **Non-Root Execution**: Worker containers run under non-root UID `1000` with dropped capabilities (`capabilities: drop: ["ALL"]`) and read-only root filesystems (`readOnlyRootFilesystem: true`). Ephemeral writes are strictly restricted to `/tmp` and `/dev/shm`.
2. **Zero PII / Document Content Leakage**: All logs are processed through `secured_logging_filter` using `structlog`. Customer document text, OCR tokens, speech transcripts, presigned URLs, and authentication credentials are systematically scrubbed and replaced with `[REDACTED]`.
3. **Structured JSON Logs**: Logs output single-line JSON with correlation fields: `tenant_id`, `document_id`, `task_queue`, `activity_id`, `page_number`.

---

## Local Development Setup

### Prerequisites

- **Python**: 3.11+
- **System Utilities**:
  - `ffmpeg` & `libsndfile` (for audio ingestion)
  - `poppler-utils` & `tesseract-ocr` (for PDF rendering & OCR)

### Installation

```bash
# Clone repository
git clone https://github.com/organization/rag-document-ai-worker.git
cd rag-document-ai-worker

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# Install base dependencies and dev tools
pip install -e ".[dev]"

# Optionally install model dependencies (GPU / inference)
pip install -e ".[vision]"   # For Docling, PaddleOCR, PyTorch Vision
pip install -e ".[audio]"    # For Faster-Whisper, PyAnnote, Torchaudio
```

### Running Workers Locally

Workers run directly with default environment settings, or with custom variables exported in your shell:

Start the **Vision Worker**:
```bash
python -m cmd.vision_worker.main
```

Start the **Audio Worker**:
```bash
python -m cmd.audio_worker.main
```

### Code Quality & Testing

The repository uses **Ruff** for linting, formatting, and import sorting, **Mypy** for static typing, and **Pytest** for testing:

```bash
# Lint check and format check
python -m ruff check .
python -m ruff format --check .

# Automatically apply lint fixes and code formatting
python -m ruff check --fix .
python -m ruff format .

# Static type analysis
python -m mypy cmd internal test

# Run unit and integration tests
python -m pytest test/unit test/integration

# Run chaos and performance benchmarks
python -m pytest test/chaos test/performance
```

You can also use the included `Makefile`:
```bash
make check   # Runs lint, typecheck, and unit/integration tests
```

---

## Containerization & Kubernetes Deployment

### Docker Builds

Multi-stage CUDA 12.2 images with `libjemalloc2` memory allocator:

```bash
# Build Vision Worker Image
docker build -f deployments/docker/Dockerfile.vision -t rag-document-ai-vision-worker:0.1.0 .

# Build Audio Worker Image
docker build -f deployments/docker/Dockerfile.audio -t rag-document-ai-audio-worker:0.1.0 .
```

### Helm 3 Deployment

Dedicated Helm 3 charts configure independent Deployments, KEDA `ScaledObject` autoscalers, and Prometheus Operator `ServiceMonitor` resources:

```bash
# Deploy doc-ai-vision-worker
helm upgrade --install doc-ai-vision deployments/helm \
  -n doc-ai --create-namespace \
  -f deployments/helm/values-vision.yaml

# Deploy doc-ai-audio-worker
helm upgrade --install doc-ai-audio deployments/helm \
  -n doc-ai --create-namespace \
  -f deployments/helm/values-audio.yaml
```

### GitOps with ArgoCD

ArgoCD manifests and ApplicationSets are located in `deployments/argocd/`:
- `application-vision.yaml`: Declarative vision worker deployment with KEDA replica drift immunity.
- `application-audio.yaml`: Declarative audio worker deployment.
- `applicationset.yaml`: Multi-cluster matrix generator for staging and production environments.
