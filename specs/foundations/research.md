# Phase 0 Research: Day 1 Foundations

All Technical Context fields were supplied explicitly in the plan input — no
`NEEDS CLARIFICATION` markers remain. This document records the decisions, the
rationale (Rule 6: every materially-architectural choice cited), and the
alternatives rejected, so `DECISIONS.md` can quote it.

## R1 — Tracing backend: Phoenix (Arize)

- **Decision**: Run Arize Phoenix as a local compose container; instrument the app through OpenTelemetry with an OTLP exporter pointed at Phoenix.
- **Rationale**: Runs locally with no external account or API key (keeps Rule 2 surface minimal), ships a usable trace UI out of the box, and natively models LLM-call spans — which Days 3–4 (RAG + chatbot) will need without a backend swap. Wiring it on Day 1 satisfies Rule 7's "never retrofitted".
- **Alternatives considered**: Jaeger (no native LLM-span semantics; would need re-instrumentation for Days 3–4); Grafana Tempo (heavier compose footprint, separate UI stack); a hosted vendor (introduces an external secret and account dependency, violating the keep-it-local goal).

## R2 — Dataset source: `scikit-learn/scikit-learn` closed issues

- **Decision**: Fetch closed issues via the GitHub REST API; PAT read from Vault, not `.env` (Rule 2).
- **Rationale**: Binding under the constitution's Project Scope. Closed issues carry settled, human-applied labels — the supervision signal for the 4-class task. REST (not GraphQL) keeps pagination + rate-limit handling simple and auditable (Rule 9).
- **Alternatives considered**: GitHub GraphQL (more efficient but more complex error/rate-limit surface for marginal Day 1 gain); GH Archive bulk dumps (staleness + extra ingestion path, no benefit at this scale).

## R3 — Label→class mapping strategy

- **Decision**: A committed `scripts/dataset/label_map.yaml` mapping scikit-learn labels to exactly one of `{bug, feature, docs, question}`, with an explicit **precedence order** for multi-label issues and a **drop rule** for issues whose labels map to none of the four.
- **Rationale**: A committed, declarative map makes the mapping reviewable and reproducible (Rule 9) and its rationale citable in `DECISIONS.md` (Rule 6). Dropping unmappable issues (rather than forcing a class) keeps labels trustworthy for downstream training.
- **Alternatives considered**: Heuristic/keyword inference from title/body (unreproducible, unauditable); keeping multi-label rows (incompatible with single-label classification target); mapping unmappable issues to `question` as a catch-all (pollutes the class — rejected).

## R4 — Split strategy: stratified + strict time ordering

- **Decision**: Drop unmappable issues, stratify by class, then time-sort so the **test split is the most recent 15%**; remaining 85% split into train/val (≈70/15 overall) stratified by class. `splits_report.json` records counts per split and per class.
- **Rationale**: Spec FR-016 requires the test set strictly more recent than train/val (realistic temporal generalization, no leakage). Stratification keeps every class represented in each split. When exact ratio and the strict time boundary conflict, the time boundary wins (recorded as an Assumption in the spec).
- **Alternatives considered**: Pure random split (temporal leakage — rejected); time-only split without stratification (risk of empty class slices in test); k-fold CV (overkill for Day 1 and incompatible with the time-ordering constraint).

## R5 — Refuse-to-boot mechanism

- **Decision**: `app/main.py` lifespan bootstraps in order Vault → DB engine → Redis pool → MinIO → tracing exporter. Each infra adapter retries with bounded backoff; on exhaustion (or a missing required Vault key) the lifespan raises, uvicorn fails startup, and the container exits non-zero with a single specific log line naming the failed dependency.
- **Rationale**: Rule 4 forbids a degraded boot. A specific log line makes SC-004 mechanically verifiable. Bounded retries absorb compose start-order races without masking a genuinely-down dependency.
- **Alternatives considered**: Lazy/first-request connection (hides failure past startup — violates Rule 4); infinite retry (container hangs instead of failing loudly — rejected); compose `depends_on: condition: service_healthy` only (necessary but insufficient — the app must still assert its own contract).

## R6 — `api` / `migrate` container topology

- **Decision**: One shared Dockerfile/image. `migrate` runs `alembic upgrade head` and exits 0; `api` `depends_on` `migrate` (completed) and infra healthchecks, then runs uvicorn. Migration `0001_baseline` enables pgvector and creates `audit_log`.
- **Rationale**: Rule 3 — every schema change through Alembic; a dedicated one-shot service makes "migration applied and exited cleanly" a first-class, observable acceptance condition (FR-004) and keeps the `api` image free of a migrate-on-boot side effect.
- **Alternatives considered**: Migrate-on-api-startup (races multiple replicas, muddies refuse-to-boot semantics); separate migrate image (needless image divergence — rejected).

## R7 — Notebook ↔ MinIO connectivity (Colab)

- **Decision**: Colab pulls/pushes via `boto3` against the local MinIO endpoint exposed through an `ngrok` tunnel; the tunnel step is documented in `RUNBOOK.md`. Notebook pushes `state_dict` + `model_card.json` (architecture, hyperparams, training-data hash, final val metrics, weights SHA-256) under `artifacts/classifier/distilbert/{run_id}/`.
- **Rationale**: Colab cannot reach `localhost` MinIO directly; ngrok is the lightest documented bridge for a solo Day 1. The SHA-256 in the model card pre-positions the Rule 4 weights-integrity check that Day 2 will enforce.
- **Alternatives considered**: Push to a cloud bucket (introduces an external secret + account — rejected for Day 1); commit weights to git (binary bloat, violates Rule 8/9 hygiene); Colab local runtime (defeats the point of Colab Pro GPU).

## R8 — Day 1 CI scope

- **Decision**: `.github/workflows/ci.yml` runs ruff → mypy `app/` → docker image build → `docker-compose up -d` → curl `/health` → `docker-compose down`, plus the log-redaction test. Eval suites are correctly-shaped stubs; thresholds not enforced.
- **Rationale**: Rule 10 wants every push gated, but golden sets/classifier do not exist until Days 2–3. A scoped CI that is green-and-meaningful Day 1 and grows by day beats a red gate referencing nonexistent artifacts. The redaction test is enforceable now (Rule 7) so it ships now.
- **Alternatives considered**: Full Rule-10 pipeline immediately (perpetually red — rejected); no CI until Day 2 (violates Rule 10's "every push" and loses the redaction guarantee — rejected).
