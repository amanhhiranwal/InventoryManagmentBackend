"""multi role company

Revision ID: 170000000000
Revises: a05976fe60e7
Create Date: 2026-07-15 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '170000000000'
down_revision: Union[str, Sequence[str], None] = 'a05976fe60e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create user_roles table
    op.create_table('user_roles',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    # Add indexes for scaling
    op.create_index('ix_user_roles_user_id', 'user_roles', ['user_id'], unique=False)
    op.create_index('ix_user_roles_role_id', 'user_roles', ['role_id'], unique=False)

    # 2. Create user_companies table
    op.create_table('user_companies',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    # Add indexes for scaling
    op.create_index('ix_user_companies_user_id', 'user_companies', ['user_id'], unique=False)
    op.create_index('ix_user_companies_company_id', 'user_companies', ['company_id'], unique=False)

    # Add role_permissions indexes for scaling (not present in initial migration)
    op.create_index('ix_role_permissions_role_id', 'role_permissions', ['role_id'], unique=False)
    op.create_index('ix_role_permissions_permission_id', 'role_permissions', ['permission_id'], unique=False)

    # 3. Migrate existing user -> role mapping
    connection = op.get_bind()
    users = connection.execute(sa.text("SELECT id, role_id FROM users")).fetchall()
    import uuid
    from datetime import datetime
    for user_id, role_id in users:
        if user_id and role_id:
            ur_id = str(uuid.uuid4())
            now = datetime.utcnow()
            connection.execute(
                sa.text("INSERT INTO user_roles (id, user_id, role_id, created_at, updated_at) VALUES (:id, :user_id, :role_id, :now, :now)"),
                {"id": ur_id, "user_id": user_id, "role_id": role_id, "now": now}
            )

    # 4. Drop role_id column from users table
    op.drop_constraint('users_role_id_fkey', 'users', type_='foreignkey')
    op.drop_column('users', 'role_id')


def downgrade() -> None:
    # 1. Re-add role_id to users
    op.add_column('users', sa.Column('role_id', sa.UUID(), nullable=True))
    op.create_foreign_key('users_role_id_fkey', 'users', 'roles', ['role_id'], ['id'])

    # 2. Populate role_id from user_roles
    connection = op.get_bind()
    user_roles = connection.execute(sa.text("SELECT user_id, role_id FROM user_roles")).fetchall()
    for user_id, role_id in user_roles:
        connection.execute(
            sa.text("UPDATE users SET role_id = :role_id WHERE id = :user_id"),
            {"role_id": role_id, "user_id": user_id}
        )

    # 3. Drop tables and indexes
    op.drop_table('user_companies')
    op.drop_table('user_roles')
    op.drop_index('ix_role_permissions_role_id', 'role_permissions')
    op.drop_index('ix_role_permissions_permission_id', 'role_permissions')
