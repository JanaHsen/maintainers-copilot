"""Process-global holder for artifacts and the loaded torch model.

Populated by the lifespan in two stages: ``set_artifacts`` after the
boot integrity check, then ``set_model`` after the state_dict has been
loaded into the architecture. Routers read via ``get_model``; if the
model isn't loaded the caller surfaces it as 503/typed-error (Rule 11).

The RAG embedder and cross-encoder live in the same process and are
held in this module's globals too (set by the lifespan, read by the
/embed and /rerank routers).
"""

from __future__ import annotations

from model_server.boot_check import VerifiedArtifacts
from model_server.embed import LoadedEmbedder
from model_server.inference import LoadedModel
from model_server.rerank import LoadedReranker

_artifacts: VerifiedArtifacts | None = None
_model: LoadedModel | None = None
_embedder: LoadedEmbedder | None = None
_reranker: LoadedReranker | None = None


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


def set_embedder(embedder: LoadedEmbedder) -> None:
    global _embedder
    _embedder = embedder


def get_embedder() -> LoadedEmbedder:
    if _embedder is None:
        raise RuntimeError("embedder requested before it was loaded")
    return _embedder


def set_reranker(reranker: LoadedReranker) -> None:
    global _reranker
    _reranker = reranker


def get_reranker() -> LoadedReranker:
    if _reranker is None:
        raise RuntimeError("reranker requested before it was loaded")
    return _reranker


def clear_artifacts() -> None:
    """Drop the in-memory artifacts and models (used by shutdown and by tests)."""
    global _artifacts, _model, _embedder, _reranker
    _artifacts = None
    _model = None
    _embedder = None
    _reranker = None
