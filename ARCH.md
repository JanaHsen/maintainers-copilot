# Architecture

Single-project layered FastAPI service (Rule 1). A request only ever flows
**downward**; a layer never imports a layer above it.

## Layers

1. **api** (`app/api/routers/`) — HTTP surface. One file per resource;
   `routers/__init__.py` aggregates them into one `APIRouter`. Calls
   **services** only.
2. **services** (`app/services/`) — orchestration / use cases (e.g.
   `health_service` probes each dependency, one OTel span per check).
3. **repositories** (`app/repositories/`) — the *only* place SQL lives;
   returns domain types, never leaks ORM rows upward.
4. **domain** (`app/domain/`) — Pydantic models (`HealthReport`,
   `DependencyStatus`), distinct from any ORM model.
5. **infra** (`app/infra/`) — one file per external system (Vault, DB,
   Redis, MinIO, tracing, request-context, log redaction; plus Day 2+
   stubs `anthropic_client`, `model_server_client`).

```mermaid
flowchart TD
    api["api — routers (HTTP)"] --> svc["services — orchestration"]
    svc --> repo["repositories — all SQL"]
    svc --> infra["infra — external systems"]
    repo --> infra
    api -. uses .-> domain["domain — Pydantic models"]
    svc -. uses .-> domain
    infra --> ext[("Vault · Postgres+pgvector · Redis · MinIO · Phoenix")]
```

## Compose topology

`migrate` and `api` share one image (entrypoint switches on command).
Boot order is enforced by healthchecks + completion gates:

```mermaid
flowchart LR
    subgraph infra
      pg[(postgres+pgvector)]
      rd[(redis)]
      mn[(minio)]
      vt[(vault)]
      px[(phoenix)]
    end
    vt --> seed[vault-seed one-shot]
    seed --> mig[migrate: alembic upgrade head -> exit 0]
    pg --> mig
    mig --> api[api: uvicorn]
    pg --> api
    rd --> api
    mn --> api
    vt --> api
    px --> api
    classDef later fill:#eee,stroke-dasharray:3 3;
    ms[model-server]:::later
    cb[chatbot]:::later
```

`model-server` and `chatbot` are declared with `profiles: [later]` — the
compose file documents the final shape while only Day 1 services run.

## Refuse-to-boot (Rule 4)

`app/main.py` lifespan bootstraps Vault → DB → Redis → MinIO → (tracing is
instrumented at import). Vault unreachable, a missing required Vault key,
Postgres-after-retries, or MinIO-after-retries each emits one
`REFUSE TO BOOT: …` line and propagates so the container exits non-zero.
Redis down is tolerated and surfaces as `/health` `degraded`.
