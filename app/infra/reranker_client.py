"""HTTPX client for the model server's /rerank endpoint.

Cross-encoder rerank is the heaviest hop in the /retrieve chain; this
client uses a longer per-call timeout than /embed (the contract in
specs/rag/contracts/reranker-client.md flags this). Same typed-error
family as `app.infra.model_server_client.classify` so the service
layer's Rule-11 mapping handles both clients with one code path.
"""

from __future__ import annotations

import json
import time

import httpx

from app.config import get_settings
from app.infra.model_server_client import (
    BASE_DELAY_SECONDS,
    MAX_ATTEMPTS,
    ModelServerError,
    ModelServerInternalError,
    ModelServerInvalidInputError,
    ModelServerTimeoutError,
    ModelServerUnreachableError,
)

# Reranker forward-pass over 30 candidates takes longer than a single
# embedding; give it a wider budget than the default 5s. BAAI/bge-reranker-base
# (~110M params) on CPU takes ~25s for 30 candidates — set 60s to leave
# headroom; the api router's overall timeout still bounds the request.
RERANK_TIMEOUT_SECONDS = 60.0


def _endpoint() -> str:
    settings = get_settings()
    return f"http://{settings.model_server_host}:{settings.model_server_port}/rerank"


def rerank(
    query: str,
    candidates: list[tuple[str, str]],
    *,
    request_id: str = "",
) -> list[tuple[str, float]]:
    """POST /rerank; returns [(id, score), ...] in INPUT order (caller sorts)."""
    if not query:
        raise ModelServerInvalidInputError("rerank: query must be non-empty")
    if not candidates:
        raise ModelServerInvalidInputError("rerank: candidates must be non-empty")
    url = _endpoint()
    headers = {"X-Request-Id": request_id} if request_id else {}
    payload = {
        "query": query,
        "candidates": [{"id": cid, "text": text} for cid, text in candidates],
    }

    last_5xx: int | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=RERANK_TIMEOUT_SECONDS) as client:
                resp = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BASE_DELAY_SECONDS * (2**attempt))
                continue
            raise ModelServerTimeoutError(
                f"model server timeout calling {url}"
            ) from exc
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BASE_DELAY_SECONDS * (2**attempt))
                continue
            raise ModelServerUnreachableError(
                f"model server unreachable at {url}"
            ) from exc

        if 200 <= resp.status_code < 300:
            try:
                data = resp.json()
            except json.JSONDecodeError as exc:
                raise ModelServerError(
                    f"model server /rerank returned non-JSON 2xx body: "
                    f"{resp.text[:200]}"
                ) from exc
            if not isinstance(data, dict) or "scores" not in data:
                raise ModelServerError(
                    "model server /rerank response missing 'scores' field"
                )
            try:
                return [(str(s["id"]), float(s["score"])) for s in data["scores"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise ModelServerError(
                    f"model server /rerank score-row shape invalid: {exc}"
                ) from exc

        if 400 <= resp.status_code < 500:
            raise ModelServerInvalidInputError(
                f"model server rejected /rerank with {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        last_5xx = resp.status_code
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(BASE_DELAY_SECONDS * (2**attempt))
            continue

    raise ModelServerInternalError(
        f"model server /rerank returned {last_5xx} after {MAX_ATTEMPTS} attempts"
    )
