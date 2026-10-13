# Security Context - RAG Document AI Worker

## Threat Model & Trust Boundaries

- **Upstream Ingestion Slices**: Untrusted input boundary. PDFs, scans, and audio windows fetched from S3 may contain corrupt headers, malformed streams, or malicious exploit vectors (e.g. PDF parser exploits, decompression bombs).
- **Compute Worker Runtime**: Isolated compute environment. Workers operate with non-root Linux permissions inside container images with restricted capabilities (`securityContext: readOnlyRootFilesystem: false`, non-root UID 1000).
- **Internal RPC & Egress**: gRPC transport to `rag-chunk-lifecycle-service` must run with mTLS or VPC-internal network isolation.
- **Object Storage Access**: Read and write access to S3 is strictly scoped to specific tenant prefixes (`s3://<bucket>/artifacts/<tenant_id>/...`) using short-lived IAM credentials (AWS IRSA).

---

## Secured Logging Invariants

1. **Confidential Content Redaction**: Document text extractions and speech transcripts may contain customer Personally Identifiable Information (PII), Protected Health Information (PHI), or confidential business data. **Raw text bodies, OCR blocks, and transcript segments must never be logged to stdout or logging backends**.
2. **Secrets & Credentials Masking**: Presigned URLs, AWS SigV4 query parameters (`X-Amz-Signature`), Temporal API tokens, and gRPC authorization metadata must be intercepted and masked before any log emission.
3. **Structured Sanity**: Log entries must be structured JSON logs containing only metadata IDs (`tenant_id`, `document_id`, `page_number`, `node_count`, `duration_ms`), never raw binary or payload contents.

---

## Input Quarantine & Memory Protection

1. **Malicious / Corrupted File Quarantine**:
   - Files failing PDF header validation or audio codec decoding must trigger non-retryable Temporal `ApplicationError` exceptions with error reason `CORRUPT_INPUT_QUARANTINE`.
   - Never allow corrupted inputs to trigger infinite retry loops that waste expensive GPU compute or exhaust memory.
2. **Memory Safety & Resource Caps**:
   - `jemalloc` (`LD_PRELOAD=/usr/lib/libjemalloc.so`) must be active in production to prevent glibc heap fragmentation.
   - Set `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128` to avoid GPU VRAM fragmentation.
   - Enforce memory limits (16 GiB limit) to ensure container orchestrator terminates runaway processes before host memory starvation occurs.

---

## Secrets & Configuration Management

- Environment variables and credentials must never be committed to source control.
- All secrets are loaded through environment variables or Kubernetes Secret volume mounts.
- Local development utilizes `.env.example` templates; actual `.env` files are ignored via `.gitignore`.

---

## Git Commit Standards

- **Git Commit Frequency**: Commit code as you build. Every working session or phase must contain **at least two git commits**.
- **Conventional Commit Patterns**: All git commit messages must strictly adhere to the following prefixes:
  - `feat:` for new features 
  - `fix:` for bug fixes
  - `refactor:` for code refactoring
  - `docs:` for documentation updates
  - `tests:` for tests addition and modification
  - `chore:` for maintenance tasks and environment setup
