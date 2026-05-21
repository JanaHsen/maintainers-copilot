"""DistilBERT inference: load weights into the architecture and classify text.

Loading path (called once from the lifespan after the boot integrity check):

  1. ``AutoTokenizer.from_pretrained('distilbert-base-uncased')`` — pinned
     tokenizer, ``max_length=512``.
  2. ``DistilBertForSequenceClassification.from_pretrained(...)`` — base
     architecture with the head sized from ``model_card.architecture.label2id``
     and ``id2label``/``label2id`` read from the model card at runtime
     (never hardcoded — see the contract in
     :func:`model_server.boot_check.verify_artifacts`).
  3. ``torch.load`` the published ``state_dict.pt`` from in-memory bytes
     and ``load_state_dict(strict=True)``. A key/dtype mismatch raises
     :class:`StateDictLoadError`; the lifespan turns that into an eighth
     refuse-to-boot mode (Rule 4).

Inference path: concatenate ``title`` and ``body`` with a blank line,
tokenize with truncation at 512 tokens, run the model under
``torch.no_grad()``, softmax the logits, return the argmax label, its
confidence, and the full per-class score map. The class names returned
are the ones in ``model_card.architecture.id2label`` — the API contract
is enforced at boot, but the labels themselves still flow from the card,
which keeps the boot check the single source of truth.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import torch
from transformers import (
    AutoTokenizer,
    DistilBertForSequenceClassification,
)
from transformers.tokenization_utils_base import (
    PreTrainedTokenizerBase,
)

from model_server.boot_check import VerifiedArtifacts

MODEL_NAME = "distilbert-base-uncased"
MAX_LENGTH = 512


class StateDictLoadError(RuntimeError):
    """torch.load_state_dict failed — keys/dtypes don't match the architecture."""


@dataclass(frozen=True)
class LoadedModel:
    model: DistilBertForSequenceClassification
    tokenizer: PreTrainedTokenizerBase
    id2label: dict[int, str]


@dataclass(frozen=True)
class Prediction:
    label: str
    confidence: float
    label_scores: dict[str, float]


def load_model(verified: VerifiedArtifacts) -> LoadedModel:
    # AutoTokenizer.from_pretrained is typed as untyped in the transformers
    # stubs; DistilBertForSequenceClassification.from_pretrained is typed,
    # so the ignore is needed only on the tokenizer call.
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)  # type: ignore[no-untyped-call]
    model = DistilBertForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(verified.label2id),
        id2label=dict(verified.id2label),
        label2id=dict(verified.label2id),
    )
    try:
        state_dict = torch.load(
            io.BytesIO(verified.weights_bytes),
            map_location="cpu",
            weights_only=True,
        )
        model.load_state_dict(state_dict, strict=True)
    except Exception as exc:  # noqa: BLE001 — wrap-and-rethrow for the lifespan
        raise StateDictLoadError(
            "failed to load state_dict into DistilBertForSequenceClassification: "
            f"{exc}"
        ) from exc
    model.eval()
    return LoadedModel(model=model, tokenizer=tokenizer, id2label=verified.id2label)


def predict(loaded: LoadedModel, title: str, body: str) -> Prediction:
    text = f"{title}\n\n{body}"
    enc = loaded.tokenizer(
        text,
        truncation=True,
        padding=False,
        max_length=MAX_LENGTH,
        return_tensors="pt",
    )
    with torch.no_grad():
        logits = loaded.model(**enc).logits[0]
    probs = torch.softmax(logits, dim=-1)
    top = int(torch.argmax(probs).item())
    label = loaded.id2label[top]
    label_scores = {
        loaded.id2label[i]: float(probs[i].item()) for i in range(probs.shape[0])
    }
    return Prediction(
        label=label,
        confidence=float(probs[top].item()),
        label_scores=label_scores,
    )
