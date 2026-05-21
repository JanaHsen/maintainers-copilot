"""HTTPX client for the model server's /embed endpoint.

Bounded timeout, bounded retries on 5xx + connection failures + read
timeouts (mirror of `app.infra.model_server_client.classify`). Errors
raise the same typed `ModelServer*Error` family the api's
`/retrieve` already maps to Rule-11 HTTP statuses
(503/504/502 — never 500).

Online single-query path only: the corpus build under `scripts/rag/`
embeds in-process via `sentence-transformers` (see research.md R3).
"""

from __future__ import annotations

import json
import time

import httpx

from app.config import get_settings
from app.infra.model_server_client import (
    BASE_DELAY_SECONDS,
    MAX_ATTEMPTS,
    TIMEOUT_SECONDS,
    ModelServerError,
    ModelServerInternalError,
    ModelServerInvalidInputError,
    ModelServerTimeoutError,
    ModelServerUnreachableError,
)


def _endpoint() -> str:
    settings = get_settings()
    return f"http://{settings.model_server_host}:{settings.model_server_port}/embed"


def embed(text: str, *, request_id: str = "") -> list[float]:
    """POST /embed with bounded timeout + retries; return the 768-dim vector."""
    if not text:
        raise ModelServerInvalidInputError("embed: text must be non-empty")
    url = _endpoint()
    headers = {"X-Request-Id": request_id} if request_id else {}
    payload = {"text": text}

    last_5xx: int | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
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
                    f"model server /embed returned non-JSON 2xx body: "
                    f"{resp.text[:200]}"
                ) from exc
            if not isinstance(data, dict) or "embedding" not in data:
                raise ModelServerError(
                    "model server /embed response missing 'embedding' field"
                )
            embedding = data["embedding"]
            if not isinstance(embedding, list) or len(embedding) != 768:
                raise ModelServerError(
                    f"model server /embed returned embedding of length "
                    f"{len(embedding) if isinstance(embedding, list) else 'N/A'}, "
                    f"expected 768"
                )
            return [float(x) for x in embedding]

        if 400 <= resp.status_code < 500:
            raise ModelServerInvalidInputError(
                f"model server rejected /embed with {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        last_5xx = resp.status_code
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(BASE_DELAY_SECONDS * (2**attempt))
            continue

    raise ModelServerInternalError(
        f"model server /embed returned {last_5xx} after {MAX_ATTEMPTS} attempts"
    )
