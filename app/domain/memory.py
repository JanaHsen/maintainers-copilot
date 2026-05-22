"""Pydantic domain models for long-term chatbot memory.

Aligned with ``specs/002-chatbot-part1-foundations/contracts/memory-tools.md``.

Three shapes:

  * :class:`Memory` — a stored memory row, returned by future ``/memory/*``
    endpoints (Part 3).
  * :class:`MemoryWriteResult` — the success payload returned by the
    ``write_memory`` tool (the error variant is a dataclass next to the
    tool itself, per the contract).
  * :class:`MemoryRecallHit` — one row from
    ``memory_repository.query_top_k``; ``similarity`` is cosine similarity
    in ``[-1, 1]`` derived from pgvector's distance (``1 - <=>``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Memory(BaseModel):
    """One stored memory row.

    Mirrors the ``chatbot_memories`` columns minus the raw embedding vector
    (which is internal to the recall path). ``source`` is constrained to
    ``'episodic'`` by the DB CHECK in Part 1; the literal is left open as a
    plain string here to ease forward-compat when the CHECK loosens.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    content: str
    source: Literal["episodic"] = "episodic"
    created_at: datetime


class MemoryWriteResult(BaseModel):
    """Success payload returned by the ``write_memory`` tool (T020).

    The error variant of the same outcome lives in
    ``app/services/tools/write_memory_tool.py`` as a frozen dataclass per
    the contract — keeping the typed-outcome shape there rather than here
    so domain types stay pure data and the tool owns its failure-mode enum.
    """

    memory_id: uuid.UUID


class MemoryRecallHit(BaseModel):
    """One row returned by ``memory_repository.query_top_k``.

    ``similarity`` is cosine similarity in ``[-1, 1]`` derived from pgvector's
    cosine distance (``1 - (embedding <=> query_vec)``). The repository
    populates this; nothing else writes to the field.
    """

    memory_id: uuid.UUID
    content: str
    created_at: datetime
    similarity: float
