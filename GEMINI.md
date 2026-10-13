# GEMINI.md - Agent Operating Guidelines & Project Context

You are Antigravity / Gemini working on the **`rag-document-ai-worker`** microservice.

---

## 1. Context Hierarchy & References

Before executing any plan, implementing features, or proposing modifications, you **MUST** read and adhere to the project specifications defined in the `.ai/` folder, starting with:

1. [`.ai/agents.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/agents.md) — Core AI agent persona, prompt-planning workflow, and execution rules.
2. [`.ai/doc-api.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/doc-api.md) — The Documented API for the microservice (accessible via `GET /docs` and `GET /api/docs`).
3. [`.ai/project-overview.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/project-overview.md) — Product definition, compute pool decoupling, and SLIs/SLOs.
4. [`.ai/architecture.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/architecture.md) — 5-layer backend architecture, system boundaries, and invariants.
5. [`.ai/code-standards.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/code-standards.md) — Python 3.11+ rules, Ruff linting/formatting/isort, and typing.
6. [`.ai/security.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/security.md) — Threat model, memory boundaries, and secured logging invariants.
7. [`.ai/ai-workflow-rules.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/ai-workflow-rules.md) — Spec-driven implementation rules and pre-commit checks.
8. [`.ai/progress-tracker.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/progress-tracker.md) — Real-time progress, completed items, and next milestones.
9. [`.ai/ui-context.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/ui-context.md) — Headless backend operational presentation & Prometheus telemetry specs.

---

## 2. Mandatory Core Invariants

### A. Continuous Doc API Maintenance & API Accessibility
- **API Accessibility**: The documented API in [`.ai/doc-api.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/doc-api.md) must be accessible via HTTP endpoints hosted on the worker's internal server (`GET /docs` for Markdown/HTML and `GET /api/docs` for JSON).
- **Zero-Drift Synchronization**: The Doc API ([`.ai/doc-api.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/doc-api.md)) **MUST be updated on every code change or addition**, no matter how small the created or modified code is.

### B. Secured Logging
- **Strict PII & Content Redaction**: Document text content, OCR blocks, speech transcripts, customer PII/PHI, presigned URLs, and authentication tokens must **NEVER** appear in log outputs.
- **Structured JSON Logs**: All logging must use structured JSON lines with correlation metadata (`tenant_id`, `document_id`, `task_queue`, `activity_id`, `page_number`).
- **Sanitized Errors**: Exception traces must sanitize underlying file paths and infrastructure credentials before logging or bubbling to Temporal.

### C. Concurrency & GIL-Safe Activity Execution
- Offload PyTorch, Docling, PaddleOCR, and Whisper model inference loops to worker threads using `asyncio.to_thread()`.
- Temporal activity heartbeats must emit uninterrupted every 10 seconds with page/offset progress checkpoints.

### D. Code Quality & Tooling
- **Linter & Formatter**: Use **Ruff** for all linting, formatting, and import sorting:
  ```bash
  python -m ruff check --fix .
  python -m ruff format .
  ```
- **Type Checking**: Strict typing with `mypy`.

---

## 3. Git Commit Standards

- **Git Commit Frequency**: Commit code as you build. Every working session or phase must contain **at least two git commits**.
- **Conventional Commit Patterns**: All git commit messages must strictly adhere to the following prefixes:
  - `feat:` for new features 
  - `fix:` for bug fixes
  - `refactor:` for code refactoring
  - `docs:` for documentation updates
  - `tests:` for tests addition and modification
  - `chore:` for maintenance tasks and environment setup

---

## 4. Pre-Commit Checklist

Before declaring any task or phase complete:
1. `python -m ruff check --fix .` (zero errors or warnings).
2. `python -m ruff format .` (clean formatting).
3. `mypy cmd internal test` (type safety verified).
4. `pytest test/unit test/integration` (all tests passing).
5. [`.ai/doc-api.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/doc-api.md) synchronized with any API, schema, or model changes.
6. [`.ai/progress-tracker.md`](file:///c:/Users/ANIK/Desktop/Gemejime/Document%20AI%20Parsers/.ai/progress-tracker.md) updated with completed progress and session notes.
7. Ensure all commit messages strictly use the required conventional commit prefixes and that at least two commits are made per session.
