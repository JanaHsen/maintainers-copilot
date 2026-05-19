"""The only place /health touches SQL (Rule 1).

A trivial liveness query plus a pgvector-presence probe; no ORM models are
needed for Day 1 health.
"""

from sqlalchemy import text

from app.infra.database import get_engine


def select_one() -> None:
    """Liveness query; raises if Postgres is unreachable."""
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def pgvector_present() -> bool:
    """True iff the ``vector`` extension is installed (baseline migration)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).first()
    return row is not None
