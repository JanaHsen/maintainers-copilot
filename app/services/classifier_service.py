"""Classifier orchestration: convert model_server_client exceptions into a typed outcome.

Per Rule 11, model-server failures must not surface as 500s. The api
layer consumes :class:`ClassifyOutcome` and chooses how to respond
(typically 503 for ``unreachable``/``timeout``/``internal``, 502 for
``unexpected`` body, 400 for ``bad_request``). Domain code stays free of
HTTP plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.infra import model_server_client
from app.infra.log_redaction import redact

ClassifyErrorKind = Literal[
    "unreachable",
    "timeout",
    "bad_request",
    "internal",
    "unexpected",
]


@dataclass(frozen=True)
class ClassifyOk:
    label: str
    confidence: float
    label_scores: dict[str, float]


@dataclass(frozen=True)
class ClassifyError:
    kind: ClassifyErrorKind
    detail: str


ClassifyOutcome = ClassifyOk | ClassifyError


# Map each typed model-server exception to its outcome kind. Keys are exception
# types so additions stay localized; subclassing order matters less because we
# look up by exact type.
_EXCEPTION_TO_KIND: dict[type[model_server_client.ModelServerError], ClassifyErrorKind] = {
    model_server_client.ModelServerUnreachableError: "unreachable",
    model_server_client.ModelServerTimeoutError: "timeout",
    model_server_client.ModelServerInvalidInputError: "bad_request",
    model_server_client.ModelServerInternalError: "internal",
}


def classify_issue(
    title: str, body: str, *, request_id: str = ""
) -> ClassifyOutcome:
    """Call the model server and return a typed outcome (never raise)."""
    try:
        resp = model_server_client.classify(
            model_server_client.ClassificationRequest(title=title, body=body),
            request_id=request_id,
        )
    except model_server_client.ModelServerError as exc:
        kind = _EXCEPTION_TO_KIND.get(type(exc), "unexpected")
        return ClassifyError(kind=kind, detail=redact(str(exc)))
    return ClassifyOk(
        label=resp.label,
        confidence=resp.confidence,
        label_scores=resp.label_scores,
    )
