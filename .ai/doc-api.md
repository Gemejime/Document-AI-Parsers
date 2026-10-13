# Microservice Documented API ("Doc API")

> **MANDATORY INVARIANT**: This document specifies both the internal system contracts and the **HTTP Doc API Endpoint** exposed by the microservice.
> **API ACCESSIBILITY REQUIREMENT**: This documentation specification MUST be accessible directly via an HTTP endpoint on the worker's internal HTTP server:
> - `GET /docs` — Returns the rendered documentation in Markdown / HTML.
> - `GET /api/docs` — Returns the structured documentation schema in JSON format.
> This file and the corresponding API endpoint handler MUST be updated on **every code change or addition**, regardless of how small the modification is.

---

## 1. Documentation API Endpoint (`GET /docs` & `GET /api/docs`)

Every worker pod (`doc-ai-vision-worker` and `doc-ai-audio-worker`) runs an internal lightweight HTTP server (default port `8080`) that exposes:
- **`GET /docs`**:
  - **Content-Type**: `text/markdown; charset=utf-8` or `text/html`
  - **Description**: Returns the complete, real-time documentation of the worker, including active Temporal queues, gRPC schemas, S3 storage keys, and model versions.
- **`GET /api/docs`**:
  - **Content-Type**: `application/json`
  - **Description**: Returns a structured JSON dictionary of all available endpoints, Temporal task queues, S3 bucket conventions, and Protobuf schema definitions.
- **`GET /healthz`**: Liveness probe (HTTP 200 OK).
- **`GET /ready`**: Readiness probe (HTTP 200 OK when models and queues are initialized).
- **`GET /metrics`**: Prometheus metrics endpoint.

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
      s3_key: str              # e.g., "slices/{tenant_id}/{doc_hash}/doc_part_02.pdf"
      start_page: int          # 0-indexed start page
      page_count: int          # Number of pages in this slice
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
      status: str              # "SUCCESS" | "QUARANTINED"
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
      s3_key: str              # e.g., "audio/{tenant_id}/{audio_hash}/audio_part_01.wav"
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
      status: str              # "SUCCESS" | "QUARANTINED"
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
  string node_type = 2;         # "paragraph", "heading", "table", "list", etc.
  string text_content = 3;
  rag.common.v1.BoundingBox bounding_box = 4;
  string table_parquet_s3_uri = 5; # Populated only if node_type == "table"
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
  float x1 = 1;  # Normalized [0.0, 1.0]
  float y1 = 2;  # Normalized [0.0, 1.0]
  float x2 = 3;  # Normalized [0.0, 1.0]
  float y2 = 4;  # Normalized [0.0, 1.0]
  int32 page_number = 5;
}
```
