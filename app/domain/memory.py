"""Pydantic domain models for long-term chatbot memory.

Aligned with ``specs/002-chatbot-part1-foundations/contracts/memory-tools.md``.
Repository layer (``app/repositories/memory_repository.py``) returns
``MemoryRecallHit`` rows; service layer (``app/services/tools/*``) consumes
them.

The full set of types (``Memory``, ``MemoryWriteResult``) is rounded out by
T013 when the rest of the domain modules land. ``MemoryRecallHit`` is here
now so the memory repository can import it without a forward reference.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


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
