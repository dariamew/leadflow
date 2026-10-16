"""Durable lead polling cursor and qualification queue."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "qualification_runs",
        sa.Column("trigger", sa.String(20), server_default="manual", nullable=False),
    )
    op.create_table(
        "lead_monitor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("portal_key", sa.String(64)),
        sa.Column("cursor", sa.Integer()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True)),
        sa.Column("last_poll_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(80)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        "INSERT INTO lead_monitor (id,enabled,interval_seconds,revision) VALUES (1,false,60,0)"
    )
    op.create_table(
        "lead_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("portal_key", sa.String(64), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(80)),
        sa.Column("claim_token", sa.String(36)),
        sa.Column(
            "run_id", sa.String(36), sa.ForeignKey("qualification_runs.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("portal_key", "lead_id", name="uq_lead_job_portal_lead"),
    )
    op.create_index("ix_lead_jobs_status", "lead_jobs", ["status"])
    op.create_index("ix_lead_jobs_available_at", "lead_jobs", ["available_at"])


def downgrade():
    op.drop_table("lead_jobs")
    op.drop_table("lead_monitor")
    with op.batch_alter_table("qualification_runs") as batch:
        batch.drop_column("trigger")
