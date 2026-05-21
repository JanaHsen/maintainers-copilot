# Contract: api ↔ model-server `/embed`

`app/infra/embedding_client.py` calls this endpoint over the existing
httpx transport (`app/infra/model_server_client.py`). Same typed-error
family as `/classify` (Rule 11 — see R4 in research.md).

## Request

`POST {MODEL_SERVER_URL}/embed`

Headers:
- `Content-Type: application/json`
- `X-Request-Id` (propagated from the inbound api request — see
  Day 2 slice (d)).

Body:

```json
{
  "text": "string — the (HyDE-transformed) query, ≤ 8000 chars"
}
```

The corpus build calls this endpoint too, with batch shape:

```json
{
  "texts": ["s1", "s2", ...]
}
```

## Response — 200

```json
{
  "embedding": [/* float[D] */],
  "model_id": "BAAI/bge-small-en-v1.5",
  "dim": 384
}
```

Batch variant:

```json
{
  "embeddings": [[/* float[D] */], ...],
  "model_id": "BAAI/bge-small-en-v1.5",
  "dim": 384
}
```

### Invariants

- `dim` is constant for a given `model_id`. The api asserts `dim ==
  RAG_EMBEDDING_DIM` at boot and refuses to boot on mismatch (extends
  Rule 4).
- `embedding` magnitudes are not normalized at the wire boundary;
  cosine similarity in pgvector handles normalization.

## Error responses

The model server reuses the existing FastAPI exception conventions:

| HTTP | When |
|------|------|
| 422  | Body fails validation (empty text, missing field). |
| 503  | Embedding model not loaded (refuse-to-boot would have triggered, so this is exceptional). |
| 500  | Genuine internal error (forwarded to caller as 502 per Rule 11 mapping in R4). |

The api side maps these into its typed-error family in
`app/infra/embedding_client.py`:

| api → caller         | When |
|----------------------|------|
| `EmbedUnreachableError` (→503) | Network error reaching model server. |
| `EmbedTimeoutError` (→504)     | Timeout. |
| `EmbedBadInputError` (→502)    | 4xx from model server (programmer error). |
| `EmbedInternalError` (→502)    | 5xx from model server after bounded retries. |

The api's `/retrieve` handler never returns a 500 caused by an
upstream embed failure (Rule 11).

## Tracing

- The httpx call is auto-instrumented by `HTTPXClientInstrumentor`;
  the span name is `POST /embed`.
- The api adds a `rag.embed.text_length` attribute and a
  `rag.embed.hyde_applied` boolean attribute (true when the embed
  input is HyDE-generated, false when it's the raw question).
