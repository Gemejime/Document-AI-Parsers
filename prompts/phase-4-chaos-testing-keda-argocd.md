# Phase 4: Chaos Testing, KEDA Scaling & GitOps ArgoCD Integration - Implementation Plan & Prompt

## 1. Executive Summary & Objective

Phase 4 implements **Production Hardening, Resiliency Chaos Validation, KEDA Task-Queue Scaling, and GitOps ArgoCD Continuous Delivery** for the `rag-document-ai-worker` microservice, strictly adhering to:
- [`tasks/RAG Document AI Worker Implementation Plan.pdf`](../tasks/RAG%20Document%20AI%20Worker%20Implementation%20Plan.pdf) (Pages 5, 8–10)
- [`tasks/Production-Grade Multimodal RAG Architectural Specification.pdf`](../tasks/Production-Grade%20Multimodal%20RAG%20Architectural%20Specification.pdf)
- [`tasks/Go and Python Microservices Architecture.pdf`](../tasks/Go%20and%20Python%20Microservices%20Architecture.pdf)
- [`tasks/Disaggregated Hybrid Model Architecture.pdf`](../tasks/Disaggregated%20Hybrid%20Model%20Architecture.pdf)
- [`tasks/Production Grade Backend Concepts.pdf`](../tasks/Production%20Grade%20Backend%20Concepts.pdf)
- [`GEMINI.md`](../GEMINI.md) & [`.ai/architecture.md`](../.ai/architecture.md)
- [`.ai/ai-workflow-rules.md`](../.ai/ai-workflow-rules.md)
- [`.ai/doc-api.md`](../.ai/doc-api.md)
- [`.ai/security.md`](../.ai/security.md)
- [`.ai/code-standards.md`](../.ai/code-standards.md)
- [`.ai/progress-tracker.md`](../.ai/progress-tracker.md)

---

## 2. Core Deliverables for Phase 4

### A. Production Helm 3 Chart Suite (`deployments/helm/`)
1. **`Chart.yaml`**: Standard Helm 3 chart specification (`apiVersion: v2`, `name: rag-document-ai-worker`, `version: 0.1.0`, `appVersion: "0.1.0"`).
2. **`templates/_helpers.tpl`**: Standardized template helper macros for chart naming, full name expansion, standard Kubernetes labels (`helm.sh/chart`, `app.kubernetes.io/name`, `app.kubernetes.io/instance`, `app.kubernetes.io/component`, `app.kubernetes.io/managed-by`), and selector labels.
3. **`templates/serviceaccount.yaml`**: Dedicated Kubernetes `ServiceAccount` supporting AWS IRSA (`eks.amazonaws.com/role-arn`) / GCP Workload Identity annotations for secure S3 object access.
4. **`templates/deployment.yaml`**:
   - Parameterized deployment template serving both `doc-ai-vision-worker` and `doc-ai-audio-worker` profiles.
   - Resource allocation honoring PDF specs: 1x NVIDIA L4/A10G GPU (`nvidia.com/gpu: 1`), 4,000m request / 8,000m limit CPU, 8 GiB request / 16 GiB limit RAM.
   - Memory and storage isolation:
     - 2 GiB memory-backed `emptyDir` mounted at `/dev/shm` (satisfying IPC and PyTorch DataLoader without host `/dev/shm` leaks).
     - 4 GiB scratch storage mounted at `/tmp`.
     - Read-only persistent volume mount at `/models/cache` referencing NVMe pre-baked weights PVC (`doc-ai-model-weights-nvme-pvc`).
   - Container security context: non-root execution (`runAsUser: 1000`, `runAsGroup: 1000`, `runAsNonRoot: true`), `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, and `drop: [ALL]` capabilities.
   - Environment variables injecting `LD_PRELOAD=/usr/lib/libjemalloc.so`, `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128`, `OMP_NUM_THREADS=2`, `OPENBLAS_NUM_THREADS=2`, and model cache directories (`HF_HOME`, `TORCH_HOME`, `DOCLING_CACHE_DIR`, `WHISPER_CACHE_DIR`).
   - Probes: Liveness & readiness probes wired to port 8080 (`/healthz` and `/ready`).
   - Prometheus scrape annotations on port 8080 (`/metrics`).
5. **`templates/service.yaml`**: ClusterIP service exposing internal probe and Prometheus scrape port 8080.
6. **`templates/pvc.yaml`**: PersistentVolumeClaim definition for NVMe model weights cache (`doc-ai-model-weights-nvme-pvc`).
7. **`templates/keda-scaledobject.yaml`**:
   - KEDA `ScaledObject` (`apiVersion: keda.sh/v1alpha1`) driven by Temporal task queue depth / schedule-to-start latency:
     - `doc-ai-vision-queue`: Target threshold **5** pending tasks/pod, `minReplicaCount: 2`, `maxReplicaCount: 20`, `cooldownPeriod: 300`, `pollingInterval: 15`.
     - `doc-ai-audio-queue`: Target threshold **3** pending tasks/pod, `minReplicaCount: 1`, `maxReplicaCount: 10`, `cooldownPeriod: 300`, `pollingInterval: 15`.
8. **`templates/servicemonitor.yaml`**: Prometheus Operator `ServiceMonitor` resource targeting port 8080 `/metrics` with a 15s scrape interval.
9. **`templates/prometheusrule.yaml`**: Alerting rules conforming to Implementation Plan PDF Page 10:
   - `QueueBacklogGrowth` (Warning): `schedule_to_start` latency on either queue exceeds 45s for >3 minutes.
   - `CudaOOMErrors` (Page): `rate(doc_ai_cuda_oom_errors_total[5m]) > 0`.
   - `ActivityFailuresHigh` (Page): Activity failure rate > 2% across 10 minutes (`rate(doc_ai_activity_failures_total[10m]) / rate(doc_ai_task_duration_seconds_count[10m]) > 0.02`).
10. **Values files**:
    - `values.yaml`: Base shared default configuration.
    - `values-vision.yaml`: Hardened configuration for `doc-ai-vision-worker`.
    - `values-audio.yaml`: Hardened configuration for `doc-ai-audio-worker` (strictly threshold: 3).

### B. GitOps ArgoCD Declarative Delivery Suite (`deployments/argocd/`)
1. **`application-vision.yaml`**: ArgoCD `Application` resource linking to git repository and target Kubernetes namespace `doc-ai`, tracking `deployments/helm` with `values-vision.yaml`.
2. **`application-audio.yaml`**: ArgoCD `Application` resource linking to git repository and target Kubernetes namespace `doc-ai`, tracking `deployments/helm` with `values-audio.yaml`.
3. **`applicationset.yaml`**: ArgoCD `ApplicationSet` orchestrating multi-environment matrix deployments (`staging`, `production`) across decoupled worker clusters with automated drift detection and self-healing (`automated.prune: true`, `automated.selfHeal: true`).
4. **KEDA Autoscaling Drift Immunity (`ignoreDifferences`)**:
   - Applications and ApplicationSet must configure `ignoreDifferences` for `spec.replicas` on `apps/Deployment` to prevent ArgoCD from overwriting KEDA dynamic autoscaling back to static git replica counts:
     ```yaml
     ignoreDifferences:
       - group: apps
         kind: Deployment
         jsonPointers:
           - /spec/replicas
     ```
5. **GitOps CD Pipeline Automation**:
   - GitOps image tag synchronization in `.github/workflows/docker-publish.yml` with `permissions: contents: write` allowing automated commit/PR updating Helm values when release tags (`v*.*.*`) are published to GHCR.

### C. Chaos Testing & Resiliency Verification Suite
1. **Temporal Heartbeat Checkpoint Recovery Test (`test/chaos/test_checkpoint_chaos.py`)**:
   - Simulates forced SIGKILL / process interruption during active processing of vision (page 52) and audio (offset 45s).
   - Validates that upon task restart/reassignment, the worker retrieves `activity.get_heartbeat_details()`, resumes at page 51 / offset 45s, and emits only the delta progress without reprocessing previously completed chunks or corrupting S3 Parquet tables.
   - Verifies transactional S3 Parquet writing idempotency.
2. **VRAM Saturation & Dual-Mode Soak Testing (`test/performance/test_vram_soak.py`)**:
   - *CI Regression Mode*: 50–100 rapid cycles monitoring host-heap memory via `tracemalloc` and `gc.get_objects()`, asserting zero memory drift ($\Delta < 1\%$).
   - *Extended Soak Mode* (`RUN_EXTENDED_SOAK_TEST=true`): 6-hour continuous stress parsing on GPU hardware, validating `cleanup_vram_and_memory()` (`torch.cuda.empty_cache()` + `gc.collect()`) under `jemalloc` with zero CUDA OOM errors.
3. **Telemetry & Alerting Conformance Test (`test/unit/test_telemetry_metrics.py`)**:
   - Asserts all required metrics from PDF Page 9 & 10 are declared, registered, and properly populated:
     - `doc_ai_pages_processed_total`
     - `doc_ai_audio_seconds_processed_total`
     - `doc_ai_task_duration_seconds`
     - `doc_ai_vram_allocated_bytes`
     - `doc_ai_heartbeat_emissions_total`
     - `doc_ai_cuda_oom_errors_total`
     - `doc_ai_heartbeat_delay_seconds`
     - `doc_ai_activity_failures_total`
   - Verifies Prometheus alert expressions evaluate cleanly against registered metrics.

---

## 3. Implementation Plan & Execution Steps

### Step 1: Update Telemetry & Test Discovery
- Register `doc_ai_activity_failures_total` in `internal/server/probe_server.py`.
- Add `"test/chaos"` to `testpaths` in `pyproject.toml`.

### Step 2: Complete Production Helm 3 Chart Suite
- Create `deployments/helm/Chart.yaml` (`apiVersion: v2`)
- Create `deployments/helm/templates/_helpers.tpl`
- Create `deployments/helm/templates/serviceaccount.yaml`
- Create `deployments/helm/templates/deployment.yaml`
- Create `deployments/helm/templates/service.yaml`
- Create `deployments/helm/templates/pvc.yaml`
- Create `deployments/helm/templates/keda-scaledobject.yaml` (Vision threshold: 5, Audio threshold: 3)
- Create `deployments/helm/templates/servicemonitor.yaml`
- Create `deployments/helm/templates/prometheusrule.yaml`
- Create `deployments/helm/values.yaml` (base shared defaults)
- Verify `values-vision.yaml` and `values-audio.yaml` against templates using `helm template` and `yamllint`.

### Step 3: Implement GitOps ArgoCD Manifests & CI Automation
- Create `deployments/argocd/application-vision.yaml` with `ignoreDifferences: [/spec/replicas]`
- Create `deployments/argocd/application-audio.yaml` with `ignoreDifferences: [/spec/replicas]`
- Create `deployments/argocd/applicationset.yaml` with matrix generator and `ignoreDifferences`
- Update `.github/workflows/docker-publish.yml` with `permissions: contents: write` and GitOps tag sync step.

### Step 4: Implement Chaos & Resiliency Test Suites
- Create `test/chaos/test_checkpoint_chaos.py` validating SIGKILL interruption and delta resumption.
- Create `test/performance/test_vram_soak.py` with dual-mode (CI fast regression vs 6-hour soak).
- Create `test/unit/test_telemetry_metrics.py` verifying Prometheus metrics and alerting rules.

### Step 5: Documentation, API Sync & Pre-Commit Verification
- Synchronize `.ai/doc-api.md` with Section 7 (Helm chart parameters, KEDA triggers, Prometheus metrics & alerts).
- Synchronize `.ai/progress-tracker.md` marking Phase 4 progress.
- Run full pre-commit verification:
  1. `python -m ruff check --fix .`
  2. `python -m ruff format .`
  3. `python -m mypy cmd internal test`
  4. `python -m pytest test/unit test/integration test/performance test/chaos`
  5. `helm lint deployments/helm -f deployments/helm/values-vision.yaml`
  6. `helm lint deployments/helm -f deployments/helm/values-audio.yaml`
- Perform Git commits adhering to conventional prefixes and git standards (at least 2 commits per session/phase).
