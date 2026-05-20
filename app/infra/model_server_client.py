"""HTTPX client for the self-hosted DistilBERT model server.

Bounded timeout, bounded retries on 5xx + connection failures + read
timeouts. **Not** retried on 4xx — those are programmer errors and
retrying just amplifies them. Errors surface as a small typed family so
the service layer can map them to Rule-11 outcomes instead of leaking
500s to callers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

# Bounded so a hung model server can't block a request indefinitely
# (Rule 4 spirit: surface failures, don't hide them).
TIMEOUT_SECONDS = 5.0
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 0.2


class ModelServerError(RuntimeError):
    """Base for any model-server call failure (typed for Rule 11 mapping)."""


class ModelServerUnreachableError(ModelServerError):
    """Connection refused, DNS failure, no route to host."""


class ModelServerTimeoutError(ModelServerError):
    """Read/connect timeout after exhausting retries."""


class ModelServerInvalidInputError(ModelServerError):
    """4xx — request was rejected by the model server; never retried."""


class ModelServerInternalError(ModelServerError):
    """5xx after exhausting retries."""


@dataclass(frozen=True)
class ClassificationRequest:
    title: str
    body: str


@dataclass(frozen=True)
class ClassificationResponse:
    label: str
    confidence: float
    label_scores: dict[str, float]


def _endpoint(path: str) -> str:
    settings = get_settings()
    return f"http://{settings.model_server_host}:{settings.model_server_port}{path}"


def _parse_classification_response(payload: dict[str, object]) -> ClassificationResponse:
    try:
        label = str(payload["label"])
        confidence = float(payload["confidence"])  # type: ignore[arg-type]
        raw_scores = payload["label_scores"]
        if not isinstance(raw_scores, dict):
            raise ValueError("label_scores is not an object")
        label_scores = {str(k): float(v) for k, v in raw_scores.items()}  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelServerError(f"unexpected /classify response shape: {exc}") from exc
    return ClassificationResponse(
        label=label, confidence=confidence, label_scores=label_scores
    )


def classify(
    request: ClassificationRequest, *, request_id: str = ""
) -> ClassificationResponse:
    """POST /classify with bounded timeout, bounded retries on 5xx + transport errors."""
    url = _endpoint("/classify")
    payload = {"title": request.title, "body": request.body}
    headers = {"X-Request-Id": request_id} if request_id else {}

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
                    f"model server returned non-JSON 2xx body: {resp.text[:200]}"
                ) from exc
            if not isinstance(data, dict):
                raise ModelServerError("model server /classify returned non-object JSON")
            return _parse_classification_response(data)

        if 400 <= resp.status_code < 500:
            # Programmer error on our side — retrying won't help.
            raise ModelServerInvalidInputError(
                f"model server rejected /classify with {resp.status_code}: "
                f"{resp.text[:200]}"
            )

        last_5xx = resp.status_code
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(BASE_DELAY_SECONDS * (2**attempt))
            continue

    raise ModelServerInternalError(
        f"model server returned {last_5xx} after {MAX_ATTEMPTS} attempts"
    )
