<!--
SYNC IMPACT REPORT
==================
Version change: 1.2.0 → 1.3.0
Bump rationale: MINOR — dataset reverted to pandas-dev/pandas after the
  scikit-learn corpus produced only 4 question-class test samples
  (16,926 → 4,787 mapped), insufficient for stable per-class F1
  evaluation. Pandas canonical run_id 20260519T133455Z remains valid in
  MinIO.

Modified principles: None (Rules 1–11 unchanged)

Modified sections:
  - Project Scope: binding dataset source `scikit-learn/scikit-learn`
      → `pandas-dev/pandas` (revert)

Removed sections: None

Templates requiring updates:
  - .specify/templates/plan-template.md       ✅ aligned (no rule change)
  - .specify/templates/spec-template.md       ✅ aligned (no change)
  - .specify/templates/tasks-template.md      ✅ aligned (no change)
  - .specify/templates/checklist-template.md  ✅ aligned (no change)

Follow-up TODOs: None.

Previous report (kept for history):
  Version change: 1.1.0 → 1.2.0
  Bump rationale: MINOR — dataset source changed before Day 2 work begins;
    pandas fetch artifacts in MinIO superseded but retained for audit.
  Modified sections:
    - Project Scope: binding dataset source `pandas-dev/pandas`
        → `scikit-learn/scikit-learn`

  Version change: 1.0.0 → 1.1.0
  Bump rationale: MINOR — two new principles added before any code was
    written, bringing the constitution into full alignment with the Week 7
    brief's CI scope and chatbot resilience requirements. No existing rule
    was modified or removed.
  Modified principles: None (Rules 1–9 unchanged)
  Added principles:
    - X.  CI Discipline (Rule 10)
    - XI. Resilient Tool Use (Rule 11)
  Modified sections:
    - Intro paragraph: "nine principles" → "eleven principles"
    - Development Workflow & Quality Gates: added Rule 10 and Rule 11 gates
    - Governance / Compliance review: "Rules 1–9" → "Rules 1–11"
  Removed sections: None

  Version change: (none) → 1.0.0
  Bump rationale: Initial ratification of the project constitution.
-->

# Maintainer's Copilot Constitution

This constitution governs the Maintainer's Copilot — a solo project for Week 7
of the AIE program. Every subsequent `plan.md` and `tasks.md` MUST reference
the rules below by their number (e.g. "satisfies Rule 4", "Rule 7 task").
The eleven principles are non-negotiable. The Governance section defines the
only process by which they may change.

## Core Principles

### I. Layered Architecture (Rule 1)

Code MUST be organized into strict layers with one-directional dependencies:

- `app/api/` handles HTTP only. Routers MUST NOT touch SQLAlchemy, Redis,
  MinIO, Vault, or the model server directly.
- `app/services/` owns business logic, transaction boundaries, and
  cache/memory invalidation.
- `app/repositories/` owns all SQL. No SQL exists outside this layer.
- `app/domain/` holds Pydantic domain models. These MUST be distinct types
  from the SQLAlchemy ORM models; ORM models MUST NOT leak past repositories.
- `app/infra/` holds adapters for Vault, MinIO, Redis, the Anthropic API
  client, the model server client, the tracing backend, and the log
  redaction layer.

**Rationale**: Layer isolation keeps every line explicable, makes the data
path auditable, and lets each concern be tested without the others.

### II. Secrets Discipline (Rule 2)

Every secret MUST resolve from HashiCorp Vault at startup — Anthropic API key,
JWT signing key, database password, MinIO root user and password, tracing
backend key. The `.env` file MUST contain only the Vault root token and port
numbers. A grep for `sk-ant-` or for `password` anywhere under `app/` MUST
return zero matches outside the Vault adapter.

**Rationale**: A single, audited secrets path eliminates credential sprawl and
makes leak detection a mechanical check rather than a judgment call.

### III. Storage Discipline (Rule 3)

Postgres 16 with the pgvector extension is the ONLY relational and vector
store. MinIO is the ONLY blob store. Redis 7 is the ONLY ephemeral store.
Every schema change MUST flow through an Alembic migration. "Drop the volume
and start over" is NOT a migration strategy and is prohibited.

**Rationale**: One store per responsibility plus reversible migrations keeps
state recoverable and the system reproducible from a clean clone.

### IV. Refuse To Boot (Rule 4)

The `api` container MUST exit with a clear, specific error when any of the
following holds: Vault is unreachable; classifier weights are missing; the
weights' SHA-256 does not match the model card; the tracing backend is
misconfigured; or any threshold in `eval_thresholds.yaml` is set to zero or
disabled. A degraded or silently-misconfigured boot is forbidden.

**Rationale**: Failing loudly at startup prevents shipping a system that is
quietly unevaluated, untraced, or running unverified weights.

### V. Evals Are The Grade (Rule 5)

Two golden sets live in the repo: 25 hand-curated examples for classification
and 25 for RAG. CI MUST run both suites on every push. A regression below the
threshold committed in `eval_thresholds.yaml` MUST block the merge. The
`eval_report.json` from each run MUST be persisted to MinIO.

**Rationale**: Quality is defined by measured behaviour on a fixed set, not by
inspection; the gate is automated so it cannot be skipped under pressure.

### VI. Decisions Backed By Numbers (Rule 6)

`DECISIONS.md` MUST record every architectural choice — embedding model,
chunking strategy, deployment classifier, retrieval weighting, long-term
memory type, and any other materially-architectural decision. Each entry MUST
cite a number from the golden set or a published benchmark.

**Rationale**: A decision without a number is a preference; this rule forces
every choice to be defensible on Friday with evidence.

### VII. Observability (Rule 7)

Tracing MUST be wired from the first commit, never retrofitted. Every request
MUST receive a trace ID and a request ID. Every uncaught exception MUST be
logged with both IDs. Logs MUST be redacted of anything matching an Anthropic
key prefix, a password, or a token-shaped string, and a redaction test in CI
MUST prove the redaction works.

**Rationale**: A request you cannot trace is a request you cannot debug or
defend; redaction is enforced by test so it cannot silently rot.

### VIII. Tooling (Rule 8)

Python dependencies MUST be managed with `uv`, producing `pyproject.toml` and
`uv.lock`. `.venv` MUST be in `.gitignore` on the first commit. `docker-compose`
MUST orchestrate the entire stack, and a fresh clone MUST come up cleanly with
`cp .env.example .env` followed by `docker-compose up`.

**Rationale**: A reproducible, single-command bring-up is the difference
between a project that can be reviewed and one that only runs on one laptop.

### IX. No Vibe Coding (Rule 9)

Every line in the repo MUST be explicable on Friday. File names MUST describe
what lives in them. `utils.py`, `helpers.py`, and `misc.py` (and equivalently
vague names) are prohibited.

**Rationale**: Self-describing structure is what makes the entire codebase
auditable by a single maintainer under a deadline.

### X. CI Discipline (Rule 10)

Every push to any branch MUST trigger a CI pipeline that runs, in order:
linting via `ruff`, type-checking via `mypy` on `app/`, building each Docker
image declared in `docker-compose.yml`, the classification and RAG eval suites
(Rule 5), the log redaction test (Rule 7), and a smoke test that brings the
full stack up via `docker-compose up`, hits `/health`, and tears down. A
failure at any step MUST block the merge. The CI workflow definition MUST
live in `.github/workflows/`.

**Rationale**: A merge is only safe if every quality gate the project depends
on has passed; centralizing them in one pipeline eliminates the "I forgot to
run the linter" class of regression and makes every gate the brief demands
uniformly enforced.

### XI. Resilient Tool Use (Rule 11)

The chatbot MUST handle upstream tool failures gracefully. When any tool the
chatbot calls — the classifier endpoint, the NER endpoint, the summarizer
endpoint, the RAG pipeline, the long-term memory store, or the Anthropic API
— fails or times out, the chatbot MUST catch the error, log it with the trace
ID and request ID (Rule 7), surface a user-visible message explaining what
could not be done, and continue the conversation. A 5xx response to the user
caused by an upstream tool failure is prohibited; the only 5xx responses
permitted are those caused by the chatbot's own code paths failing.

**Rationale**: A chatbot that crashes when its tools misbehave is not a
production-shaped system; graceful degradation is the difference between a
demo and a service.

## Project Scope & Technology Stack

The Maintainer's Copilot is an authenticated chatbot for open-source
maintainers. It:

- classifies GitHub issues into bug / feature / docs / question using three
  models compared on the same test set;
- extracts code entities from issue text;
- summarizes issue threads;
- answers questions over project docs using advanced RAG;
- carries memory across conversations;
- is embeddable as a React widget in a host app.

The dataset is closed issues from `pandas-dev/pandas`. The LLM provider is the
Anthropic API. The relational/vector store is Postgres 16 + pgvector; the blob
store is MinIO; the ephemeral store is Redis 7; secrets come from HashiCorp
Vault. These technology choices are binding under Rules 2 and 3 and MUST NOT
be substituted without a constitutional amendment.

## Development Workflow & Quality Gates

- `plan.md` and `tasks.md` MUST cite the relevant rule numbers (1–11) for
  each constrained decision or task.
- CI MUST run linting, type-checking, image builds, the classification and
  RAG golden suites, the redaction test, and a stack smoke test on every
  push; a failing gate blocks merge (Rules 5, 7, 10).
- The chatbot's tool-calling paths MUST be reviewed against Rule 11 before
  the Day 4 branch merges into `main`.
- No schema reaches `main` except through an Alembic migration (Rule 3).
- Every architectural choice merged MUST have a corresponding `DECISIONS.md`
  entry with a cited number (Rule 6).
- A change that cannot be brought up via `cp .env.example .env` +
  `docker-compose up` from a clean clone is not done (Rule 8).

## Governance

This constitution supersedes all other practices for this project. When a
plan, task, or piece of code conflicts with a rule, the rule wins; the
conflicting work MUST be changed or an amendment MUST be ratified first.

**Amendment procedure**: Amendments are recorded by editing this file. Each
amendment MUST state which rule(s) changed and why, and MUST update any
dependent template flagged in the Sync Impact Report.

**Versioning policy**: This constitution is versioned with semantic
versioning. MAJOR — a rule is removed or redefined in a backward-incompatible
way. MINOR — a rule or section is added or materially expanded. PATCH —
clarifications and wording fixes with no semantic change.

**Compliance review**: Every plan's Constitution Check gate MUST verify
compliance with Rules 1–11 before Phase 0 and again after design. Any
justified deviation MUST be recorded in the plan's Complexity Tracking table;
unjustified deviations block the work.

**Version**: 1.3.0 | **Ratified**: 2026-05-18 | **Last Amended**: 2026-05-19