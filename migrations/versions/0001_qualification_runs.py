"""Store source snapshots, qualification results and approval state."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "qualification_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False, unique=True),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("extractor", sa.String(20), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("applied_attributes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_qualification_runs_lead_id", "qualification_runs", ["lead_id"])
    op.create_index("ix_qualification_runs_created_at", "qualification_runs", ["created_at"])


def downgrade():
    op.drop_index("ix_qualification_runs_created_at", table_name="qualification_runs")
    op.drop_index("ix_qualification_runs_lead_id", table_name="qualification_runs")
    op.drop_table("qualification_runs")
