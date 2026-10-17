# Phase 2: Vision & Tabular Pipeline - Implementation Plan & Prompt

## 1. Executive Summary & Objective

Phase 2 implements the core **Vision & Tabular Pipeline** for the `rag-document-ai-worker` microservice, strictly adhering to:
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

### Core Deliverables for Phase 2:

1. **Docling Layout Engine Adapter (`internal/vision/layout/docling_engine.py`)**:
   - Progressive page streaming across the thread boundary with yield-based iterator / progress callback.
   - Cooperative cancellation via `threading.Event` (`stop_event`).
   - Normalization of all extracted bounding boxes to unit coordinates $[0.0, 1.0]$ with top-left origin convention using `internal.vision.coordinates.normalize_bbox`.
   - Layout decomposition into structured AST nodes: paragraphs, headings (with explicit hierarchical tags `heading_h1` .. `heading_h6` and `#` markdown syntax), lists, and table placeholders.
   - Resumption support: skips pages prior to `start_from_page` for mid-slice recovery without duplicate parsing.
   - Safe fallback/mock execution layer when heavy PyTorch/Docling C++ models are not compiled or during lightweight test suites, with seamless plug-in for native Docling models (`DocumentConverter`).

2. **Multi-Pass Fallback OCR Adapter (`internal/vision/ocr/fallback_ocr.py`)**:
   - Integrates **PaddleOCR** as the high-accuracy secondary OCR engine for multilingual/scanned regions.
   - Confidence-based OCR trigger: triggers secondary OCR pass when native text confidence falls below threshold (`settings.OCR_CONFIDENCE_THRESHOLD`, default 0.80) or when scanned image regions lack vector font streams.
   - Integrates normalized bounding boxes and merges reconstructed OCR spans into AST layout text without duplication.
   - Provides a lightweight mock fallback mode for testing environments lacking GPU or compiled PaddleOCR C++ libraries.

3. **Table Extraction Engine (`internal/vision/table/table_extractor.py`)**:
   - Boundary detection and structural table extraction powered by **Table-Transformer / TableFormer** (rows, columns, headers, cells, spanning cells).
   - Generates clean, semantically dense GitHub-Flavored Markdown (GFM) summaries for LLM comprehension in vector chunks.
   - Computes deterministic SHA-256 table hash (`table_hash`) using canonical JSON serialization of normalized table headers and cell contents:
     ```python
     canonical_repr = json.dumps({"headers": headers, "rows": rows}, sort_keys=True)
     table_hash = hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()
     ```
   - Converts tabular matrices into columnar PyArrow `Table` schema with strongly-typed columns (inferred string, int64, float64, boolean).

4. **Direct S3 Parquet Serializer (`internal/vision/table/parquet_s3_writer.py`)**:
   - Columnar Parquet serialization via `pyarrow.parquet.write_table` with Snappy compression (`compression="snappy"`).
   - Deterministic content-addressed S3 path generation:
     `s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet`
   - Direct asynchronous upload via `S3StorageClient` using multipart / memory-bounded streaming via `aiobotocore`.
   - Injects mandatory S3 object metadata headers:
     - `x-amz-meta-tenant-id`
     - `x-amz-meta-doc-hash`
     - `x-amz-meta-table-hash`
     - `x-amz-meta-page-number`
     - `Content-Type: application/vnd.apache.parquet`

5. **Streaming gRPC AST Client (`internal/transport/grpc/chunk_client.py`)**:
   - End-to-end streaming client implementing `DocumentAIChunkService` Protobuf contract (`api/proto/v1/document_ai.proto`).
   - Uses compiled Protobuf v3 stubs (`api/proto/v1/*_pb2.py`, `*_pb2_grpc.py`).
   - Client-streaming RPC `StreamDocumentAST(stream DocumentASTChunk) returns (ASTStreamResponse)`.
   - Buffers parsed AST nodes per slice and streams the complete 50–100 page document manifest within $\le 1.5$ seconds (meeting the AST Manifest Egress SLO).
   - Transports Parquet S3 URIs in `ASTNode.table_parquet_s3_uri` without leaking bulk binary data over gRPC (adhering to ADR-003).

6. **VRAM & Memory Lifecycle Management Protocol**:
   - Offload model inference to dedicated worker threads (`asyncio.to_thread()`) to keep Python's GIL from blocking Temporal heartbeats (ADR-004).
   - Proactive GPU cache deallocation: invoke `torch.cuda.empty_cache()` and `gc.collect()` after each page/slice execution to prevent VRAM fragmentation and eliminate CUDA OOM errors across continuous runs.
   - Respects container limits: `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128`, `OMP_NUM_THREADS=2`, `OPENBLAS_NUM_THREADS=2`, and `LD_PRELOAD=/usr/lib/libjemalloc.so`.

7. **Full Activity Integration (`internal/temporal/activities/vision_activity.py`)**:
   - Rewire `ParseDocumentSliceActivity` from the Phase 1 simulation stub to the full production pipeline:
     - Downloads physical PDF slice (`doc_part_*.pdf`) from S3.
     - Runs `DoclingEngine` + `TableExtractor` inside `asyncio.to_thread()`.
     - Detects tables, extracts PyArrow data, writes Parquet directly to S3.
     - Maintains 10-second heartbeat continuity with updated `VisionHeartbeatDetails`.
     - Streams AST chunks over gRPC to `rag-chunk-lifecycle-service`.
     - Preserves fail-fast quarantine (`CORRUPT_INPUT_QUARANTINE`) for truncated or invalid PDFs.
     - Preserves mid-slice checkpoint resumption (e.g. recovering at page 51 after worker failure at page 52).

8. **Documentation & Quality Verification**:
   - Update `.ai/doc-api.md` with complete contracts, schemas, and S3 metadata specifications.
   - Update `.ai/progress-tracker.md` reflecting completed deliverables.
   - Comprehensive unit and integration test suite (`test/unit/`, `test/integration/`).
   - Performance benchmark tests validating SLOs (50-page manifest egress in $\le 1.5$s, VRAM deallocation).
   - Clean Ruff linting, formatting, and strict Mypy type validation.
   - At least two git commits using conventional commit standards (`feat:`, `tests:`, `docs:`).

---

## 2. Directory Structure & Files to Modify / Create

```
rag-document-ai-worker/
├── internal/
│   ├── config/
│   │   └── settings.py               # Add OCR confidence threshold and table config
│   ├── vision/
│   │   ├── layout/
│   │   │   ├── __init__.py           # [New]
│   │   │   └── docling_engine.py     # [New] Docling parsing adapter with page streaming & normalized bboxes
│   │   ├── ocr/
│   │   │   ├── __init__.py           # [New]
│   │   │   └── fallback_ocr.py       # [New] PaddleOCR fallback adapter for low-confidence spans
│   │   ├── table/
│   │   │   ├── __init__.py           # [New]
│   │   │   ├── table_extractor.py    # [New] Table-Transformer boundary detection, markdown & PyArrow
│   │   │   └── parquet_s3_writer.py  # [New] PyArrow Parquet serializer & S3 direct uploader
│   │   └── coordinates.py            # [Existing] Normalized bounding boxes [0.0, 1.0]
│   ├── transport/
│   │   └── grpc/
│   │       └── chunk_client.py       # [Modify] Production AST gRPC streaming client
│   ├── temporal/
│   │   └── activities/
│   │       └── vision_activity.py    # [Modify] Integrate layout engine, table writer, and gRPC client
│   └── storage/
│       └── s3_client.py              # [Modify] Enhance S3 client with real aiobotocore support
├── test/
│   ├── unit/
│   │   ├── test_docling_engine.py    # [New] Unit tests for Docling layout parser & coordinates
│   │   ├── test_fallback_ocr.py      # [New] Unit tests for PaddleOCR confidence evaluation & fallback
│   │   ├── test_table_extractor.py   # [New] Unit tests for TableExtractor, markdown & canonical hash
│   │   └── test_parquet_s3_writer.py # [New] Unit tests for PyArrow serialization & S3 paths/headers
│   ├── integration/
│   │   ├── test_vision_pipeline_integration.py # [New] Integration test for full end-to-end vision activity
│   │   └── test_checkpoint_recovery.py         # [New] Checkpoint recovery test (SIGKILL at p52 -> resume p51)
│   └── performance/
│       └── test_manifest_egress_perf.py        # [New] Performance test asserting 50-page egress <= 1.5s
├── .ai/
│   ├── doc-api.md                    # [Modify] Synchronize with new components & schemas
│   └── progress-tracker.md           # [Modify] Update Phase 2 status
└── prompts/
    └── phase-2-vision-tabular-pipeline.md # [Updated] Hardened implementation plan
```

---

## 3. Detailed Component Architecture & Protocols

### A. Docling Layout Engine (`internal/vision/layout/docling_engine.py`)
- Emits structured `PageLayoutResult` containing:
  - `page_number`: 1-indexed page number.
  - `nodes`: List of `ParsedASTNode`:
    - `node_id`: UUIDv4 string.
    - `node_type`: String matching `"paragraph"`, `"heading_h1"`, `"heading_h2"`, `"heading_h3"`, `"list"`, `"table"`.
    - `text_content`: Plain text or Markdown (headings prefix `# `, tables carry clean GFM table).
    - `bounding_box`: Top-left normalized `[0.0, 1.0]` using `normalize_bbox`.
    - `confidence`: Float in `[0.0, 1.0]`.
    - `table_data`: Optional `TableData` if `node_type == "table"`.
- Resumption support: skips pages prior to `start_from_page` for mid-slice recovery.
- Thread isolation: executed within worker thread with periodic cancellation checks on `stop_event.is_set()`.

### B. Fallback OCR Adapter (`internal/vision/ocr/fallback_ocr.py`)
- Evaluates text span confidence scores against `settings.OCR_CONFIDENCE_THRESHOLD` (default 0.80).
- If confidence < threshold or text is empty in visual regions, invokes PaddleOCR to recover high-confidence characters.
- Reconstructs text spans and updates AST node text and confidence score without altering reading order.

### C. Table Extraction & Parquet Serializer (`internal/vision/table/`)
- `TableData`: Holds `headers: list[str]`, `rows: list[list[Any]]`, `bbox: NormalizedBoundingBox`, `page_number: int`.
- **Markdown Summary**: Generates clean GitHub-Flavored Markdown:
  ```markdown
  | Header 1 | Header 2 |
  | --- | --- |
  | Value 1 | Value 2 |
  ```
- **Deterministic Table Hash**:
  ```python
  canonical_repr = json.dumps({"headers": headers, "rows": rows}, sort_keys=True)
  table_hash = hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()
  ```
- **PyArrow Schema & Parquet Serialization**:
  - Converts table matrix to `pyarrow.Table` with inferred column types.
  - Serializes to bytes using `pyarrow.parquet.write_table(table, sink, compression="snappy")`.
- **S3 Egress**:
  - Direct upload to `s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet`.
  - Injects mandatory metadata: `x-amz-meta-tenant-id`, `x-amz-meta-doc-hash`, `x-amz-meta-table-hash`, `x-amz-meta-page-number`, and `Content-Type: application/vnd.apache.parquet`.

### D. gRPC Streaming Client (`internal/transport/grpc/chunk_client.py`)
- Implements `DocumentAIChunkServiceStub.StreamDocumentAST`.
- Transforms `ParsedASTNode` into `rag.document_ai.v1.DocumentASTChunk` and `rag.document_ai.v1.ASTNode`.
- Streams batches over gRPC HTTP/2 channel to `rag-chunk-lifecycle-service`.
- Validates server response (`acknowledged == True`).

### E. VRAM Management & Memory Lifecycle
- After each page or slice parse:
  ```python
  if torch.cuda.is_available():
      torch.cuda.empty_cache()
  gc.collect()
  ```
- Protects container from memory exhaustion during large 50–100 page batch parses.

### F. Security & Redaction Invariants
- Zero logging of document text, OCR results, table cell values, or presigned URLs.
- Structured JSON logs with `tenant_id`, `document_id`, `doc_hash`, `page_number`, `node_count`, `table_count`.

---

## 4. Verification & Testing Strategy

1. **Unit Tests**:
   - `test_docling_engine.py`: Tests page streaming, coordinate normalization, hierarchy levels, resumption page skipping, and cooperative cancellation.
   - `test_fallback_ocr.py`: Tests confidence thresholds, fallback trigger, and OCR text enhancement.
   - `test_table_extractor.py`: Tests markdown formatting, PyArrow schema inference, and canonical table hash determinism.
   - `test_parquet_s3_writer.py`: Tests Snappy Parquet generation, schema validation, and S3 path/metadata correctness.
2. **Integration Tests**:
   - `test_vision_pipeline_integration.py`: End-to-end `ParseDocumentSliceActivity` execution with mock S3 and gRPC clients, verifying Temporal heartbeats, Parquet S3 writing, AST streaming, and checkpoint resumption.
   - `test_checkpoint_recovery.py`: Simulates worker crash/SIGKILL at page 52 and asserts seamless recovery starting at page 51 without duplicate processing.
3. **Performance Tests**:
   - `test_manifest_egress_perf.py`: Asserts gRPC streaming of a 50–100 page document manifest completes within $\le 1.5$ seconds.
4. **Quality Gates**:
   - `python -m ruff check --fix .`
   - `python -m ruff format .`
   - `python -m mypy internal cmd test`
   - `python -m pytest test/unit test/integration`
