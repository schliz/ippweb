"""Initial migration - users and print_jobs

Revision ID: 001_initial
Revises: 
Create Date: 2026-01-19

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create users table
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sub', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('preferred_username', sa.String(length=255), nullable=True),
        sa.Column('first_login', sa.DateTime(), nullable=False),
        sa.Column('last_login', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_sub'), 'users', ['sub'], unique=True)
    
    # Create print_jobs table
    op.create_table('print_jobs',
        sa.Column('id', sa.String(length=22), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cups_job_id', sa.Integer(), nullable=True),
        sa.Column('printer_name', sa.String(length=255), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column('pages_printed', sa.Integer(), nullable=False),
        sa.Column('color_mode', sa.Enum('RGB', 'GRAY', name='colormode'), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'HELD', 'PROCESSING', 'COMPLETED', 'CANCELED', 'ABORTED', 'TIMED_OUT', name='jobstatus'), nullable=False),
        sa.Column('status_message', sa.String(length=500), nullable=True),
        sa.Column('cups_unreachable', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_print_jobs_user_id'), 'print_jobs', ['user_id'], unique=False)
    op.create_index(op.f('ix_print_jobs_cups_job_id'), 'print_jobs', ['cups_job_id'], unique=False)
    op.create_index(op.f('ix_print_jobs_created_at'), 'print_jobs', ['created_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_print_jobs_created_at'), table_name='print_jobs')
    op.drop_index(op.f('ix_print_jobs_cups_job_id'), table_name='print_jobs')
    op.drop_index(op.f('ix_print_jobs_user_id'), table_name='print_jobs')
    op.drop_table('print_jobs')
    op.drop_index(op.f('ix_users_sub'), table_name='users')
    op.drop_table('users')
