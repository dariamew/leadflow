"""Scope qualification history to the user who launched the analysis."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # Existing records have no reliable author; keep them visible to admins only.
    with op.batch_alter_table("qualification_runs") as batch:
        batch.add_column(sa.Column("created_by_user_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("created_by_username", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_qualification_created_by",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_qualification_runs_created_by_user_id", ["created_by_user_id"])


def downgrade():
    with op.batch_alter_table("qualification_runs") as batch:
        batch.drop_index("ix_qualification_runs_created_by_user_id")
        batch.drop_constraint("fk_qualification_created_by", type_="foreignkey")
        batch.drop_column("created_by_username")
        batch.drop_column("created_by_user_id")
