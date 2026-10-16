"""Distinguish password setup invitations from recovery links."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("password_invitations") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(20), nullable=False, server_default="setup")
        )
        batch.create_check_constraint("ck_invitation_purpose", "purpose IN ('setup', 'reset')")


def downgrade():
    op.execute("DELETE FROM password_invitations WHERE purpose = 'reset'")
    with op.batch_alter_table("password_invitations") as batch:
        batch.drop_constraint("ck_invitation_purpose", type_="check")
        batch.drop_column("purpose")
