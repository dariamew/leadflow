"""Persist the administrator-selected CRM source field per configured portal."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bitrix_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text_field", sa.String(128)),
        sa.Column("portal_key", sa.String(64)),
    )
    # NULL preserves the existing BITRIX_TEXT_FIELD until an admin saves a choice.
    op.execute("INSERT INTO bitrix_source (id) VALUES (1)")


def downgrade():
    op.drop_table("bitrix_source")
