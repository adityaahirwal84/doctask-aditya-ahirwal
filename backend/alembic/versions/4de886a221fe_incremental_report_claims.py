"""incremental report claims

Revision ID: 4de886a221fe
Revises: c6e1f67be212
Create Date: 2026-08-08 17:21:19.350590

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4de886a221fe'
down_revision: Union[str, Sequence[str], None] = 'c6e1f67be212'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOTE: autogenerate also proposed dropping the checkpoint/checkpoint_blobs/
# checkpoint_migrations/checkpoint_writes tables, because they belong to
# LangGraph's AsyncPostgresSaver (see app/graph/checkpointer.py) and are not
# part of this project's SQLAlchemy metadata. Alembic must never manage
# those tables - dropping them would destroy every run's crash-recovery
# state - so those operations are deliberately excluded below.


def upgrade() -> None:
    op.add_column('report_claims', sa.Column('fact_key', sa.String(length=128), nullable=True))
    op.add_column('report_claims', sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column('report_claims', 'is_current', server_default=None)
    op.create_index(op.f('ix_report_claims_fact_key'), 'report_claims', ['fact_key'], unique=False)
    op.alter_column('reports', 'run_id', existing_type=sa.UUID(), nullable=True)
    op.drop_constraint(op.f('reports_run_id_fkey'), 'reports', type_='foreignkey')
    op.create_foreign_key(None, 'reports', 'workflow_runs', ['run_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(None, 'reports', type_='foreignkey')
    op.create_foreign_key(op.f('reports_run_id_fkey'), 'reports', 'workflow_runs', ['run_id'], ['id'], ondelete='CASCADE')
    op.alter_column('reports', 'run_id', existing_type=sa.UUID(), nullable=False)
    op.drop_index(op.f('ix_report_claims_fact_key'), table_name='report_claims')
    op.drop_column('report_claims', 'is_current')
    op.drop_column('report_claims', 'fact_key')
