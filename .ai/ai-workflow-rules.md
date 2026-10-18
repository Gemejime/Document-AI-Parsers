# AI Workflow Rules - RAG Document AI Worker

## Development Approach

Build this microservice incrementally following a spec-driven workflow:
- The specification documents in [`tasks/`](tasks) and context files in [`.ai/`](.ai) define system invariants, data structures, and operational boundaries.
- Adhere strictly to the decoupled worker design (`doc-ai-vision-worker` and `doc-ai-audio-worker`) and the 5 backend architectural layers documented in [`.ai/architecture.md`](.ai/architecture.md).
- Implement against these specs without inferring undocumented dependencies or deviating from architectural decision records (ADRs).

---

## Human-in-the-Loop Rules

- **Plan First**: Propose an implementation plan or prompt in `prompts/<task-name>.md` before making code changes, and await human confirmation.
- **Ambiguity Escalation**: If GPU resource limits, queue configurations, or schema mappings are ambiguous, prompt for clarification before proceeding.
- **Destructive Action Guards**: Always request human confirmation before running destructive operations, schema drops, or major refactors.
- **Demonstrate & Verify**: Provide clear verification steps, unit test output, and linting checks after completing a unit.

---

## Non-Negotiable Invariants

1. **Continuous Doc API & OpenAPI Maintenance**: The [`.ai/doc-api.md`](.ai/doc-api.md) file and OpenAPI 3.1 endpoints (`/docs`, `/redoc`, `/api/docs`, `/openapi.json`) MUST be updated on **every code change or addition**, regardless of how small the modification is.
2. **Secured Logging**: All logs emitted by workers or utilities must be structured JSON logs. Strictly redact customer document text, OCR spans, speech transcripts, PII/PHI, presigned URLs, and authentication tokens before emission.
3. **GIL Isolation**: Blocking model inference loops (Docling, PyTorch, Whisper, PaddleOCR) MUST be isolated in worker threads (`asyncio.to_thread()`), ensuring Temporal activity heartbeats emit every 10 seconds without latency degradation.
4. **Normalized Unit Coordinates**: All document bounding box coordinates MUST be normalized to unit float bounds $[0.0, 1.0]$.
5. **Direct S3 Parquet Egress**: Tabular datasets must serialize directly to S3 as Parquet files under deterministic content-addressed keys (`s3://<bucket>/artifacts/<tenant_id>/<doc_hash>/tables/<table_hash>.parquet`), streaming only S3 URIs via gRPC.
6. **Git Commit Standards**:
   - **Git Commit Frequency**: Commit code as you build. Every working session or phase must contain **at least two git commits**.
   - **Conventional Commit Patterns**: All git commit messages must strictly adhere to the following prefixes:
     - `feat:` for new features 
     - `fix:` for bug fixes
     - `refactor:` for code refactoring
     - `docs:` for documentation updates
     - `tests:` for tests addition and modification
     - `chore:` for maintenance tasks and environment setup
7. **Quality Gates**: Ruff linting, formatting, and import sorting must pass cleanly before any commit.

---

## Pre-Commit Verification Checklist

Before committing code, submitting changes, or marking a task complete:

1. **Lint & Import Sorting**: Run `python -m ruff check --fix .` (zero errors or warnings).
2. **Code Formatting**: Run `python -m ruff format .` (clean formatting verified).
3. **Type Safety**: Verify static types via `mypy cmd internal test`.
4. **Automated Tests**: Ensure unit and integration tests are passing in `test/unit/` and `test/integration/`.
5. **Secured Logging Audit**: Verify no raw PII, document text, or secret tokens are leaked in logging statements.
6. **Doc API & OpenAPI Sync**: Confirm [`.ai/doc-api.md`](.ai/doc-api.md) reflects any new or modified classes, parameters, endpoints, or storage keys.
7. **Progress Tracker**: Update [`.ai/progress-tracker.md`](.ai/progress-tracker.md) with session notes and completed items.
