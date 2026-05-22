# Memory tool primitives — internal contracts

The two memory primitives are *internal Python interfaces*, not HTTP endpoints. The Part 2 chatbot agent loop and the Part 3 admin panel will compose them. Part 1 ships them as service-layer functions with stable signatures and explicit failure modes.

## `write_memory`

**Location**: `app/services/tools/write_memory_tool.py`.

**Signature** (typed pseudocode):

```python
@dataclass(frozen=True)
class WriteMemoryOk:
    memory_id: UUID

@dataclass(frozen=True)
class WriteMemoryError:
    kind: Literal[
        "widget_actor_forbidden",
        "embedding_unreachable",
        "embedding_timeout",
        "audit_failed",
        "db_failed",
    ]
    detail: str

WriteMemoryOutcome = WriteMemoryOk | WriteMemoryError

def write_memory(
    *,
    content: str,
    actor: Actor,                # AuthedUser(...) | WidgetSession(...)
    conversation_id: UUID,
    source: Literal["episodic"] = "episodic",
    request_id: str = "",
    trace_id: str = "",
) -> WriteMemoryOutcome: ...
```

**Behavior contract**:

1. If `actor` is a `WidgetSession`, return `WriteMemoryError("widget_actor_forbidden", …)`. No DB write. No audit row.
2. Apply `log_redaction.redact_for_persistence(content)` to the content **before** any further step (Rule 7, research R6).
3. Embed the (redacted) content via `embedding_client.embed(text)`. On failure, return the appropriate typed error (`embedding_unreachable` / `embedding_timeout`).
4. Open a single transaction; insert the memory row; insert the audit row (`action='memory.write'`, `target_type='memory'`, `target_id=memory_id`, `payload={"content_bytes": len(content_bytes), "source": "episodic", "trace_id": trace_id, "request_id": request_id}`). Commit. If either step fails, the transaction rolls back and the function returns `audit_failed` or `db_failed`.
5. Emit a Phoenix span (`memory.write`) with attributes: `actor.kind`, `actor.id`, `conversation_id`, `content_bytes`, `source`, and the result (`ok | <error.kind>`).
6. Return `WriteMemoryOk(memory_id=...)`.

**Invariants**:

- The persisted `content` never contains an unredacted `sk-ant-…`, JWT, host token, or email address.
- The persisted memory and its corresponding audit-log row land atomically (FR-021).
- `write_memory` never raises. All failure modes are typed outcomes.

## `recall_memory`

**Location**: `app/services/tools/recall_memory_tool.py`.

**Signature**:

```python
@dataclass(frozen=True)
class RecallMemoryHit:
    memory_id: UUID
    content: str
    created_at: datetime
    similarity: float  # cosine similarity in [-1, 1]

@dataclass(frozen=True)
class RecallMemoryOk:
    hits: list[RecallMemoryHit]   # length 0..k

@dataclass(frozen=True)
class RecallMemoryError:
    kind: Literal[
        "widget_actor_forbidden",
        "embedding_unreachable",
        "embedding_timeout",
        "db_failed",
    ]
    detail: str

RecallMemoryOutcome = RecallMemoryOk | RecallMemoryError

def recall_memory(
    *,
    query: str,
    actor: Actor,
    k: int = 5,
    request_id: str = "",
    trace_id: str = "",
) -> RecallMemoryOutcome: ...
```

**Behavior contract**:

1. If `actor` is a `WidgetSession`, return `RecallMemoryError("widget_actor_forbidden", …)`. No DB read.
2. Embed the query (no persistence redaction here — the query never leaves the request lifetime; the log-handler redaction layer still applies to any log emission).
3. Call `memory_repository.query_top_k(user_id=actor.user_id, query_embedding=..., k=k)`. The repository's SQL is scoped `WHERE user_id = :user_id` — cross-account isolation is enforced at the SQL boundary (FR-010, SC-003).
4. Emit a Phoenix span (`memory.recall`) with attributes: `actor.id`, `k`, `hits_returned`, `top_similarity`.
5. Return `RecallMemoryOk(hits=...)` (possibly empty).

**Invariants**:

- A `WidgetSession` actor can never read any maintainer's memories.
- No memory belonging to actor B is ever returned to actor A — enforced by the SQL `WHERE user_id = :user_id` clause and tested in `tests/integration/test_cross_conversation_memory_recall.py` (SC-003).
- `recall_memory` never raises. All failure modes are typed outcomes.

## Actor type

```python
@dataclass(frozen=True)
class AuthedUser:
    user_id: UUID
    role: Literal["user", "admin"]

@dataclass(frozen=True)
class WidgetSession:
    widget_id: UUID
    session_id: str

Actor = AuthedUser | WidgetSession
```

The `Actor` type is shared in `app/domain/conversation.py` so the chatbot service in Part 2 can pattern-match consistently.

## Testing surface

- Unit tests in `tests/services/tools/test_write_memory_tool.py` and `tests/services/tools/test_recall_memory_tool.py` cover each failure-mode branch with a mocked embedding client and a real Postgres against migration 0003.
- Integration tests in `tests/integration/test_cross_conversation_memory_recall.py` and `tests/integration/test_widget_actor_refusal.py` exercise the end-to-end path against the docker-compose stack.
