# AI Workflow Rules - RAG Document AI Worker

## Development Approach

Build this microservice incrementally following a spec-driven workflow:
- The specification documents in `tasks/` and context files in `.ai/` define system invariants, data structures, and operational boundaries.
- Adhere strictly to the decoupled worker design (`doc-ai-vision-worker` and `doc-ai-audio-worker`) and the 5 backend architectural layers.
- Implement against these specs without inferring undocumented dependencies or deviating from architectural decision records (ADRs).

---

## Human-in-the-Loop Rules

- **Plan First**: Propose an implementation plan or prompt before making code changes, and await confirmation.
- **Ambiguity Escalation**: If GPU resource limits, queue configurations, or schema mappings are ambiguous, prompt for clarification before proceeding.
- **Demonstrate & Verify**: Provide clear verification steps, unit test output, and linting checks after completing a unit.

---

## Non-Negotiable Invariants

1. **Doc API Updates**: The `.ai/doc-api.md` file MUST be updated on **every code change or addition**, regardless of how small the modification is.
2. **Secured Logging**: All logs emitted by workers or utilities must be structured JSON logs with sensitive tokens, PII, presigned URLs, and document text redacted.
3. **GIL Isolation**: Blocking model inference loops must be isolated in worker threads (`asyncio.to_thread()`), ensuring Temporal activity heartbeats emit every 10 seconds.
4. **Git Commit Standards**:
   - **Git Commit Frequency**: Commit code as you build. Every working session or phase must contain **at least two git commits**.
   - **Conventional Commit Patterns**: All git commit messages must strictly adhere to the following prefixes:
     - `feat:` for new features 
     - `fix:` for bug fixes
     - `refactor:` for code refactoring
     - `docs:` for documentation updates
     - `tests:` for tests addition and modification
     - `chore:` for maintenance tasks and environment setup
5. **Quality Gates**: Ruff linting, formatting, and import sorting must be passing before committing.

---

## Pre-Commit Verification Checklist

Before committing code, submitting changes, or marking a task complete:

1. **Lint & Format**: Run `python -m ruff check --fix .` and `python -m ruff format .` (with import sorting verified).
2. **Type Check**: Verify types pass `mypy` without errors.
3. **Tests**: Ensure unit and integration tests are passing in `test/unit/` and `test/integration/`.
4. **Secured Logging Audit**: Verify no raw PII or secret tokens are leaked in logging statements.
5. **Doc API Sync**: Confirm `.ai/doc-api.md` reflects any new or modified classes, parameters, endpoints, or storage keys.
6. **Progress Tracker**: Update `.ai/progress-tracker.md` with session notes and completed items.
