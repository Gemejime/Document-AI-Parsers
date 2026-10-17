# RAG Document AI Worker - Project Overview

## Overview

The `rag-document-ai-worker` is a horizontally scalable, stateless compute microservice decoupled into two independent worker pools:
1. **`doc-ai-vision-worker`**: High-fidelity PDF layout parsing (Docling), multi-pass fallback OCR (PaddleOCR), tabular dataset extraction (Table-Transformer), direct Parquet S3 serialization, and bounding coordinate normalization.
2. **`doc-ai-audio-worker`**: Speech-to-text transcription (Whisper Large-v3 / Faster-Whisper) and speaker diarization (PyAnnote-Audio) with word-level timestamp alignment.

Both pools operate as asynchronous Temporal Python activity workers pulling from dedicated task queues, persisting large tabular artifacts directly to S3 under content-addressed keys, and streaming structured Abstract Syntax Tree (AST) manifests to `rag-chunk-lifecycle-service` via gRPC HTTP/2 streams.

## Goals

1. **Decoupled Compute & Zero VRAM Contention**: Completely separate vision/document parsing from audio transcription into distinct container environments and GPU allocations (NVIDIA L4/A10G with 24GB VRAM) to prevent OOM errors and package conflicts.
2. **GIL-Safe Non-Blocking Activity Heartbeats**: Wrap heavy PyTorch and C++ model execution inside `asyncio.to_thread()`, emitting Temporal heartbeats every 10 seconds without latency degradation.
3. **Deterministic Physical Slices**: Ingest upstream pre-split physical document segments (`doc_part_*.pdf` and `audio_part_*.wav`), eliminating unstable byte-range PDF reads.
4. **Direct Parquet S3 Egress**: Serialize tabular structures directly to S3 as Parquet datasets (`s3://.../tables/{hash}.parquet`), passing only S3 URIs in AST gRPC payloads (deprecating Apache Arrow Flight for this worker).
5. **Normalized Unit Coordinates**: Enforce normalized coordinate bounds ($0.0 \le x_1, y_1, x_2, y_2 \le 1.0$) across varying DPI resolutions (72 DPI vector points vs. 300 DPI OCR scans) for stable provenance.

## Core Processing Flows

### Vision Processing Flow (`doc-ai-vision-worker`)
1. **Temporal Dispatch**: Worker polls `doc-ai-vision-queue` for physical PDF slice metadata.
2. **S3 Ingress**: Asynchronously streams `doc_part_*.pdf` from S3 to local temporary scratch storage.
3. **Isolated Inference (`asyncio.to_thread()`)**:
   - Executes Docling structural layout parsing.
   - Triggers fallback multi-pass OCR on low-confidence native text spans.
   - Extracts tables via Table-Transformer.
4. **Tabular Egress**: Serializes extracted tables to Parquet and uploads directly to S3 with content-addressed hash keys via `aiobotocore`.
5. **AST Streaming Egress**: Streams normalized AST nodes, bounding boxes, text blocks, and Parquet S3 URIs to `rag-chunk-lifecycle-service` via gRPC.
6. **Temporal Completion**: Emits final heartbeat and completes activity.

### Audio Processing Flow (`doc-ai-audio-worker`)
1. **Temporal Dispatch**: Worker polls `doc-ai-audio-queue` for audio window tasks.
2. **S3 Ingress**: Asynchronously downloads `audio_part_*.wav` (10–15 minute sliding window).
3. **Isolated Inference (`asyncio.to_thread()`)**:
   - Transcribes audio via Faster-Whisper / Whisper Large-v3.
   - Executes speaker diarization via PyAnnote-Audio.
   - Generates word-level timestamp alignment and speaker turn boundaries.
4. **AST Streaming Egress**: Streams timestamped AST nodes and transcript envelopes to `rag-chunk-lifecycle-service` via gRPC.
5. **Temporal Completion**: Emits final heartbeat and completes activity.

## Scope

### In Scope
- Monorepo containing decoupled workers: `doc-ai-vision-worker` and `doc-ai-audio-worker`.
- Temporal Python activity definitions and background heartbeat runners (`asyncio.to_thread()`).
- Docling layout parser adapter with PaddleOCR fallback.
- Table-Transformer boundary detection with direct S3 Parquet serialization.
- Whisper ASR and PyAnnote speaker diarization pipelines.
- Unit coordinate normalization engine ($[0.0, 1.0]$).
- Protobuf v3 contracts (`document_ai.proto`, `common.proto`) and gRPC client for AST streaming.
- Dockerfiles (`Dockerfile.vision`, `Dockerfile.audio`) with CUDA 12.2+, PyTorch 2.3+, and `jemalloc`.
- Helm charts with KEDA task-queue depth autoscaling.

### Out of Scope
- Monolithic PDF physical slicing (handled upstream by `rag-ingestion-coordinator` using `qpdf`/`pypdf`).
- Monolithic media splitting into 10–15m windows (handled upstream by `ffmpeg` inspection activities).
- Apache Arrow Flight server hosting (deprecated in favor of direct S3 Parquet writes).
- Dual vision engine routing (MinerU runtime deferred to control container size and schema stability).
- Parent-child chunk grouping and token bounding (owned downstream by `rag-chunk-lifecycle-service`).
- Dense vector generation (handled downstream by Triton `rag-inference-serving`).

## Calibrated Success Criteria (SLIs & SLOs)

- **Vision Throughput**: $\le 2.0$ seconds per page on dual-column visual layouts on NVIDIA L4/A10G.
- **ASR Real-Time Factor (RTF)**: $\text{RTF} \le 0.15$ on dedicated audio GPU workers.
- **Process Stability**: Zero CUDA OOM errors across 2,000 consecutive segment runs under `jemalloc`.
- **Heartbeat Continuity**: 100% of Temporal activity heartbeats emitted within the 10-second target window.
- **AST Manifest Egress**: Complete gRPC streaming of a 50–100 page document manifest within $\le 1.5$ seconds.
