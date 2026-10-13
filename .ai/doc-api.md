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
| `/api/docs` | `GET` | `application/json` | Alias redirect or raw OpenAPI 3.1 JSON schema |
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
- **Heartbeat Timeout**: `15s` (Emits heartbeat every `10s`)
- **Execution Mode**: Runs model inference in dedicated worker thread via `asyncio.to_thread()`.
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
      status: str  # "SUCCESS" | "QUARANTINED"
      pages_processed: int
      table_count: int
      parquet_s3_uris: list[str]  # S3 URIs of extracted tables
      ast_streamed: bool
      duration_seconds: float
  ```

---

### B. Audio Task Queue: `doc-ai-audio-queue`

#### Activity: `TranscribeAudioWindowActivity`
- **Module**: `internal.temporal.activities.audio_activity`
- **Queue**: `doc-ai-audio-queue`
- **Heartbeat Timeout**: `15s` (Emits heartbeat every `10s`)
- **Execution Mode**: Runs inference in worker thread via `asyncio.to_thread()`.
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
      status: str  # "SUCCESS" | "QUARANTINED"
      audio_seconds_processed: float
      segment_count: int
      speaker_count: int
      ast_streamed: bool
      duration_seconds: float
  ```

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
  string text_content = 3;
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
  double start_time_seconds = 2;
  double end_time_seconds = 3;
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

message BoundingBox {
  float x1 = 1;  // Normalized [0.0, 1.0]
  float y1 = 2;  // Normalized [0.0, 1.0]
  float x2 = 3;  // Normalized [0.0, 1.0]
  float y2 = 4;  // Normalized [0.0, 1.0]
  int32 page_number = 5;
}
```
