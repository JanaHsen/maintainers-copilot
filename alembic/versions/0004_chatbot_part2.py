"""chatbot part 2: composite partial index on audit_log for admin queries

Revision ID: 0004_chatbot_part2
Revises: 0003_chatbot
Create Date: 2026-05-23

Single additive change: a composite partial index on ``audit_log
(actor_user_id, action, timestamp DESC)`` restricted to rows with a
non-NULL ``actor_user_id``. This speeds up the Part 3 admin-panel
"list this user's audit entries by action and most-recent first" query.

The index is partial (``WHERE actor_user_id IS NOT NULL``) so widget-only
rows — which always have ``actor_user_id IS NULL`` per Part 1's
``audit_log`` evolution — do not bloat the index. ``downgrade()`` drops
the index. No data changes either way.

Rule 3 (storage). See ``specs/003-chatbot-part2-brain/data-model.md``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_chatbot_part2"
down_revision: str | None = "0003_chatbot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_log_actor_action_time",
        "audit_log",
        [
            sa.text("actor_user_id"),
            sa.text("action"),
            sa.text('"timestamp" DESC'),
        ],
        postgresql_where=sa.text("actor_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_actor_action_time", table_name="audit_log")
