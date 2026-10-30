# Progress Tracker - RAG Document AI Worker

## Current Phase

- **Phase 4: Chaos Testing, KEDA Scaling & GitOps ArgoCD Integration** (Completed)

## Current Goal

- Production Hardening & Full End-to-End Orchestration Validation.

## Completed

- **CI/CD Secret Injection & Deployment Workflow Hardening**:
  - **CI/CD Secret Injection Workflow Hardening** (`.github/workflows/deploy.yml`):
    - Secured secret injection by sourcing values through step-scoped environment variables and writing to protected temporary files (`chmod 700` dir / `chmod 600` files) with clean EXIT trap removal instead of command-line literal interpolation.
    - Added pre-injection validation halting the workflow if `PROD_HF_TOKEN`, `PROD_AWS_ACCESS_KEY_ID`, or `PROD_AWS_SECRET_ACCESS_KEY` are unset.
    - Aligned deployment names in rollout checks with Helm chart-rendered names: `doc-ai-vision-worker-rag-document-ai-worker` and `doc-ai-audio-worker-rag-document-ai-worker`.
    - Stabilized concurrency group to `deploy-production` preventing race conditions across concurrent ref deployments.
    - Added `workflow_run` trigger for `Publish Production Container Images` and restricted automatic production deployments to approved release branches (`main`, `master`).
    - Removed unsupported staging option from deployment dispatch inputs.
  - **Secret Management Documentation Hardening** (`deployments/SECRET_MANAGEMENT.md`):
    - Replaced machine-specific file URIs with repository-relative links across the specification.
    - Updated `KUBECONFIG` specification to raw YAML format matching `azure/k8s-set-context@v4` requirements.
    - Documented current `S3StorageClient` limitation where operations lack SDK bindings, clarifying that `ServiceAccount` annotations alone do not yet enable IRSA.
    - Corrected drift enforcement statement indicating verification is manually executed via `--check-only` since `.env.prod` is git-ignored and omitted from fresh checkouts.
    - Aligned zero-downtime rotation restart commands with chart-rendered deployment names.

- **Automated Environment Synchronization & Deployment Enforcement Pattern**:
  - **Dynamic ConfigMap Ingestion** (`deployments/helm/templates/configmap.yaml`):
    - Replaced static variable bindings with dynamic range iteration over `.Values.env`.
    - Preserved core service ports (`PORT`, `GRPC_PORT`) with safe defaults (`8080`, `50051`).
    - Guarded against nil values and excluded `secretName` from ConfigMap keys.
  - **Automatic Pod Restart on ConfigMap Change** (`deployments/helm/templates/deployment.yaml`):
    - Added `checksum/config` annotation to `spec.template.metadata.annotations` using `sha256sum` over `configmap.yaml` template content to trigger rolling restarts on config changes.
    - Updated container `envFrom` to reference the generated ConfigMap (`{{ include "rag-document-ai-worker.fullname" . }}-config`) and optional secret (`{{ .Values.env.secretName }}`).
  - **Environment Synchronization & Drift Validation Script** (`scripts/sync_env_to_deployments.py`):
    - Created CLI supporting `--sync` (syncing `.env.prod` to `deployments/helm/values.yaml`) and `--check-only` (exit code 1 on drift, 0 on clean).
    - Automatically excluded sensitive keys (`PASSWORD`, `SECRET`, `TOKEN`, `KEY`).
    - Preserved `secretName` and top-level Helm configurations (`replicaCount`, `image`, `resources`, `keda`, `persistence`, etc.).
    - Handled UTF-8 console output cleanly across Windows shells.
  - **Git Pre-Commit Hook Enforcement** (`.git/hooks/pre-commit`):
    - Enforced `--check-only` validation before git commits to prevent uncommitted drift.
  - **Developer Documentation in `.env.example`** (`.env.example`):
    - Added architectural notice clarifying `.env` is for local development only and `.env.prod` is the single source of truth for `deployments/`.
  - **Comprehensive Test Suite & Quality Verification** (`test/unit/test_sync_env_to_deployments.py`):
    - Added 11 unit tests covering parsing, secret filtering, drift detection, deployment synchronization, and CLI exit codes.
    - Verified clean linting and formatting via Ruff, type safety via Mypy, and zero drift with `--check-only`.

- **Documentation & Environment Reference Hardening**:
  - **Comprehensive Production Architecture Specification** (`architecture.md`):
    - Created dedicated root `architecture.md` focused on `rag-document-ai-worker`, detailing the decoupled worker pool topology (`doc-ai-vision-worker` and `doc-ai-audio-worker`), microservice interaction boundaries (upstream `rag-ingestion-coordinator`, downstream `rag-chunk-lifecycle-service` over gRPC HTTP/2, and Triton `rag-inference-serving`), 5-layer internal architecture, GIL thread isolation with 10s heartbeat checkpoints, end-to-end Mermaid sequence flows, and KEDA queue depth autoscaling.
  - **Comprehensive Production README** (`README.md`):
    - Overhauled `README.md` with full architectural topology, decoupled compute pool definitions, runtime environment variable reference table, HTTP documentation/probe/metrics endpoint reference (`/docs`, `/redoc`, `/api/docs`, `/healthz`, `/ready`, `/metrics`), model weights NVMe PVC caching strategy, secured logging invariants, local development quickstart, and Docker/Helm/ArgoCD deployment guides.
  - **Doc API Synchronization** (`.ai/doc-api.md`):
    - Added Section 8 detailing runtime configuration schema.
- **Code Review Findings Remediation & Hardening**:
  - **Dev Dependencies & TestClient Remediation** (`pyproject.toml`):
    - Removed unsatisfiable `httpx2>=0.1.0` entry from `[project.optional-dependencies] dev` while retaining `httpx>=0.27.0`.
  - **gRPC Chunk Service Client Target Endpoint Resolution** (`internal/transport/grpc/chunk_client.py`, `test/unit/`):
    - Updated `ChunkServiceClient.__init__` to assign `settings.CHUNK_SERVICE_ENDPOINT` strictly when `target_endpoint is None`, preserving explicitly empty strings (`""`) to route properly to mock/offline branches. Added unit test in `test/unit/test_audio_grpc_stream.py`.
  - **Documentation & Metric Contract Alignment** (`architecture.md`, `README.md`):
    - Aligned `TranscribeAudioWindowActivity` JSON schemas in `architecture.md` with `.ai/doc-api.md` (`audio_id`, `audio_hash`, `window_duration_seconds`, `audio_seconds_processed`, `segment_count`, `speaker_count`, `ast_streamed`).
    - Aligned exported Prometheus metric table in `README.md` to reflect runtime names `doc_ai_pages_processed_total` and `doc_ai_task_duration_seconds`.
  - **Setuptools Package Discovery Configuration** (`pyproject.toml`):
    - Configured explicit `[tool.setuptools.packages.find]` specifying `include = ["api*", "cmd*", "internal*"]` and `exclude = ["deployments*", "prompts*", "test*", "tasks*"]` with namespace support, resolving setuptools flat-layout multi-package discovery build failure during `pip install -e ".[dev]"`.
  - **Native Engine Failure Semantics & Resumption Clamping** (`internal/`, `test/`):
    - Updated `DoclingEngine.parse_slice_pages` so native mode strictly validates local PDF file existence (`FileNotFoundError`) and converter readiness (`RuntimeError`), preventing accidental fall-through to synthetic page generation. Synthetic pages are now strictly produced only when `use_native=False`.
    - Updated `WhisperEngine.transcribe_intervals` to clamp resumed segment start times to `start_from_offset` and exclude words starting before `start_from_offset` (`w.start >= start_from_offset`), preserving baseline segment and word timing behavior when starting at offset zero.
    - Added comprehensive unit tests in `test/unit/test_docling_engine.py` and `test/unit/test_whisper_engine.py` verifying error handling, strict lack of fall-through, and boundary clamping.
  - **Docker & Deployment Hardening** (`.dockerignore`, `deployments/`):
    - Added `.env` to `.dockerignore` to prevent local environment file leaks during container builds.
    - Guarded vision and audio Helm values image tag updates in `.github/workflows/docker-publish.yml` with worker-selection conditions (`WORKER`).
    - Aligned PersistentVolumeClaim name across `deployments/argocd/applicationset.yaml` and `deployments/helm/templates/pvc.yaml` directly to `doc-ai-model-weights-nvme-pvc` matching pod volume mounts.
  - **Vision & Audio Engine Checkpoint & Parquet Egress Fidelity** (`internal/`):
    - Updated native resume filters in `WhisperEngine.transcribe_intervals` to skip boundary segments ending exactly at `start_from_offset` and exclude words at or before checkpoint when `start_from_offset > 0.0`.
    - Enhanced `DoclingEngine._parse_with_docling_native` to read actual page dimensions from `doc.pages[page_num].size` with coordinate origin derivation from `box.coord_origin`, and converted native table grids to project `TableData` for table nodes to reach Parquet egress.
  - **Test Suite Resiliency & SLO Alignment** (`test/`):
    - Aligned manifest conversion benchmark in `test/performance/test_manifest_egress_perf.py` to enforce the documented 1.5s SLO.
    - Updated native error propagation tests in `test/unit/test_whisper_engine.py` and `test/unit/test_pyannote_engine.py` to use `monkeypatch` forcing `faster_whisper` and `pyannote.audio` import failures.
  - **Least Privilege Workflow Permissions & Reliable GitOps Push** (`.github/workflows/docker-publish.yml`):
    - Lowered default workflow permissions to `contents: read`.
    - Assigned `packages: write` strictly to the `publish` job and `contents: write` strictly to the `sync-gitops-helm` job.
    - Removed `|| echo` fallback from `git push origin HEAD:main` to prevent silent delivery failures.
    - Hardened `sed` regex pattern (`/^image:/,/^[a-zA-Z]/ s/^([[:space:]]+)tag:[[:space:]].*/\1tag: .../`) to preserve indentation and match only the intended `tag:` key under `image:`.
  - **GitOps Resource Contention Prevention** (`deployments/argocd/applicationset.yaml`):
    - Configured distinct target namespaces and explicit PVC names for ApplicationSet generated applications, eliminating resource collision with standalone applications targeting `doc-ai`.
  - **Helm Manifest Linting & Raw Template Exclusion** (`.yamllint.yaml`, `.github/workflows/ci.yml`):
    - Added root `.yamllint.yaml` with raw template exclusions (`deployments/helm/templates/`).
    - Updated `helm-lint` CI step to lint raw Helm values and rendered Helm templates (`helm template | yamllint`).
  - **Helm Chart Parameterization & Template Precision** (`deployments/helm/`):
    - Filtered `QueueBacklogGrowth` alert expression by release `task_queue` (`temporal_activity_schedule_to_start_seconds{task_queue="..."}`).
    - Exposed `taskQueue` key in `values.yaml`, `values-vision.yaml`, and `values-audio.yaml`.
    - Prefixed PVC claim name with release fullname to prevent claim collisions between releases in the same namespace.
    - Rendered `.Values.serviceAccount.automountServiceAccountToken` directly without `default true` filter to honor explicit `false`.
    - Updated default `persistence.existingClaim: true` in `values.yaml` to prevent invalid `ReadOnlyMany` empty volume provisioning on shared NVMe storage.
  - **Test Suite Precision & Chart Validation Alignment** (`test/`):
    - Aligned VRAM soak test drift threshold to $< 1\%$ (`drift_ratio < 0.01`) in `test/performance/test_vram_soak.py`.
    - Enhanced checkpoint chaos assertion in `test/chaos/test_checkpoint_chaos.py` to assert delta telemetry increment ($75.0$s) and verify that all resumed audio segments start at or after the $45$s offset.
    - Updated `test/unit/test_telemetry_metrics.py` to dynamically parse alert expressions from `deployments/helm/templates/prometheusrule.yaml` and validate registered metrics against active Helm rules to catch chart-test drift.

- **Phase 4: Chaos Testing, KEDA Scaling & GitOps ArgoCD Integration (Completed)**:
  - **Production Helm 3 Chart Suite** (`deployments/helm/`):
    - `Chart.yaml`: Helm 3 specification for `rag-document-ai-worker` (`v0.1.0`).
    - `templates/_helpers.tpl`: Standardized macro labels (`helm.sh/chart`, `app.kubernetes.io/name`, `app.kubernetes.io/instance`, `app.kubernetes.io/component`, `app.kubernetes.io/managed-by`).
    - `templates/serviceaccount.yaml`: Dedicated Kubernetes `ServiceAccount` with AWS IRSA / GCP Workload Identity annotation support.
    - `templates/deployment.yaml`: Parameterized workload deployment for `doc-ai-vision-worker` and `doc-ai-audio-worker` profiles with 1x NVIDIA GPU, 2 GiB memory `/dev/shm` emptyDir, 4 GiB `/tmp`, NVMe model weights cache, non-root security context (`runAsUser: 1000`, `readOnlyRootFilesystem: true`, `drop: [ALL]`), and jemalloc memory tuning.
    - `templates/service.yaml`: ClusterIP service exposing probe and Prometheus scrape port 8080.
    - `templates/pvc.yaml`: PersistentVolumeClaim for NVMe model weights (`doc-ai-model-weights-nvme-pvc`).
    - `templates/keda-scaledobject.yaml`: KEDA `ScaledObject` driven by Temporal queue depth (`doc-ai-vision-queue` threshold: 5, `doc-ai-audio-queue` threshold: 3).
    - `templates/servicemonitor.yaml`: Prometheus Operator `ServiceMonitor` targeting `/metrics` at 15s scrape interval.
    - `templates/prometheusrule.yaml`: Prometheus alerting rules (`QueueBacklogGrowth`, `CudaOOMErrors`, `ActivityFailuresHigh`).
    - Values configurations: `values.yaml` (base shared defaults), `values-vision.yaml`, and `values-audio.yaml`.
  - **GitOps ArgoCD Declarative Delivery** (`deployments/argocd/`):
    - `application-vision.yaml`: ArgoCD Application targeting `doc-ai` namespace with `values-vision.yaml`.
    - `application-audio.yaml`: ArgoCD Application targeting `doc-ai` namespace with `values-audio.yaml`.
    - `applicationset.yaml`: Multi-environment matrix generator (`staging`, `production`) across decoupled worker clusters.
    - **KEDA Drift Immunity**: Configured `ignoreDifferences` for `/spec/replicas` preventing ArgoCD from overwriting dynamic KEDA autoscaling.
    - **GitOps Release Tag Sync**: Updated `.github/workflows/docker-publish.yml` with `permissions: contents: write` and automated Helm image tag synchronization on release tags (`v*.*.*`).
  - **Chaos Testing & Resiliency Verification Suite** (`test/chaos/`, `test/performance/`, `test/unit/`):
    - `test/chaos/test_checkpoint_chaos.py`: Simulates abrupt SIGKILL interruption at vision page 52 and audio offset 45s, asserting exact delta recovery (49 pages remaining and 75s audio remaining) and transactional S3 Parquet writing idempotency.
    - `test/performance/test_vram_soak.py`: Dual-mode soak test suite featuring CI regression memory drift tracking (< 1% delta via `tracemalloc` and `gc.get_objects()`) and extended 6-hour GPU soak test.
    - `test/unit/test_telemetry_metrics.py`: Asserts all 8 mandatory Prometheus metrics and alert expressions conform to specifications.
    - Telemetry extension: Registered `doc_ai_activity_failures_total` counter in `internal/server/probe_server.py`.

- **Phase 3: Dedicated Audio Pipeline & Speaker Diarization (Completed)**:
  - **Audio Models & Data Structures** (`internal/audio/models.py`): Defined `WordTiming`, `RawWhisperSegment`, `SpeakerTurn`, and `AlignedTranscriptSegment` maintaining strict absolute timeline mapping.
  - **Faster-Whisper Large-v3 Speech-to-Text Engine** (`internal/audio/asr/whisper_engine.py`): High-throughput CTranslate2 runner with word-level timestamps, cooperative cancellation (`threading.Event`), progressive interval yielding, mid-window offset resumption skipping audio prior to `start_from_offset`, and native/mock fallback execution.
  - **PyAnnote 3.1 Speaker Diarization Adapter** (`internal/audio/diarization/pyannote_engine.py`): Voiceprint clustering engine with Hugging Face Hub token authentication (`settings.HF_TOKEN`), caching in `settings.HF_HOME`, speaker count constraints (`min_speakers`, `max_speakers`), and cooperative cancellation.
  - **Timeline Alignment & Absolute Timeline Engine** (`internal/audio/alignment.py`): Word-to-speaker turn temporal overlap assignment with silence gap fallback (preceding turn or `SPEAKER_00`), grouping consecutive words by speaker and silence threshold ($1.5$s), and projecting all word and segment timestamps to the global media timeline (ADR-009).
  - **Streaming gRPC Audio Transport Client** (`internal/transport/grpc/chunk_client.py`): Implemented `stream_audio_transcript` adhering to `DocumentAIChunkService.StreamAudioTranscript` with Protobuf `AudioTranscriptChunk`, `TranscriptSegment`, and `WordTimestamp` serialization, batched streaming, server acknowledgment validation, and resilient offline fallback.
  - **Audio VRAM & Host Memory Lifecycle Protocol** (`internal/audio/memory.py`): `cleanup_vram_and_memory()` flushing `torch.cuda.empty_cache()` and `gc.collect()` after each window execution.
  - **Temporal Activity Integration** (`internal/temporal/activities/audio_activity.py`): Production `TranscribeAudioWindowActivity` with physical WAV container & header validation (`wave` RIFF inspection), non-retryable fail-fast quarantine (`CORRUPT_INPUT_QUARANTINE`), mid-window checkpoint recovery, thread-isolated execution via `asyncio.to_thread()`, 10s heartbeats via `HeartbeatRunner`, gRPC streaming, and Prometheus delta accounting ($\Delta = \text{processed} - \text{start\_from\_offset}$).
  - **Comprehensive Verification Suite** (`test/unit/`, `test/integration/`, `test/performance/`): Added 12 new tests covering Whisper transcription, PyAnnote diarization, timeline alignment, absolute timeline projections, gRPC streaming, end-to-end activity execution, mid-window checkpoint recovery, physical corruption quarantine, and Real-Time Factor performance (RTF $\le 0.15$).

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

- **Phase 1: Worker Decoupling & Runtimes (Completed)**:
  - **Core Settings & Secured Logging** (`internal/config/settings.py`): Pydantic `WorkerSettings` reading environment variables, model cache directories, structured JSON logging via `structlog`, and security filters redacting tokens, keys, presigned URLs, and document/transcript text.
  - **FastAPI Operational Probe Server** (`internal/server/probe_server.py`): Interactive OpenAPI 3.1 (`/docs`, `/redoc`), schema specs (`/api/docs`, `/openapi.json`), health probes (`/healthz`, `/ready` tied to worker readiness), and Prometheus metrics (`/metrics`).
  - **Coordinate Normalization Math** (`internal/vision/coordinates.py`): Normalized bounding boxes strictly bounded to $[0.0, 1.0]$ with top-left vs bottom-left origin adapters (handling PostScript/PDF coordinate flipping), clamping, and validation.
  - **Storage & Transport Protocols** (`internal/storage/s3_client.py`, `internal/transport/grpc/chunk_client.py`): Asynchronous client protocols and stubs for S3 slice reads/Parquet writes and gRPC AST streaming.
  - **Thread-Isolated Heartbeat Runner & Cooperative Cancellation** (`internal/temporal/common/heartbeat_runner.py`): `HeartbeatRunner` with `ProgressTracker`, non-blocking 10s interval emissions, latency telemetry, and `threading.Event` cooperative abort.
  - **Temporal Activities Framework** (`internal/temporal/activities/vision_activity.py`, `audio_activity.py`): `ParseDocumentSliceActivity` and `TranscribeAudioWindowActivity` supporting checkpoint resumption (recovering from previous page/offset) and non-retryable fail-fast quarantine (`CORRUPT_INPUT_QUARANTINE`).
  - **Worker Bootstrap Entry Points** (`cmd/vision_worker/main.py`, `cmd/audio_worker/main.py`): Co-hosting Temporal Worker and Uvicorn probe server concurrently in the main asyncio event loop via `asyncio.gather()`.
  - **Containerization & Helm Deployments** (`deployments/docker/Dockerfile.vision`, `Dockerfile.audio`, `deployments/helm/values-vision.yaml`, `values-audio.yaml`): Multi-stage CUDA 12.2 images with `libjemalloc2`, model cache env vars, non-root UID 1000, read-only rootfs, 2 GiB `/dev/shm`, and KEDA `ScaledObject` queue depth triggers.
  - **Comprehensive Verification Suite** (`test/unit/`, `test/integration/`): 24 unit and integration tests passing covering coordinate math, settings redaction, probe endpoints, GIL thread isolation, and Temporal checkpoint resumption.

- **Phase 2: Vision & Tabular Pipeline (Completed)**:
  - **Docling Layout Parser & Engine Adapter** (`internal/vision/layout/docling_engine.py`): Structured AST node decomposition (`heading_h1`..`h6`, `paragraph`, `table`, `list`), bounding boxes normalized to $[0.0, 1.0]$, mid-slice recovery skipping pages $< \text{start\_from\_page}$, single-pass item grouping by page, and cooperative cancellation.
  - **Secondary Fallback OCR Pass** (`internal/vision/ocr/fallback_ocr.py`): Multilingual fallback with PaddleOCR triggered when node confidence $< \text{settings.OCR\_CONFIDENCE\_THRESHOLD}$ (bounded to $[0.0, 1.0]$), preserving unmodified nodes when OCR pass does not run.
  - **Tabular Dataset Extraction & PyArrow Parquet S3 Writer** (`internal/vision/table/`): `TableData` formatting GFM Markdown, canonical SHA-256 table hash calculation, PyArrow Table conversion with duplicate column name deduplication (`_{i}`), and `ParquetS3Writer` persisting directly to S3 with configurable codecs (`settings.TABLE_PARQUET_COMPRESSION`).
  - **Streaming gRPC AST Manifest Egress** (`internal/transport/grpc/chunk_client.py`): StreamDocumentAST client streaming chunks to `rag-chunk-lifecycle-service` with error propagation for Temporal activity retries.

## In Progress

- Production Hardening & Full End-to-End Orchestration Validation (Phase 3 Audio and Phase 4 GitOps / Chaos suites verified).

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
- Code review feedback addressed: standardized portable relative documentation paths across `.ai.md`, `GEMINI.md`, and `.ai/ai-workflow-rules.md`; clarified host-heap memory role of `jemalloc`; scoped container capabilities and read-only rootfs mounts in `.ai/security.md`; locked `/api/docs` to raw OpenAPI 3.1 schema; standardized pytest testpaths to `test/unit` and `test/integration`.
- Implemented and verified complete GitHub Actions CI/CD automation suite:
  - `.github/workflows/ci.yml`: Ruff lint/format checks, strict Mypy type validation, Pytest unit/integration test suite with coverage upload, Protobuf v1 syntax & schema validation, Helm values YAML linting, Gitleaks security scanning, and multi-stage Dockerfile smoke builds.
  - `.github/workflows/docker-publish.yml`: Multi-image matrix build and publish for `doc-ai-vision-worker` and `doc-ai-audio-worker` to GitHub Container Registry (`ghcr.io`) with Docker Buildx and GitHub Actions layer caching.
  - `Makefile`: Standardized automation entry points (`make lint`, `make format`, `make typecheck`, `make test`, `make proto`, `make check`).
  - `api/proto/v1/`: Implemented Protobuf v3 contracts (`common.proto` and `document_ai.proto`) for normalized bounding boxes and gRPC streaming to `rag-chunk-lifecycle-service`.
- **Code Review Hardening (Audio Pipeline & Resumption Lifecycle)**:
  - `internal/audio/alignment.py`: Simplified word timestamp calculation to project directly from `start_offset_seconds` and `w.start_time`/`w.end_time`, eliminating double-offset adjustments.
  - `internal/audio/asr/whisper_engine.py` & `internal/audio/diarization/pyannote_engine.py`: Updated native transcription, diarization, and model-loading error handling to re-raise errors when `use_native=True`, restricting simulated mode to `use_native=False`.
  - `internal/temporal/activities/audio_activity.py`:
    - Replaced shared scratch path with unique `tempfile.mkstemp` and guaranteed cleanup in a `finally` block.
    - Removed mock WAV payload fabrication in activity runtime; missing audio files now raise retryable `ApplicationError("FILE_NOT_FOUND")`.
    - Enforced cooperative cancellation: raises `ApplicationError`/`asyncio.CancelledError` when `stop_event` is set rather than returning partial success.
    - Enhanced checkpoint resumption: persisted processed segments in `AudioHeartbeatDetails` and replayed on resume so retries deliver full transcripts without loss.
  - `internal/transport/grpc/chunk_client.py`: Verified exception propagation in `stream_audio_transcript` (already raising errors on RPC/stream failure).
  - Synchronized `.ai/doc-api.md` with updated `AudioHeartbeatDetails` schema.
  - Resolved CI pytest collection failure by adding `httpx>=0.27.0` and `httpx2>=0.1.0` to `[project.optional-dependencies] dev` in `pyproject.toml` to support Starlette's `TestClient`.
  - Configured `GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}` in `.github/workflows/ci.yml` secret-scan step to comply with Gitleaks Action v2 licensing requirement for the `Gemejime` GitHub Organization.
- **Production Environment Configuration & Hardening**:
  - Analyzed and synthesized all environment variables required for running `rag-document-ai-worker` in production, creating comprehensive `.env.example` template with security and hardware allocator invariants (`jemalloc`, `PYTORCH_CUDA_ALLOC_CONF`, thread pinning).
  - Explicitly integrated `CHUNK_SERVICE_ENDPOINT` into `WorkerSettings` (`internal/config/settings.py`) and wired as default target endpoint in `ChunkServiceClient` (`internal/transport/grpc/chunk_client.py`).
  - Patched `activity.heartbeat` in chaos recovery test suite (`test/chaos/test_checkpoint_chaos.py`) ensuring clean long-duration activity tests without activity context errors.
  - Fully synchronized `.ai/doc-api.md` Section 8 and `README.md` key environment variable reference tables.
- **Main Branch Merge & Conflict Resolution**:
  - Merged latest updates from `origin/main` (commit `284661f`: `.github/workflows` cleanup delegating to `rag-document-ai-worker`).
  - Resolved modify/delete conflicts for `.github/workflows/ci.yml` and `.github/workflows/docker-publish.yml` by preserving the updated CI/CD definitions from the feature branch.
  - Verified pre-commit validation checklist: Ruff lint/format clean, Mypy strict type checking clean (65 files), and 72/72 unit and integration tests passing.
- **Production Environment Variables & Helm `.env` Compatibility**:
  - Expanded and annotated `.env.example` with comprehensive documentation and production instructions, explicitly highlighting affected variables (`TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `CHUNK_SERVICE_ENDPOINT`, `HF_TOKEN`, `AWS_REGION`, `AWS_ENDPOINT_URL_S3`, `PPOCR_HOME`).
  - Added `envFrom` support to Helm deployment template (`deployments/helm/templates/deployment.yaml`) allowing pods to directly source environment variables from `.env`-backed Kubernetes Secrets and ConfigMaps.
  - Synchronized `values.yaml`, `values-vision.yaml`, and `values-audio.yaml` with missing variables (`CHUNK_SERVICE_ENDPOINT`, `AWS_REGION`, `HF_TOKEN`, `PPOCR_HOME`) and `envFrom` stubs.
  - Added `PPOCR_HOME` to `WorkerSettings` and propagated it alongside `AWS_REGION` and `AWS_ENDPOINT_URL_S3` to `os.environ` in `internal/config/settings.py`.
  - Updated `.env.prod` with corrected Kubernetes service FQDN for chunk service (`rag-chunk-lifecycle-service.default.svc.cluster.local:50051`).
  - Synchronized `.ai/doc-api.md` Section 8 to maintain zero-drift specification.
- **Review Finding Verification & Quality Hardening**:
  - `test/unit/test_settings.py`: Retained `_env_file=None` in `test_worker_settings_defaults()` to guard against unintended `.env` file ingestion during default checks, while adding a documented `# type: ignore[call-arg]` suppression to satisfy strict Mypy against `pydantic-settings` runtime constructor keywords.
  - `deployments/helm/values-audio.yaml`: Removed explicit empty `HF_TOKEN` as well as redundant `CHUNK_SERVICE_ENDPOINT` and `AWS_REGION` entries under `env:` so that values injected via `envFrom` (or falling back to `WorkerSettings` defaults) are not overridden.
  - Evaluated PaddleOCR review finding against `internal/config/settings.py`: Skipped modifications since PaddleOCR is configured via `PPOCR_HOME` environment variable (set to `/tmp/.paddleocr` in `configure_logging()`) and explicit directory parameters would disrupt PaddleOCR's automated model download routines.
  - Verified 100% clean check suite: Ruff lint/format clean, strict Mypy zero errors (65 files), and all 72/72 unit and integration tests passing.
- **S3 AWS Credentials Support (`AWS_ACCESS_KEY_ID` & `AWS_SECRET_ACCESS_KEY`)**:
  - Added `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` to `WorkerSettings` (`internal/config/settings.py`) with environment propagation in `configure_logging()`.
  - Added sensitive key scrubbing pattern `access[_-]?key` to `secured_logging_filter` to ensure credential masking in JSON structured logs.
  - Added `aws_access_key_id` and `aws_secret_access_key` parameters and `get_client_kwargs()` helper to `S3StorageClient` (`internal/storage/s3_client.py`).
  - Added unit test coverage in `test/unit/test_settings.py` verifying defaults, environment overrides, log redaction, and `S3StorageClient` client kwargs.
  - Synchronized `.ai/doc-api.md` Section 8 and `README.md` key environment variable reference tables.
- **Review Finding Hardening & Test Isolation**:
  - `.env.example`: Unset space-filled values for `AWS_ENDPOINT_URL_S3`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` (setting to empty strings) and removed redundant `S3_ENDPOINT_URL`.
  - `test/unit/test_settings.py`: Added `monkeypatch` fixture to `test_worker_settings_defaults()` to ensure tests pass independent of the host runner's environment variables.
  - `test/unit/test_settings.py`: Wrapped `configure_logging(cfg)` in `patch.dict(os.environ)` to prevent environment variable mutations from leaking between test cases.

