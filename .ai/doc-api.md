# Microservice Documented API ("Doc API")

> **MANDATORY INVARIANT**: This document specifies both the internal system contracts and the **HTTP Doc API & OpenAPI Endpoint** exposed by the microservice.
> **API ACCESSIBILITY REQUIREMENT**: This documentation specification MUST be accessible directly via HTTP endpoints hosted on the worker's internal server (port `8080`):
> - `GET /docs` — Interactive OpenAPI 3.1 Documentation via **Swagger UI**.
> - `GET /redoc` — Interactive OpenAPI 3.1 Documentation via **ReDoc**.
> - `GET /openapi.json` & `GET /api/docs` — Machine-readable **OpenAPI 3.1 JSON Specification**.
> This file and the corresponding API endpoint handler MUST be updated on **every code change or addition**, regardless of how small the modification is.

---

## 1. Documentation & Probe API Endpoints (OpenAPI 3.1)

Every worker pod (`doc-ai-vision-worker` and `doc-ai-audio-worker`) runs an internal FastAPI ASGI server (default port `8080`) that serves:

| Endpoint | Method | Response Format | Description |
| :--- | :--- | :--- | :--- |
| `/docs` | `GET` | `text/html` | Interactive Swagger UI powered by OpenAPI 3.1 |
| `/redoc` | `GET` | `text/html` | Interactive ReDoc UI documentation |
| `/api/docs` | `GET` | `application/json` | Raw OpenAPI 3.1 JSON specification schema |
| `/openapi.json` | `GET` | `application/json` | Standard machine-readable OpenAPI 3.1 JSON specification |
| `/healthz` | `GET` | `application/json` | Liveness probe returning `{"status": "healthy"}` |
| `/ready` | `GET` | `application/json` | Readiness probe verifying GPU caches & Temporal queue connectivity |
| `/metrics` | `GET` | `text/plain` | Prometheus metrics scrape endpoint |

### OpenAPI Root Info Schema
```json
{
  "openapi": "3.1.0",
  "info": {
    "title": "RAG Document AI Worker API",
    "description": "Internal HTTP Documentation, Health, and Operational API for rag-document-ai-worker",
    "version": "0.1.0"
  },
  "paths": {
    "/healthz": {
      "get": {
        "summary": "Liveness Probe",
        "responses": {
          "200": { "description": "Worker event loop is healthy" }
        }
      }
    },
    "/ready": {
      "get": {
        "summary": "Readiness Probe",
        "responses": {
          "200": { "description": "Worker connected to Temporal and model caches initialized" },
          "503": { "description": "Worker initializing or connection severed" }
        }
      }
    },
    "/api/docs": {
      "get": {
        "summary": "OpenAPI Documentation Schema",
        "responses": {
          "200": { "description": "OpenAPI 3.1 JSON specification" }
        }
      }
    }
  }
}
```

---

## 2. Temporal Activity Interfaces & Task Queues

### A. Vision Task Queue: `doc-ai-vision-queue`

#### Activity: `ParseDocumentSliceActivity`
- **Module**: `internal.temporal.activities.vision_activity`
- **Queue**: `doc-ai-vision-queue`
- **Heartbeat Timeout**: `30s` (Emits heartbeat every `10s`, allowing a 3x buffer against GC/memory jitter)
- **Execution Mode**: Runs model inference in dedicated worker thread via `asyncio.to_thread()`. The vision engine streams progress page-by-page across the thread boundary to keep heartbeat checkpoints fresh.
- **Input Payload**:
  ```python
  class ParseDocumentSliceInput(BaseModel):
      tenant_id: str
      document_id: str
      doc_hash: str
      s3_bucket: str
      s3_key: str  # e.g., "slices/{tenant_id}/{doc_hash}/doc_part_02.pdf"
      start_page: int  # 0-indexed start page
      page_count: int  # Number of pages in this slice
  ```
- **Checkpoint Details (Heartbeat)**:
  ```python
  class VisionHeartbeatDetails(BaseModel):
      current_page: int
      pages_completed: int
      total_pages: int
      last_checkpoint_timestamp_utc: str
  ```
- **Output Result**:
  ```python
  class ParseDocumentSliceOutput(BaseModel):
      pages_processed: int
      table_count: int
      parquet_s3_uris: list[str]  # S3 URIs of extracted tables
      ast_streamed: bool
      duration_seconds: float
  ```
- **Error Semantics & Quarantine**:
  - Truncated, encrypted, or unparseable PDF slices fail immediately by throwing a non-retryable Temporal exception:
    `temporalio.exceptions.ApplicationError("Corrupted PDF slice", type="CORRUPT_INPUT_QUARANTINE", non_retryable=True)`
  - The workflow coordinator catches `CORRUPT_INPUT_QUARANTINE` and routes the document to quarantine without wasteful retry storms.

---

### B. Audio Task Queue: `doc-ai-audio-queue`

#### Activity: `TranscribeAudioWindowActivity`
- **Module**: `internal.temporal.activities.audio_activity`
- **Queue**: `doc-ai-audio-queue`
- **Heartbeat Timeout**: `30s` (Emits heartbeat every `10s`)
- **Execution Mode**: Runs inference in worker thread via `asyncio.to_thread()`, streaming segment progress across the thread boundary.
- **Input Payload**:
  ```python
  class TranscribeAudioWindowInput(BaseModel):
      tenant_id: str
      audio_id: str
      audio_hash: str
      s3_bucket: str
      s3_key: str  # e.g., "audio/{tenant_id}/{audio_hash}/audio_part_01.wav"
      start_offset_seconds: float
      window_duration_seconds: float
  ```
- **Checkpoint Details (Heartbeat)**:
  ```python
  class AudioHeartbeatDetails(BaseModel):
      current_offset_seconds: float
      processed_seconds: float
      total_seconds: float
  ```
- **Output Result**:
  ```python
  class TranscribeAudioWindowOutput(BaseModel):
      audio_seconds_processed: float
      segment_count: int
      speaker_count: int
      ast_streamed: bool
      duration_seconds: float
  ```
- **Error Semantics & Quarantine**:
  - Corrupt audio payloads or unsupported codecs fail immediately by throwing:
    `temporalio.exceptions.ApplicationError("Corrupted audio window", type="CORRUPT_INPUT_QUARANTINE", non_retryable=True)`

---

## 3. Storage Egress API (AWS S3)

### Direct Tabular Parquet Serialization
- **Writer**: `internal.vision.table.parquet_s3_writer`
- **Transport**: `aiobotocore` asynchronous streaming multipart upload.
- **Key Pattern**:
  ```
  s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet
  ```
- **Metadata Headers**:
  - `x-amz-meta-tenant-id`: `<tenant_id>`
  - `x-amz-meta-doc-hash`: `<doc_hash>`
  - `x-amz-meta-table-hash`: `<table_hash>`
  - `x-amz-meta-page-number`: `<page_number>`
  - `Content-Type`: `application/vnd.apache.parquet`

---

## 4. Streaming IPC Egress API (gRPC / Protobuf v3)

### Target Service: `rag-chunk-lifecycle-service`
- **Client**: `internal.transport.grpc.chunk_client`
- **Protocol**: HTTP/2 streaming gRPC using Protobuf v3 contracts (`api/proto/v1/`).

#### Service Definition (`document_ai.proto`)
```protobuf
syntax = "proto3";

package rag.document_ai.v1;

import "api/proto/v1/common.proto";

service DocumentAIChunkService {
  rpc StreamDocumentAST(stream DocumentASTChunk) returns (ASTStreamResponse);
  rpc StreamAudioTranscript(stream AudioTranscriptChunk) returns (ASTStreamResponse);
}

message DocumentASTChunk {
  string tenant_id = 1;
  string document_id = 2;
  int32 page_number = 3;
  repeated ASTNode nodes = 4;
}

message ASTNode {
  string node_id = 1;
  string node_type = 2;         // "paragraph", "heading", "table", "list", etc.
  string text_content = 3;      // Text content or Markdown summary when node_type == "table"
  rag.common.v1.BoundingBox bounding_box = 4;
  string table_parquet_s3_uri = 5; // Populated only if node_type == "table"
}

message AudioTranscriptChunk {
  string tenant_id = 1;
  string audio_id = 2;
  repeated TranscriptSegment segments = 3;
}

message TranscriptSegment {
  string speaker_id = 1;
  double start_time_seconds = 2; // Absolute timeline timestamp (start_offset_seconds + window_offset)
  double end_time_seconds = 3;   // Absolute timeline timestamp
  string text = 4;
  repeated WordTimestamp words = 5;
}

message WordTimestamp {
  string word = 1;
  double start_time = 2;
  double end_time = 3;
  float confidence = 4;
}

message ASTStreamResponse {
  bool acknowledged = 1;
  int64 chunks_received = 2;
  string status_message = 3;
}
```

#### Common Coordinates (`common.proto`)
```protobuf
syntax = "proto3";

package rag.common.v1;

// Normalized coordinates strictly bound to [0.0, 1.0] anchored at top-left origin (0.0, 0.0)
message BoundingBox {
  float x1 = 1;  // Normalized top-left x [0.0, 1.0]
  float y1 = 2;  // Normalized top-left y [0.0, 1.0]
  float x2 = 3;  // Normalized bottom-right x [0.0, 1.0]
  float y2 = 4;  // Normalized bottom-right y [0.0, 1.0]
  int32 page_number = 5;
}
```

---

## 5. Vision & Tabular Pipeline Models & Engine Adapters

### A. Docling Layout Engine (`internal.vision.layout.docling_engine`)
- **`ParsedASTNode`**:
  ```python
  @dataclass
  class ParsedASTNode:
      node_id: str
      node_type: str  # paragraph, heading_h1..h6, list, table
      text_content: str  # Plain text or Markdown (headings prefix '# ', tables carry GFM table)
      bounding_box: NormalizedBoundingBox  # Normalized [0.0, 1.0] top-left origin
      confidence: float  # [0.0, 1.0]
      table_parquet_s3_uri: str | None = None  # Populated for table nodes
      table_data: TableData | None = None
  ```
- **`PageLayoutResult`**:
  ```python
  @dataclass
  class PageLayoutResult:
      page_number: int  # 1-indexed page number
      nodes: list[ParsedASTNode]
  ```
- **Resumption & Cooperative Cancellation**:
  - `parse_slice_pages(pdf_path, start_from_page=0, total_pages=..., stop_event=..., on_page_complete=...)`:
    - Skips pages `< start_from_page` for zero-duplicate mid-slice recovery.
    - Yields page by page across worker thread boundary, updating Temporal heartbeats every 10s.
    - Checks `stop_event.is_set()` to abort cleanly on activity cancellation.

### B. Fallback OCR Engine (`internal.vision.ocr.fallback_ocr`)
- **`FallbackOCREngine`**:
  - Secondary OCR pass integrating PaddleOCR for multilingual/scanned regions.
  - Triggers when node confidence < `settings.OCR_CONFIDENCE_THRESHOLD` (default `0.80`, bounded `[0.0, 1.0]`) or when scanned visual blocks contain empty text streams.
  - Updates text content and enhances confidence scores while preserving reading order and normalized bounding boxes.

### C. Tabular Dataset Extraction & PyArrow Parquet (`internal.vision.table`)
- **`TableData` (`internal.vision.table.table_extractor`)**:
  - `to_markdown() -> str`: Clean GitHub-Flavored Markdown (GFM) with escaped pipes and table captions.
  - `compute_table_hash() -> str`: Deterministic SHA-256 table hash from canonical JSON representation:
    ```python
    canonical_repr = json.dumps({"headers": headers, "rows": rows}, sort_keys=True)
    table_hash = hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()
    ```
  - `to_pyarrow_table() -> pyarrow.Table`: Columnar PyArrow Table with strongly-typed columns (inferred boolean, int64, float64, string) and unique `_{i}` suffixes for repeated headers.
- **`ParquetS3Writer` (`internal.vision.table.parquet_s3_writer`)**:
  - Serializes `TableData` to Parquet bytes using configurable compression through the `compression` argument or `settings.TABLE_PARQUET_COMPRESSION` (default `snappy`, supported codecs: `snappy`, `zstd`, `gzip`).
  - Persists directly to S3 under content-addressed path:
    `s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet`
  - Injects mandatory S3 object metadata headers:
    - `x-amz-meta-tenant-id`: `<tenant_id>`
    - `x-amz-meta-doc-hash`: `<doc_hash>`
    - `x-amz-meta-table-hash`: `<table_hash>`
    - `x-amz-meta-page-number`: `<page_number>`
    - `Content-Type`: `application/vnd.apache.parquet`
- **Tabular Settings Configuration**:
  - `settings.TABLE_PARQUET_COMPRESSION`: Compression codec for PyArrow Parquet tables (default: `snappy`, supported codecs: `snappy`, `zstd`, `gzip`). Documented alongside `settings.OCR_CONFIDENCE_THRESHOLD` to govern downstream tabular artifact persistence.

### D. Memory & VRAM Protocol (`internal.vision.memory`)
- `cleanup_vram_and_memory()`:
  - Invokes `torch.cuda.empty_cache()` and `torch.cuda.ipc_collect()` after each page/slice iteration.
  - Invokes `gc.collect()` to maintain bounded host heap memory within container cgroup caps.

---

## 6. Audio Pipeline & Diarization Models & Engine Adapters

### A. Audio Data Models (`internal.audio.models`)
- **`WordTiming`**:
  ```python
  @dataclass
  class WordTiming:
      word: str
      start_time: float  # Absolute media timeline timestamp
      end_time: float  # Absolute media timeline timestamp
      confidence: float = 1.0
  ```
- **`SpeakerTurn`**:
  ```python
  @dataclass
  class SpeakerTurn:
      speaker_id: str
      start_time: float  # Relative to processed slice
      end_time: float  # Relative to processed slice
  ```
- **`RawWhisperSegment`**:
  ```python
  @dataclass
  class RawWhisperSegment:
      text: str
      start_time: float  # Relative to processed slice
      end_time: float  # Relative to processed slice
      words: list[WordTiming]
  ```
- **`AlignedTranscriptSegment`**:
  ```python
  @dataclass
  class AlignedTranscriptSegment:
      speaker_id: str
      start_time_seconds: float  # Absolute timeline timestamp
      end_time_seconds: float  # Absolute timeline timestamp
      text: str
      words: list[WordTiming]
  ```

### B. Faster-Whisper Large-v3 Speech-to-Text Engine (`internal.audio.asr.whisper_engine`)
- **`WhisperEngine`**:
  - High-throughput CTranslate2 runner (`settings.WHISPER_MODEL_SIZE`, `settings.WHISPER_DEVICE`, `settings.WHISPER_COMPUTE_TYPE`, `settings.WHISPER_BEAM_SIZE`).
  - Word-level timestamps (`word_timestamps=True`).
  - Resumption support: skips audio prior to `start_from_offset` for mid-window recovery.
  - Progressive audio interval processing (e.g. 15–30s strides) yielding across the thread boundary for active heartbeat reporting.
  - Cooperative cancellation via `threading.Event` (`stop_event`).
  - Resilient mock/simulated fallback runner when CUDA/CTranslate2 binaries are absent.

### C. PyAnnote Speaker Diarization Engine (`internal.audio.diarization.pyannote_engine`)
- **`PyAnnoteEngine`**:
  - Voiceprint clustering adapter standardizing on PyAnnote 3.1 (`settings.PYANNOTE_MODEL_ID`).
  - Hugging Face cache support via `settings.HF_HOME` and gated token support via `settings.HF_TOKEN` with secured logging redaction.
  - Configurable speaker count constraints (`min_speakers`, `max_speakers`).
  - Cooperative cancellation and progressive interval processing.
  - Fallback simulated runner when neural checkpoints are not pre-downloaded.

### D. Timeline Alignment & Absolute Timeline Projection (`internal.audio.alignment`)
- **`align_audio_timeline`**:
  - Computes temporal overlap between each word and speaker turns:
    $$\text{overlap}(w, s) = \max(0.0, \min(w.\text{end}, s.\text{end}) - \max(w.\text{start}, s.\text{start}))$$
  - Assigns word to turn with $\max(\text{overlap})$.
  - Silence gap fallback: assigns unassigned words to nearest preceding active turn, or `"SPEAKER_00"`.
  - Groups consecutive words by speaker, splitting on speaker change or silence gap $> 1.5$s.
  - Enforces the **Absolute Timeline Invariant (ADR-009)**:
    $$\text{timestamp}_{\text{absolute}} = \text{start\_offset\_seconds} + \text{start\_from\_offset} + t_{\text{slice}}$$
    Both `TranscriptSegment.start_time_seconds` / `end_time_seconds` AND `WordTimestamp.start_time` / `end_time` are projected onto this absolute timeline.

### E. Audio Memory & VRAM Cleanup (`internal.audio.memory`)
- `cleanup_vram_and_memory()`:
  - Flushes `torch.cuda.empty_cache()` and `gc.collect()` after each audio window execution.

---

## 7. Production Deployment, Autoscaling & Observability Specifications

### A. Production Helm 3 Chart (`deployments/helm/`)
The microservice is packaged as a Helm 3 chart (`rag-document-ai-worker`, `v0.1.0`) providing parameterized deployments for both decoupled worker types:

| Component | Values File | Task Queue | Default Replicas | GPU Allocation |
| :--- | :--- | :--- | :--- | :--- |
| `doc-ai-vision-worker` | `values-vision.yaml` | `doc-ai-vision-queue` | 2 (Min: 2, Max: 20) | 1x NVIDIA L4/A10G |
| `doc-ai-audio-worker` | `values-audio.yaml` | `doc-ai-audio-queue` | 1 (Min: 1, Max: 10) | 1x NVIDIA L4/A10G |

#### Memory & Security Isolations
- **IPC / PyTorch DataLoader**: 2 GiB memory-backed `emptyDir` mounted at `/dev/shm`.
- **Scratch Buffer**: 4 GiB `emptyDir` mounted at `/tmp`.
- **Pre-baked Model Cache**: Read-only NVMe PVC mount at `/models/cache` (`doc-ai-model-weights-nvme-pvc`).
- **Security Context**: Non-root execution (`runAsUser: 1000`, `runAsGroup: 1000`, `runAsNonRoot: true`), `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, and capabilities drop (`drop: [ALL]`).

### B. KEDA Autoscaling (`templates/keda-scaledobject.yaml`)
Autoscaling is driven dynamically by Temporal task queue schedule-to-start latency / queue depth:
- `doc-ai-vision-queue`: Target threshold **5** pending tasks/pod, `minReplicaCount: 2`, `maxReplicaCount: 20`, `cooldownPeriod: 300s`, `pollingInterval: 15s`.
- `doc-ai-audio-queue`: Target threshold **3** pending tasks/pod, `minReplicaCount: 1`, `maxReplicaCount: 10`, `cooldownPeriod: 300s`, `pollingInterval: 15s`.

### C. GitOps ArgoCD Integration & KEDA Drift Immunity
ArgoCD manifests (`deployments/argocd/`) deploy the decoupled worker workloads across staging and production namespaces (`doc-ai`, `doc-ai-staging`):
- **Drift Immunity**: Configures `ignoreDifferences` on `apps/Deployment` for `/spec/replicas` so ArgoCD does not overwrite dynamic KEDA replica counts back to static Git values.
- **Automated Sync**: Configures `selfHeal: true`, `prune: true`, and exponential retry backoff.

### D. Prometheus Observability & Alerting Rules (`templates/prometheusrule.yaml`)
The worker exposes 8 operational metrics at `GET /metrics`:
- `doc_ai_pages_processed_total`: Total document pages processed.
- `doc_ai_audio_seconds_processed_total`: Total seconds of audio transcribed.
- `doc_ai_heartbeat_emissions_total`: Total Temporal activity heartbeats emitted.
- `doc_ai_cuda_oom_errors_total`: Total CUDA Out-of-Memory exceptions.
- `doc_ai_task_duration_seconds`: Histogram of task execution latency.
- `doc_ai_heartbeat_delay_seconds`: Histogram of heartbeat emission intervals.
- `doc_ai_vram_allocated_bytes`: Gauge of GPU VRAM allocated.
- `doc_ai_activity_failures_total`: Counter of activity processing failures.

#### Alerting Invariants
- `QueueBacklogGrowth` (Warning): Triggered when `schedule_to_start` latency on either queue exceeds 45s for > 3 minutes.
- `CudaOOMErrors` (Page): Triggered immediately when `rate(doc_ai_cuda_oom_errors_total[5m]) > 0`.
- `ActivityFailuresHigh` (Page): Triggered when activity failure rate exceeds 2% across 10 minutes (`rate(doc_ai_activity_failures_total[10m]) / rate(doc_ai_task_duration_seconds_count[10m]) > 0.02`).


