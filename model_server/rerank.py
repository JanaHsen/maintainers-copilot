"""Online cross-encoder rerank for /retrieve.

Loads `cross-encoder/ms-marco-MiniLM-L-6-v2` at boot. The api calls
`/rerank` once per `/retrieve` request with a 30-candidate batch; the
cross-encoder scores all (query, candidate) pairs in one forward pass
on CPU.

Load failure is a refuse-to-boot
(`REFUSE TO BOOT: cross-encoder failed to load`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sentence_transformers import CrossEncoder

logger = logging.getLogger("model_server.rerank")

RERANK_MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class RerankerModelLoadError(RuntimeError):
    """CrossEncoder(...) failed at boot — refuse to boot."""


@dataclass(frozen=True)
class LoadedReranker:
    model: CrossEncoder
    model_id: str


def load_reranker() -> LoadedReranker:
    try:
        model = CrossEncoder(RERANK_MODEL_ID)
    except Exception as exc:  # noqa: BLE001 — wrap-and-rethrow for the lifespan
        raise RerankerModelLoadError(
            f"failed to load {RERANK_MODEL_ID}: {exc}"
        ) from exc
    logger.info("cross-encoder loaded: %s", RERANK_MODEL_ID)
    return LoadedReranker(model=model, model_id=RERANK_MODEL_ID)


def rerank(
    loaded: LoadedReranker,
    *,
    query: str,
    candidates: list[tuple[str, str]],
) -> list[tuple[str, float]]:
    """Score every (query, candidate_text) pair, return [(id, score), ...].

    Order matches the input. Higher score = more relevant. The cross-encoder
    returns float relevance scores (NOT probabilities) — the api sorts
    client-side.
    """
    if not candidates:
        return []
    pairs = [[query, text] for _id, text in candidates]
    scores = loaded.model.predict(pairs, show_progress_bar=False)
    return [(candidates[i][0], float(scores[i])) for i in range(len(candidates))]
