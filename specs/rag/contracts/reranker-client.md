# Contract: api ↔ model-server `/rerank`

`app/infra/reranker_client.py` calls this endpoint over the existing
httpx transport. Same typed-error family as `/classify` (Rule 11).

## Request

`POST {MODEL_SERVER_URL}/rerank`

Headers:
- `Content-Type: application/json`
- `X-Request-Id` (propagated from the inbound api request).

Body:

```json
{
  "query": "how do I group a DataFrame by date and aggregate?",
  "candidates": [
    { "id": "<child_chunk_id>", "text": "<child chunk content>" },
    ...30...
  ]
}
```

### Invariants

- `candidates` must be non-empty and the api caps it at 30 (the
  stage-1 funnel width per FR-015). The server may accept more but
  the api never sends more.
- `id` is opaque to the reranker; the server returns it back so the
  caller can join scores back to its own state without an extra
  lookup.

## Response — 200

```json
{
  "scores": [
    { "id": "<child_chunk_id>", "score": 9.7 },
    { "id": "<child_chunk_id>", "score": 6.2 },
    ...
  ],
  "model_id": "cross-encoder/ms-marco-MiniLM-L-6-v2"
}
```

The order in `scores` is **not** required to be sorted; the api
sorts client-side. Higher score = more relevant.

## Error responses

Same shape as `/embed`. The api side typed-error family in
`app/infra/reranker_client.py`:

| api → caller            | When |
|-------------------------|------|
| `RerankUnreachableError` (→503) | Network error reaching model server. |
| `RerankTimeoutError` (→504)     | Timeout — note: the reranker is the heaviest call in the chain, give it a longer per-call budget than `/embed`. |
| `RerankBadInputError` (→502)    | 4xx from model server. |
| `RerankInternalError` (→502)    | 5xx from model server after bounded retries. |

The api's `/retrieve` handler never returns a 500 caused by an
upstream rerank failure (Rule 11).

## Tracing

- The httpx call is auto-instrumented; the span name is `POST /rerank`.
- The api adds `rag.rerank.candidate_count` and `rag.rerank.k_returned`
  attributes. The reranker server itself adds
  `rag.rerank.batch_forward_ms` (cross-encoder inference time, useful
  for SC-001 latency budget tracking).
