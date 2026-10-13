# Code Standards - RAG Document AI Worker

## General Principles

- **Single Responsibility**: Decouple compute-heavy ML parsing from Temporal activity lifecycle management.
- **Root-Cause Focus**: Never patch race conditions or GIL stalls with arbitrary sleep timers; use event-driven and thread-isolated constructs.
- **Fail-Fast on Corrupt Input**: Truncated PDFs or corrupt audio must immediately throw non-retryable application exceptions so the orchestrator can quarantine them without retry storms.

---

## Python & Code Quality Standards

- **Runtime Target**: Python 3.11+.
- **Linter & Formatter**: **Ruff** is the authoritative linter, formatter, and import sorter for the repository:
  - Line length: `100` characters.
  - Formatter: `python -m ruff format .`
  - Linter & Import Sorter: `python -m ruff check --fix .`
  - Imports: Grouped into standard library, third-party, and first-party (`internal`, `cmd`, `api`).
- **Static Typing**: Strict typing with `mypy`.
  - No untyped `def` functions.
  - Avoid `typing.Any` wherever possible; use explicit Pydantic models or typed Protobuf structures.
  - Validate unknown S3 metadata and task payloads using Pydantic `BaseModel` schemas.

---

## Concurrency & GIL-Safety Rules

1. **GIL Isolation**: Heavy PyTorch, Docling, PaddleOCR, and Whisper loops MUST be offloaded to worker threads via `asyncio.to_thread()` or `concurrent.futures.ThreadPoolExecutor(max_workers=1)`.
2. **Heartbeat Continuity**: The primary `asyncio` event loop must remain free to report Temporal activity heartbeats every 10 seconds.
3. **Thread Contention**: Bounded CPU threads via environment variables:
   ```bash
   OMP_NUM_THREADS=2
   OPENBLAS_NUM_THREADS=2
   ```

---

## Secured Logging & Observability

- **Structured JSON Logging**: All logs must be emitted as machine-parseable JSON lines (using `structlog` or standard logging configured with JSON formatters).
- **Mandatory Correlation IDs**: Every log message must include:
  - `tenant_id`
  - `document_id` / `audio_id`
  - `task_queue`
  - `activity_id`
  - `page_number` / `time_offset`
- **Security Invariant (Secured Logging)**:
  - **Strict Redaction**: Never log raw document text spans, transcript PII, presigned S3 URLs containing signature tokens, API keys, or raw bearer headers.
  - Apply automated regex scrubbing or token masking prior to log emission.
  - Error logs must sanitize internal system paths and database schemas.

---

## Testing & Quality Assurance

- Tests must be maintained across:
  - `test/unit/` (or `Tests/UnitTest/<test-name>`): Math normalization ($[0.0, 1.0]$ bounds), schema validation, AST serialization.
  - `test/integration/` (or `Tests/IntegrationTest/<test-name>`): Temporal heartbeat mock runs, checkpoint recovery simulations, and mock S3 Parquet writes.
- Maintain $\ge 80\%$ test coverage on business logic in `internal/`.

---

## Doc API Mandatory Maintenance

- **Continuous Synchronization**: Whenever any model, activity signature, gRPC proto, or storage path is created or altered, `.ai/doc-api.md` **MUST** be updated immediately in the same change step, no matter how small the code modification is.

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
