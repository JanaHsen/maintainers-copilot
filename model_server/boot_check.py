"""Artifact-integrity verification run at model-server startup (Rule 4).

Four checks are performed against the published Colab artifact and the
canonical training dataset; any mismatch is fatal and the process refuses
to boot with a single specific log line per mismatch type:

  1. ``model_card.json`` is present and parses.
  2. ``architecture.label2id`` equals the compiled-in expectation
     ``{bug: 0, docs: 1, feature: 2, question: 3}`` — the class id
     mapping is part of the API contract and must not silently change.
  3. SHA-256 of ``state_dict.pt`` equals ``weights.weights_sha256`` from
     the model card (Day 1 contract C6, now enforced at boot).
  4. SHA-256 of train.parquet bytes equals ``data.training_data_hash``
     from the model card. Originally this used pandas'
     ``hash_pandas_object`` on the row content to match the Colab notebook,
     but that hash isn't stable across pandas major versions and the
     serving image disagreed with Colab on the same bytes. File-bytes
     SHA-256 catches every tamper of train.parquet and is
     version-independent; ``data.training_data_hash`` is the SHA-256 of
     the published parquet.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from model_server.storage import ArtifactNotFoundError, ArtifactStorage

EXPECTED_LABEL2ID: dict[str, int] = {
    "bug": 0,
    "docs": 1,
    "feature": 2,
    "question": 3,
}


class ArtifactIntegrityError(RuntimeError):
    """Base for boot-time artifact-integrity failures (refuse-to-boot)."""


class ModelCardMissingError(ArtifactIntegrityError):
    """model_card.json is absent from MinIO at the configured run id."""


class ModelCardSchemaError(ArtifactIntegrityError):
    """model_card.json is present but doesn't have the expected shape."""


class WeightsMissingError(ArtifactIntegrityError):
    """state_dict.pt is absent from MinIO at the configured run id."""


class TrainParquetMissingError(ArtifactIntegrityError):
    """train.parquet is absent from MinIO at the configured dataset run id."""


class Label2IdMismatchError(ArtifactIntegrityError):
    """architecture.label2id differs from the compiled-in expectation."""


class WeightsHashMismatchError(ArtifactIntegrityError):
    """state_dict.pt SHA-256 differs from model_card weights.weights_sha256."""


class TrainingDataHashMismatchError(ArtifactIntegrityError):
    """Recomputed train.parquet hash differs from model_card data.training_data_hash."""


@dataclass(frozen=True)
class VerifiedArtifacts:
    """Carrier for artifacts that passed verification, consumed by inference."""

    model_card: dict[str, Any]
    id2label: dict[int, str]
    label2id: dict[str, int]
    weights_bytes: bytes


def _require_dict(model_card: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    cursor: Any = model_card
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            raise ModelCardSchemaError(
                f"model_card.json missing required key '{'.'.join(path)}'"
            )
        cursor = cursor[key]
    if not isinstance(cursor, dict):
        raise ModelCardSchemaError(
            f"model_card.json key '{'.'.join(path)}' is not an object"
        )
    return cursor


def _require_str(model_card: dict[str, Any], path: tuple[str, ...]) -> str:
    cursor: Any = model_card
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            raise ModelCardSchemaError(
                f"model_card.json missing required key '{'.'.join(path)}'"
            )
        cursor = cursor[key]
    if not isinstance(cursor, str):
        raise ModelCardSchemaError(
            f"model_card.json key '{'.'.join(path)}' is not a string"
        )
    return cursor


def _load_model_card(storage: ArtifactStorage) -> dict[str, Any]:
    try:
        raw = storage.read_model_card()
    except ArtifactNotFoundError as exc:
        raise ModelCardMissingError(str(exc)) from exc
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelCardSchemaError(f"model_card.json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ModelCardSchemaError("model_card.json is not a JSON object")
    return parsed


def _check_label2id(model_card: dict[str, Any]) -> dict[str, int]:
    architecture = _require_dict(model_card, ("architecture",))
    raw = architecture.get("label2id")
    if not isinstance(raw, dict):
        raise ModelCardSchemaError(
            "model_card.json key 'architecture.label2id' is not an object"
        )
    actual: dict[str, int] = {}
    for label, idx in raw.items():
        if not isinstance(label, str) or not isinstance(idx, int):
            raise ModelCardSchemaError(
                "model_card.json 'architecture.label2id' must map str→int"
            )
        actual[label] = idx
    if actual != EXPECTED_LABEL2ID:
        raise Label2IdMismatchError(
            f"model_card label2id {actual} does not match expected {EXPECTED_LABEL2ID}"
        )
    return actual


def _check_weights_sha(model_card: dict[str, Any], storage: ArtifactStorage) -> bytes:
    expected_sha = _require_str(model_card, ("weights", "weights_sha256"))
    try:
        weights_bytes = storage.read_state_dict()
    except ArtifactNotFoundError as exc:
        raise WeightsMissingError(str(exc)) from exc
    actual_sha = hashlib.sha256(weights_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise WeightsHashMismatchError(
            f"state_dict.pt SHA-256 mismatch — model_card says {expected_sha}, "
            f"computed {actual_sha}"
        )
    return weights_bytes


def _compute_training_data_hash(train_parquet_bytes: bytes) -> str:
    """SHA-256 of the train.parquet file bytes.

    Earlier this used ``pd.util.hash_pandas_object`` on the row content, to
    match the Colab notebook. That hash is not stable across pandas major
    versions — Colab and the serving image disagreed in CI, even on the
    same parquet bytes. File-bytes SHA-256 is version-independent and
    catches every tamper of train.parquet, which is what the check
    actually needs to do. ``model_card.data.training_data_hash`` is the
    SHA-256 of the published parquet bytes; if the parquet on MinIO
    differs from the one used at training time, the boot refuses.
    """
    return hashlib.sha256(train_parquet_bytes).hexdigest()


def _check_training_data_hash(
    model_card: dict[str, Any], storage: ArtifactStorage
) -> None:
    expected_hash = _require_str(model_card, ("data", "training_data_hash"))
    try:
        train_bytes = storage.read_train_parquet()
    except ArtifactNotFoundError as exc:
        raise TrainParquetMissingError(str(exc)) from exc
    actual_hash = _compute_training_data_hash(train_bytes)
    if actual_hash != expected_hash:
        raise TrainingDataHashMismatchError(
            f"train.parquet hash mismatch — model_card says {expected_hash}, "
            f"computed {actual_hash}"
        )


def _read_id2label(model_card: dict[str, Any]) -> dict[int, str]:
    architecture = _require_dict(model_card, ("architecture",))
    raw = architecture.get("id2label")
    if not isinstance(raw, dict):
        raise ModelCardSchemaError(
            "model_card.json key 'architecture.id2label' is not an object"
        )
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            idx = int(k)
        except (TypeError, ValueError) as exc:
            raise ModelCardSchemaError(
                "model_card.json 'architecture.id2label' keys must be int-coercible"
            ) from exc
        if not isinstance(v, str):
            raise ModelCardSchemaError(
                "model_card.json 'architecture.id2label' values must be strings"
            )
        out[idx] = v
    return out


def verify_artifacts(storage: ArtifactStorage) -> VerifiedArtifacts:
    """Run all four integrity checks; raise the first failure encountered."""
    model_card = _load_model_card(storage)
    label2id = _check_label2id(model_card)
    weights_bytes = _check_weights_sha(model_card, storage)
    _check_training_data_hash(model_card, storage)
    id2label = _read_id2label(model_card)
    return VerifiedArtifacts(
        model_card=model_card,
        id2label=id2label,
        label2id=label2id,
        weights_bytes=weights_bytes,
    )
