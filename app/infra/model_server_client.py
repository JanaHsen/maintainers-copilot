"""Self-hosted model-server client — STUB (Day 2+).

Pre-positions the classifier-inference boundary (the fine-tuned DistilBERT
served by the ``model-server`` compose service declared with
``profiles: [later]``). Not wired on Day 1; calls raise loud (Rule 9).
"""

from dataclasses import dataclass

_NOT_YET = "model_server_client is a Day 2+ stub and is not wired on Day 1"


@dataclass(frozen=True)
class ClassificationRequest:
    text: str


@dataclass(frozen=True)
class ClassificationResponse:
    label: str
    score: float


def classify(request: ClassificationRequest) -> ClassificationResponse:
    """Classify issue text via the self-hosted model server (Day 2+)."""
    raise NotImplementedError(_NOT_YET)
