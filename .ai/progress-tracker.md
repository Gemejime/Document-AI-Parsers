# Progress Tracker - RAG Document AI Worker

## Current Phase

- **Phase 1: Worker Decoupling & Runtimes** (In Progress)

## Current Goal

- Monorepo repository setup, environment configuration, code standards, CI/CD integration, and AI context definitions.

## Completed

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

## In Progress

- Scaffolding monorepo directory layout (`cmd/`, `internal/`, `api/proto/v1/`).

## Next Up

1. Scaffold Protobuf contracts in `api/proto/v1/` (`document_ai.proto`, `common.proto`).
2. Scaffold `internal/config/settings.py` with Pydantic settings.
3. Implement `internal/server/probe_server.py` with FastAPI OpenAPI 3.1 & Prometheus metrics.
4. Implement `internal/temporal/common/heartbeat_runner.py` with `asyncio.to_thread()` isolation.
5. Implement `internal/vision/coordinates.py` for unit coordinate normalization ($[0.0, 1.0]$).

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
