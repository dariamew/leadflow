"""Add roles and invitations; bootstrap the oldest active account as administrator."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("role", sa.String(20), server_default="manager", nullable=False))
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=True)
        batch.create_check_constraint("ck_users_role", "role IN ('admin', 'manager')")
    users = sa.table(
        "users",
        sa.column("id"),
        sa.column("role"),
        sa.column("is_active"),
        sa.column("password_hash"),
        sa.column("created_at"),
    )
    connection = op.get_bind()
    first = connection.execute(
        sa.select(users.c.id)
        .where(users.c.is_active == sa.true(), users.c.password_hash.is_not(None))
        .order_by(users.c.created_at, users.c.id)
        .limit(1)
    ).scalar()
    if first:
        connection.execute(users.update().where(users.c.id == first).values(role="admin"))
    lock = op.create_table(
        "user_admin_lock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.bulk_insert(lock, [{"id": 1, "version": 0}])
    op.create_table(
        "password_invitations",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_password_invitations_expires_at", "password_invitations", ["expires_at"])


def downgrade():
    op.drop_table("password_invitations")
    op.drop_table("user_admin_lock")
    # Pending accounts stay unable to log in after a downgrade.
    op.execute("UPDATE users SET password_hash = '!' WHERE password_hash IS NULL")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_column("role")
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=False)
