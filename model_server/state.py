"""Process-global holder for artifacts and the loaded torch model.

Populated by the lifespan in two stages: ``set_artifacts`` after the
boot integrity check, then ``set_model`` after the state_dict has been
loaded into the architecture. Routers read via ``get_model``; if the
model isn't loaded the caller surfaces it as 503/typed-error (Rule 11).
"""

from __future__ import annotations

from model_server.boot_check import VerifiedArtifacts
from model_server.inference import LoadedModel

_artifacts: VerifiedArtifacts | None = None
_model: LoadedModel | None = None


def set_artifacts(artifacts: VerifiedArtifacts) -> None:
    global _artifacts
    _artifacts = artifacts


def get_artifacts() -> VerifiedArtifacts:
    if _artifacts is None:
        raise RuntimeError(
            "model artifacts requested before boot verification completed"
        )
    return _artifacts


def set_model(model: LoadedModel) -> None:
    global _model
    _model = model


def get_model() -> LoadedModel:
    if _model is None:
        raise RuntimeError("model requested before it was loaded")
    return _model


def is_model_loaded() -> bool:
    return _model is not None


def clear_artifacts() -> None:
    """Drop the in-memory artifacts and model (used by shutdown and by tests)."""
    global _artifacts, _model
    _artifacts = None
    _model = None
