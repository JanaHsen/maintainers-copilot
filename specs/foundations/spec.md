# Feature Specification: Day 1 Foundations — Infrastructure, Health, Dataset Pipeline

**Feature Branch**: `foundations`

**Created**: 2026-05-18

**Status**: Draft

**Input**: User description: "Day 1 of the Maintainer's Copilot. Scope: foundations only. Stack comes up healthy via docker-compose, /health reports upstream dependency status, api refuses to boot without Vault, tracing is scaffolded. An offline data pipeline produces a versioned, stratified, time-ordered dataset in MinIO from scikit-learn/scikit-learn closed issues. A Colab notebook kicks off DistilBERT fine-tuning. DECISIONS.md, ARCH.md, RUNBOOK.md are created."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-command healthy stack with honest health reporting (Priority: P1)

A developer clones the repository, copies `.env.example` to `.env`, fills in
the Vault root token, and runs the single bring-up command. Every
infrastructure dependency reaches a healthy state and the application exposes
a health endpoint that returns success with a body enumerating each upstream
dependency and whether it is reachable. The application refuses to start —
exiting with a non-zero status and a clear log line — when the secrets store
is unreachable. A single request to the health endpoint produces a visible,
connected span tree in the tracing backend.

**Why this priority**: This is the foundation every later day builds on. With
nothing else done, a reviewer can still clone, bring the stack up, and
confirm the system is honest about its own state — a viable, demonstrable
slice on its own.

**Independent Test**: From a clean clone, perform the documented env setup and
bring-up; observe every infrastructure container report healthy, the migrate
step apply the baseline schema change and exit cleanly, the health endpoint
return success with per-dependency status, the application refuse to boot
when the secrets store is down, and the health request appear as a span tree
in the tracing backend.

**Acceptance Scenarios**:

1. **Given** a clean clone with `.env` created from `.env.example` and a valid secrets-store root token, **When** the developer runs the documented bring-up command, **Then** the relational+vector store, the ephemeral store, the blob store, and the secrets store each reach a healthy state.
2. **Given** the infrastructure dependencies are healthy, **When** the schema-migration step runs, **Then** it applies the baseline migration and exits with a success (zero) status.
3. **Given** the stack is up, **When** a client requests the health endpoint, **Then** the response is a success status with a structured body listing each upstream dependency and its individual reachable/unreachable status.
4. **Given** the secrets store is unreachable at startup, **When** the application container starts, **Then** it exits with a non-zero status and emits a clear, specific log line naming the secrets store as the cause.
5. **Given** the stack is up and tracing is configured, **When** a single health request is made, **Then** the tracing backend shows a connected span tree attributable to that request.

---

### User Story 2 - Offline dataset pipeline produces a versioned, time-ordered dataset (Priority: P2)

A developer runs the offline data pipeline. It fetches closed issues from the
chosen public source repository, stores the raw payloads verbatim in the blob
store under a versioned prefix, applies a committed label-to-class mapping
that reduces source labels to exactly one of four classes, and produces a
stratified train/validation/test split in which the test set is strictly more
recent in time than the train and validation sets. A machine-readable splits
report recording counts per split and per class is written to the blob store.

**Why this priority**: Days 2 and 3 cannot start without this dataset. It is
independent of the running application — it can be executed and verified on
its own — but it depends on the blob store from Story 1 being available.

**Independent Test**: Run the pipeline against the source repository; verify
raw payloads land under a versioned prefix in the blob store, the committed
mapping file produces only the four allowed classes, the test split's
earliest item is newer than the latest train/validation item, and a splits
report with per-split and per-class counts exists in the blob store.

**Acceptance Scenarios**:

1. **Given** access to the source repository's public issue history, **When** the pipeline runs, **Then** raw closed-issue payloads are persisted verbatim as line-delimited records in the blob store under a versioned prefix.
2. **Given** raw payloads and the committed label-mapping file, **When** the mapping is applied, **Then** every retained, labeled issue is assigned exactly one of the four classes and unmappable issues are handled by a documented rule.
3. **Given** the mapped dataset, **When** the split is produced, **Then** the split is stratified by class and the earliest timestamp in the test set is strictly later than the latest timestamp in the train and validation sets.
4. **Given** a completed split, **When** the pipeline finishes, **Then** a splits report containing counts per split and per class is written to the blob store.
5. **Given** a previous pipeline run exists, **When** the pipeline is re-run, **Then** a new versioned prefix is used so prior runs remain intact.

---

### User Story 3 - Reproducible fine-tuning notebook (Priority: P3)

A developer opens the provided notebook in a hosted GPU environment and runs
it. The notebook trains a transformer classifier on the train split, and
persists the run configuration, the training loss curve, and the final model
weights back to the blob store. The resulting classifier artifact is not
consumed today; a later day picks it up.

**Why this priority**: It produces the artifact a later day depends on but is
not required for today's stack or dataset to be demonstrable. It depends on
Story 2's train split existing.

**Independent Test**: Open the notebook, point it at the train split, run it,
and confirm the run configuration, a loss curve, and final weights are
written to the blob store; confirm no later-day component is required for
this to succeed.

**Acceptance Scenarios**:

1. **Given** a train split exists in the blob store, **When** the notebook is run end to end, **Then** it fine-tunes a transformer classifier on that split.
2. **Given** a completed training run, **When** the notebook finishes, **Then** the run configuration, the training loss curve, and the final weights are persisted to the blob store.
3. **Given** the notebook completed, **When** today's deliverables are reviewed, **Then** the classifier artifact is present but not wired into the running application.

---

### User Story 4 - Decision, architecture, and runbook documentation (Priority: P2)

A reviewer opens the repository's top-level documentation and finds a
decisions log with the first entries justified, an architecture description
with a diagram, and a runbook listing the exact commands to bring the stack
up, tear it down, and re-run the dataset pipeline.

**Why this priority**: The project's governance requires architectural
choices to be recorded with justification, and a reviewer must be able to
operate the stack from written instructions. It is independently verifiable
by reading the three files.

**Independent Test**: Open the three documentation files and confirm the
decisions log contains the dataset choice, the label mapping rationale, the
split sizes, and the tracing backend choice each with a one-line
justification; the architecture file contains a layered description and a
diagram; and the runbook's commands, when followed, bring the stack up, tear
it down, and re-run the pipeline.

**Acceptance Scenarios**:

1. **Given** the repository, **When** the decisions log is opened, **Then** it contains entries for the dataset choice, the label mapping and its rationale, the train/validation/test split sizes, and the tracing backend choice, each with a one-line justification.
2. **Given** the repository, **When** the architecture file is opened, **Then** it contains a short layered-architecture description and a diagram.
3. **Given** the repository, **When** the runbook is opened and followed, **Then** the documented commands successfully bring the stack up, tear it down, and re-run the dataset pipeline.

---
### User Story 5 - Continuous integration gates from the first push (Priority: P2)

Every push to any branch triggers an automated pipeline that lints, type-checks, builds the project's images, runs the (today: stubbed) eval suites and redaction test, and smoke-tests the stack by bringing it up, hitting the health endpoint, and tearing it down. Any failure blocks merge.

**Why this priority**: Governance Rule 10 mandates these gates from the first commit, not retrofitted. The eval suites and redaction test are stubbed today (Days 2 and 3 fill them in) but the pipeline definition itself must exist and pass.

**Independent Test**: Push a commit to a feature branch; observe the CI run lint, type-check, build images, execute the stubbed eval suites and redaction test, run the stack smoke test, and report green; introduce a deliberate lint error and confirm CI fails the push.

**Acceptance Scenarios**:

1. **Given** a push to any branch, **When** CI runs, **Then** it executes lint, type-check, image build, stubbed eval suites, stubbed redaction test, and a stack smoke test in that order.
2. **Given** any one CI step fails, **When** the developer attempts a merge, **Then** the merge is blocked.
3. **Given** the eval suites are stubbed, **When** CI runs, **Then** the stubbed suites exist and pass, and the threshold file `eval_thresholds.yaml` exists with non-zero placeholder thresholds (so Rule 4's refuse-to-boot check on disabled thresholds is satisfiable).

---
### Edge Cases

- The secrets-store root token in `.env` is blank or invalid → the application MUST refuse to boot with a clear log line, not start in a degraded state.
- A single upstream dependency (e.g., the ephemeral store) is down while others are healthy → the health endpoint MUST still respond and report that specific dependency as unreachable rather than failing entirely.
- The source-repository issue API enforces rate limits or paginates → the pipeline MUST handle pagination and rate limiting without producing a partial dataset silently; an incomplete fetch MUST be detectable.
- An issue carries multiple source labels that map to different classes, or no mappable label at all → the mapping MUST apply a documented, deterministic resolution rule (e.g., precedence order; exclude-if-unmappable).
- Too few issues exist in a class to stratify across three splits → the pipeline MUST surface this rather than produce an empty class slice silently.
- The pipeline is re-run → it MUST NOT overwrite a prior versioned run's raw payloads.
- The migrate step is run twice → it MUST be idempotent (already-applied migration is a no-op success, not an error).
- The tracing backend is misconfigured → consistent with the refuse-to-boot principle, this MUST be treated as a startup failure rather than silently dropping traces.

## Requirements *(mandatory)*

### Functional Requirements

**Infrastructure & bring-up**

- **FR-001**: The system MUST come up from a clean clone via a single documented bring-up command after copying `.env.example` to `.env` and supplying the secrets-store root token.
- **FR-002**: The `.env.example` MUST contain only the secrets-store root token placeholder and port numbers — no application secrets.
- **FR-003**: A relational+vector store, an ephemeral store, a blob store, and a secrets store (in development mode) MUST each be orchestrated and MUST each reach a healthy state on bring-up.
- **FR-004**: A schema-migration step MUST apply a baseline migration and then exit cleanly with a success status; re-running it MUST be idempotent.
- **FR-005**: The application MUST start only after its infrastructure dependencies are healthy and expose a health endpoint.

**Health & boot discipline**

- **FR-006**: The health endpoint MUST return a success status with a structured body that lists each upstream dependency and its individual reachable/unreachable status.
- **FR-007**: The health endpoint MUST remain responsive and report per-dependency status even when one or more non-secrets dependencies are unreachable.
- **FR-008**: The application MUST refuse to start — exit non-zero with a clear, specific log line — when the secrets store is unreachable.
- **FR-009**: Every application secret MUST be resolved from the secrets store at startup; `.env` MUST NOT carry application secrets (governance Rule 2).

**Observability**

- **FR-010**: Distributed tracing MUST be scaffolded from this first feature such that a single health request produces a connected span tree in the chosen tracing backend (governance Rule 7).
- **FR-011**: Each request MUST be associated with a trace identifier and a request identifier.

**Offline dataset pipeline**

- **FR-012**: The pipeline MUST fetch closed issues from the chosen public source repository via its issue API, handling pagination and rate limiting without silently producing a partial dataset.
- **FR-013**: Raw issue payloads MUST be persisted verbatim as line-delimited records in the blob store under a versioned prefix; re-runs MUST use a new version and MUST NOT overwrite prior runs.
- **FR-014**: A committed mapping file MUST define how source labels reduce to exactly one of four classes: bug, feature, docs, question.
- **FR-015**: The mapping MUST be applied deterministically, with a documented rule for issues that are unmappable or map ambiguously.
- **FR-016**: The pipeline MUST produce a stratified train/validation/test split in which the test set is strictly more recent in time than the train and validation sets.
- **FR-017**: A machine-readable splits report containing counts per split and per class MUST be written to the blob store.

**Fine-tuning notebook**

- **FR-018**: A notebook in the repository MUST fine-tune a transformer classifier on the train split when run in a hosted GPU environment.
- **FR-019**: The notebook MUST persist the run configuration, the training loss curve, and the final weights to the blob store.
- **FR-020**: The resulting classifier artifact is explicitly out of scope for consumption today and MUST NOT be wired into the running application.


**Continuous integration**

- **FR-024**: A CI workflow definition MUST exist in `.github/workflows/` and MUST run on every push to any branch (governance Rule 10).
- **FR-025**: The workflow MUST execute, in order: lint, type-check on the application source, build of each image declared in the orchestration file, the stubbed classification and RAG eval suites, the stubbed redaction test, and a smoke test that brings the stack up, hits the health endpoint, and tears down.
- **FR-026**: Any step failing MUST block merge.
- **FR-027**: `eval_thresholds.yaml` MUST exist with non-zero placeholder thresholds for both eval suites so the refuse-to-boot check (governance Rule 4) is satisfiable from Day 1.

**Documentation**

- **FR-021**: A decisions log MUST be created containing first entries for the dataset choice (with the closed-issue count fetched), the label mapping and its rationale, the train/validation/test split sizes (as cited percentages or counts), and the tracing backend choice (with at least one comparative metric or capability cited versus a rejected alternative). Each entry MUST cite a number where one exists; entries that cannot cite a number MUST justify why and what number a later day will produce (governance Rule 6).
- **FR-022**: An architecture document MUST be created with a short layered-architecture description and a diagram.
- **FR-023**: A runbook MUST be created listing the exact commands to bring the stack up, tear it down, and re-run the dataset pipeline.

**Explicitly out of scope today** (later days): authentication, the chatbot itself, the embeddable web widget, the retrieval pipeline, the entity-extraction and summarization endpoints, the LLM-baseline classifier, the classical-ML classifier, and both golden sets.

### Key Entities *(include if feature involves data)*

- **Health Report**: The structured response of the health endpoint; enumerates each upstream dependency with a reachable/unreachable status.
- **Raw Issue Record**: A verbatim closed-issue payload from the source repository, stored line-delimited under a versioned blob prefix.
- **Label Mapping**: A committed file relating source repository labels to exactly one of the four target classes, with a documented ambiguity/unmappable rule.
- **Dataset Split**: The stratified train/validation/test partition with a strict time ordering (test newer than train and validation).
- **Splits Report**: A machine-readable artifact recording counts per split and per class.
- **Training Run Artifacts**: The run configuration, training loss curve, and final weights persisted by the notebook.
- **Decision Record**: An entry in the decisions log capturing an architectural choice with a one-line justification.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer who has never seen the repository can go from a clean clone to a fully healthy stack using only the runbook, in under 15 minutes of active effort, with no manual steps beyond editing `.env`.
- **SC-002**: After bring-up, 100% of orchestrated infrastructure dependencies report healthy and the schema-migration step exits with a success status.
- **SC-003**: The health endpoint returns success and its body correctly reflects the true reachable/unreachable state of every upstream dependency, including when a non-secrets dependency is deliberately stopped.
- **SC-004**: With the secrets store stopped, the application fails to start 100% of the time and the cause is identifiable from a single log line.
- **SC-005**: A single health request is observable end to end as one connected span tree in the tracing backend.
- **SC-006**: The dataset pipeline produces a split in which the earliest test item is strictly more recent than the latest train/validation item, verified by timestamp comparison, with zero items outside the four allowed classes.
- **SC-007**: The splits report's per-split and per-class counts sum to the total mapped dataset size with no discrepancy.
- **SC-008**: Re-running the pipeline leaves all previously written versioned raw payloads intact and creates a new version.
- **SC-009**: The notebook, run end to end on the train split, leaves a run configuration, a loss curve, and final weights in the blob store.
- **SC-010**: A reviewer can find, in the three documentation files, justifications for the dataset choice, label mapping, split sizes, and tracing backend, plus a working set of up/down/re-run commands.
- **SC-011**: A push to any branch triggers a CI run that executes lint, type-check, image build, stubbed eval suites, stubbed redaction test, and stack smoke test, and reports green; a deliberately introduced lint error fails CI 100% of the time.

## Assumptions

- **Source repository**: Closed issues from `scikit-learn/scikit-learn` are the dataset, per the project governance binding the dataset choice.
- **Target classes**: Exactly four — bug, feature, docs, question — per governance scope.
- **Split ratios**: A stratified 70% train / 15% validation / 15% test split is used as a reasonable default unless the decisions log records otherwise; the strict time-ordering constraint (test newer than train/validation) takes precedence over exact ratio when the two conflict.
- **Fetch volume**: A bounded, recent window of closed issues sufficient for a stratified four-class split is fetched; the exact count is recorded in the splits report and decisions log rather than fixed in this spec.
- **Tracing backend**: An OpenTelemetry-compatible backend (e.g., a self-hosted Jaeger in the compose stack) is assumed as the reasonable default; the specific choice and its one-line justification are recorded in the decisions log.
- **Unmappable issues**: Issues with no label that maps to one of the four classes are excluded from the dataset (not forced into a class); issues with conflicting mapped labels are resolved by a documented precedence order in the mapping file.
- **Hosted GPU environment**: The notebook targets a Colab Pro-class environment with GPU; reproducing training locally is out of scope.
- **Development-mode secrets store**: The secrets store runs in development mode for Day 1; production hardening is a later concern.
- **Single maintainer / solo project**: No multi-user, authentication, or access-control concerns apply today (explicitly out of scope).
- **Governance compliance**: This feature is constrained by project governance Rules 1 (layered architecture), 2 (secrets discipline), 3 (storage discipline), 4 (refuse to boot), 6 (decisions backed by numbers), 7 (observability), 8 (tooling / one-command bring-up), and 10 (CI discipline).
