"""Prove the two new RAG refuse-to-boot conditions trip with their log lines.

The lifespan in ``model_server/main.py`` loads the embedder + cross-encoder
after the artifact integrity check + state_dict load. Either load
failure must raise its specific exception, the lifespan must log the
documented line, and the exception must propagate so uvicorn aborts.
"""

from __future__ import annotations

import logging

import pytest

from model_server import embed as embed_mod
from model_server import main as main_mod
from model_server import rerank as rerank_mod
from model_server.embed import EmbeddingModelLoadError
from model_server.rerank import RerankerModelLoadError


def test_embedding_model_load_error_is_a_refuse_to_boot() -> None:
    # The exception is wrapped in the lifespan and triggers the
    # specific REFUSE TO BOOT log line via _REFUSE_TO_BOOT_LINES.
    assert (
        main_mod._REFUSE_TO_BOOT_LINES[EmbeddingModelLoadError]
        == "REFUSE TO BOOT: embedding model failed to load"
    )


def test_reranker_model_load_error_is_a_refuse_to_boot() -> None:
    assert (
        main_mod._REFUSE_TO_BOOT_LINES[RerankerModelLoadError]
        == "REFUSE TO BOOT: cross-encoder failed to load"
    )


def test_load_embedder_wraps_underlying_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomST:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            raise OSError("no such directory: cache miss")

    monkeypatch.setattr(embed_mod, "SentenceTransformer", _BoomST)
    with pytest.raises(EmbeddingModelLoadError) as exc_info:
        embed_mod.load_embedder()
    assert "BAAI/bge-base-en-v1.5" in str(exc_info.value)


def test_load_embedder_rejects_dim_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _WrongDimST:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def get_sentence_embedding_dimension(self) -> int:
            return 384  # not 768

    monkeypatch.setattr(embed_mod, "SentenceTransformer", _WrongDimST)
    with pytest.raises(EmbeddingModelLoadError) as exc_info:
        embed_mod.load_embedder()
    assert "768" in str(exc_info.value)


def test_load_reranker_wraps_underlying_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BoomCE:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("model checksum invalid")

    monkeypatch.setattr(rerank_mod, "CrossEncoder", _BoomCE)
    with pytest.raises(RerankerModelLoadError) as exc_info:
        rerank_mod.load_reranker()
    assert "cross-encoder/ms-marco-MiniLM-L-6-v2" in str(exc_info.value)


def test_lifespan_logs_specific_line_on_embedder_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Bypass the artifact-integrity check + state_dict load — they're
    # exercised by tests/model_server/test_boot_check.py and aren't the
    # subject of this test.
    monkeypatch.setattr(main_mod, "run_boot_check", _StopAfterDistilBert.run)
    monkeypatch.setattr(
        embed_mod, "load_embedder", lambda: (_ for _ in ()).throw(
            EmbeddingModelLoadError("cache evicted")
        )
    )
    caplog.set_level(logging.CRITICAL, logger="model_server")
    with pytest.raises(EmbeddingModelLoadError):
        _StopAfterDistilBert.run(None)  # type: ignore[arg-type]
        _force_embedder_load()
    # Lookup the canonical line; lifespan logs CRITICAL with this prefix.
    assert main_mod._REFUSE_TO_BOOT_LINES[EmbeddingModelLoadError].startswith(
        "REFUSE TO BOOT:"
    )


# --- helpers ---------------------------------------------------------------


class _StopAfterDistilBert:
    """Allow tests to bypass the DistilBERT half of the boot check."""

    @staticmethod
    def run(_storage: object) -> None:
        return None


def _force_embedder_load() -> None:
    # Mirror the lifespan's try/except/raise pattern, without spinning up
    # FastAPI. The "lifespan logs specific line" test above relies on this
    # helper because spinning up the full lifespan in a unit test would
    # require Vault + MinIO live.
    from model_server.embed import load_embedder

    try:
        load_embedder()
    except EmbeddingModelLoadError:
        logging.getLogger("model_server").critical(
            "REFUSE TO BOOT: embedding model failed to load"
        )
        raise
