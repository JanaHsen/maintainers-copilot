"""Online query embedding for /retrieve.

Loads `BAAI/bge-base-en-v1.5` via sentence-transformers at boot. The
api calls `/embed` once per `/retrieve` request to embed the
(HyDE-transformed) query — bulk offline corpus embedding lives in
`scripts/rag/embed_and_upsert.py`, NOT here (see research.md R3).

The model is loaded eagerly by the lifespan so the first request
doesn't pay a multi-second cold-start cost; an early load failure is
a refuse-to-boot (`REFUSE TO BOOT: embedding model failed to load`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sentence_transformers import SentenceTransformer

logger = logging.getLogger("model_server.embed")

EMBED_MODEL_ID = "BAAI/bge-base-en-v1.5"
EMBED_DIM = 768


class EmbeddingModelLoadError(RuntimeError):
    """SentenceTransformer(...) failed at boot — refuse to boot."""


@dataclass(frozen=True)
class LoadedEmbedder:
    model: SentenceTransformer
    model_id: str
    dim: int


def load_embedder() -> LoadedEmbedder:
    """Eager-load the embedding model from the pre-cached HF directory."""
    try:
        model = SentenceTransformer(EMBED_MODEL_ID)
        dim = int(model.get_sentence_embedding_dimension() or 0)
    except Exception as exc:  # noqa: BLE001 — wrap-and-rethrow for the lifespan
        raise EmbeddingModelLoadError(
            f"failed to load {EMBED_MODEL_ID}: {exc}"
        ) from exc
    if dim != EMBED_DIM:
        raise EmbeddingModelLoadError(
            f"{EMBED_MODEL_ID} reported dim={dim}, expected {EMBED_DIM} "
            f"(matches the vector(768) column in 0002_rag_chunks)"
        )
    logger.info("embedding model loaded: %s (dim=%d)", EMBED_MODEL_ID, dim)
    return LoadedEmbedder(model=model, model_id=EMBED_MODEL_ID, dim=dim)


def embed_one(loaded: LoadedEmbedder, text: str) -> list[float]:
    """Embed a single query string. Returns a 768-dim float list."""
    vec = loaded.model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return [float(x) for x in vec]


def embed_batch(loaded: LoadedEmbedder, texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings; same model, same normalization as embed_one."""
    if not texts:
        return []
    vectors = loaded.model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [[float(x) for x in v] for v in vectors]
