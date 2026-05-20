"""Prove the seven refuse-to-boot mismatch types each surface their own error.

Rule 4: an integrity failure must be loud and fatal. These tests exercise
the public ``verify_artifacts`` entry point with an in-memory fake storage
so the verification logic — not the boto3 wiring — is what's under test.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pytest

from model_server.boot_check import (
    EXPECTED_LABEL2ID,
    Label2IdMismatchError,
    ModelCardMissingError,
    ModelCardSchemaError,
    TrainingDataHashMismatchError,
    TrainParquetMissingError,
    VerifiedArtifacts,
    WeightsHashMismatchError,
    WeightsMissingError,
    verify_artifacts,
)
from model_server.storage import ArtifactNotFoundError

WEIGHTS = b"\x00fake-state-dict-bytes\x00"
WEIGHTS_SHA = hashlib.sha256(WEIGHTS).hexdigest()


def _train_df() -> pd.DataFrame:
    # Six rows is enough to make the pandas object hash deterministic;
    # the test doesn't care what the hash value is, only that the same
    # bytes hash to the same hex on both ends of verify.
    return pd.DataFrame(
        {
            "issue_number": [1, 2, 3, 4, 5, 6],
            "target_class": ["bug", "feature", "docs", "question", "bug", "feature"],
        }
    )


def _train_parquet_bytes() -> bytes:
    buf = io.BytesIO()
    _train_df().to_parquet(buf)
    return buf.getvalue()


def _train_data_hash(df: pd.DataFrame) -> str:
    return hashlib.sha256(
        pd.util.hash_pandas_object(
            df[["issue_number", "target_class"]], index=False
        ).to_numpy().tobytes()
    ).hexdigest()


def _well_formed_model_card() -> dict[str, Any]:
    return {
        "architecture": {
            "name": "distilbert-base-uncased",
            "label2id": dict(EXPECTED_LABEL2ID),
            "id2label": {str(v): k for k, v in EXPECTED_LABEL2ID.items()},
        },
        "data": {
            "training_data_hash": _train_data_hash(_train_df()),
        },
        "weights": {
            "weights_sha256": WEIGHTS_SHA,
        },
        "hyperparameters": {"max_length": 512},
    }


@dataclass
class FakeStorage:
    model_card_bytes: bytes | None
    state_dict_bytes: bytes | None
    train_parquet_bytes: bytes | None

    def read_model_card(self) -> bytes:
        if self.model_card_bytes is None:
            raise ArtifactNotFoundError("model_card.json missing (fake)")
        return self.model_card_bytes

    def read_state_dict(self) -> bytes:
        if self.state_dict_bytes is None:
            raise ArtifactNotFoundError("state_dict.pt missing (fake)")
        return self.state_dict_bytes

    def read_train_parquet(self) -> bytes:
        if self.train_parquet_bytes is None:
            raise ArtifactNotFoundError("train.parquet missing (fake)")
        return self.train_parquet_bytes


def _storage(
    *,
    model_card: dict[str, Any] | None | str = "default",
    state_dict: bytes | None = WEIGHTS,
    train_parquet: bytes | None | object = "default",
) -> FakeStorage:
    card_bytes: bytes | None
    if model_card == "default":
        card_bytes = json.dumps(_well_formed_model_card()).encode()
    elif model_card is None:
        card_bytes = None
    elif isinstance(model_card, str):
        card_bytes = model_card.encode()
    else:
        card_bytes = json.dumps(model_card).encode()

    parquet_bytes: bytes | None
    if train_parquet == "default":
        parquet_bytes = _train_parquet_bytes()
    else:
        # Allow tests to inject bytes or None explicitly.
        parquet_bytes = train_parquet  # type: ignore[assignment]

    return FakeStorage(
        model_card_bytes=card_bytes,
        state_dict_bytes=state_dict,
        train_parquet_bytes=parquet_bytes,
    )


def test_happy_path_returns_verified_artifacts() -> None:
    result = verify_artifacts(_storage())
    assert isinstance(result, VerifiedArtifacts)
    assert result.label2id == EXPECTED_LABEL2ID
    assert result.id2label == {0: "bug", 1: "docs", 2: "feature", 3: "question"}
    assert result.weights_bytes == WEIGHTS


def test_missing_model_card_raises_specific_error() -> None:
    with pytest.raises(ModelCardMissingError):
        verify_artifacts(_storage(model_card=None))


def test_invalid_json_model_card_raises_schema_error() -> None:
    with pytest.raises(ModelCardSchemaError):
        verify_artifacts(_storage(model_card="not-json{"))


def test_model_card_missing_architecture_raises_schema_error() -> None:
    bad = _well_formed_model_card()
    del bad["architecture"]
    with pytest.raises(ModelCardSchemaError):
        verify_artifacts(_storage(model_card=bad))


def test_label2id_mismatch_raises_specific_error() -> None:
    bad = _well_formed_model_card()
    # swap two indices — same keys, different ids
    bad["architecture"]["label2id"] = {
        "bug": 1,
        "docs": 0,
        "feature": 2,
        "question": 3,
    }
    with pytest.raises(Label2IdMismatchError):
        verify_artifacts(_storage(model_card=bad))


def test_missing_state_dict_raises_specific_error() -> None:
    with pytest.raises(WeightsMissingError):
        verify_artifacts(_storage(state_dict=None))


def test_weights_sha_mismatch_raises_specific_error() -> None:
    with pytest.raises(WeightsHashMismatchError):
        verify_artifacts(_storage(state_dict=WEIGHTS + b"tampered"))


def test_missing_train_parquet_raises_specific_error() -> None:
    with pytest.raises(TrainParquetMissingError):
        verify_artifacts(_storage(train_parquet=None))


def test_training_data_hash_mismatch_raises_specific_error() -> None:
    # Different rows -> different pandas object hash -> mismatch.
    other_df = pd.DataFrame(
        {
            "issue_number": [99, 100],
            "target_class": ["bug", "docs"],
        }
    )
    buf = io.BytesIO()
    other_df.to_parquet(buf)
    with pytest.raises(TrainingDataHashMismatchError):
        verify_artifacts(_storage(train_parquet=buf.getvalue()))
