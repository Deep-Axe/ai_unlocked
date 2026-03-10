"""add quote records and po fields

Revision ID: a1b2c3d4e5f6
Revises: 8bb2e528fc5a
Create Date: 2026-03-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '8bb2e528fc5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add PO system columns to reorder_recommendations ────────────────────
    op.add_column('reorder_recommendations',
        sa.Column('po_blob_path', sa.Text(), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('invoice_blob_path', sa.Text(), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('po_number', sa.String(50), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('invoice_number', sa.String(50), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('winning_vendor_id', sa.Integer(),
                  sa.ForeignKey('vendors.id'), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('order_value', sa.Float(), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('vendors_contacted', sa.Integer(), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('quotes_received', sa.Integer(), nullable=True))
    op.add_column('reorder_recommendations',
        sa.Column('ai_quote_reasoning', sa.Text(), nullable=True))

    # ── Create quote_records table ────────────────────────────────────────────
    op.create_table(
        'quote_records',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('recommendation_id', sa.Integer(),
                  sa.ForeignKey('reorder_recommendations.id'), nullable=False),
        sa.Column('vendor_id', sa.Integer(),
                  sa.ForeignKey('vendors.id'), nullable=False),
        sa.Column('tenant_id', sa.Integer(),
                  sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('quoted_price', sa.Float(), nullable=False),
        sa.Column('lead_time_days', sa.Integer(), nullable=False),
        sa.Column('proforma_blob_path', sa.Text(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('quote_records')
    op.drop_column('reorder_recommendations', 'ai_quote_reasoning')
    op.drop_column('reorder_recommendations', 'quotes_received')
    op.drop_column('reorder_recommendations', 'vendors_contacted')
    op.drop_column('reorder_recommendations', 'order_value')
    op.drop_column('reorder_recommendations', 'winning_vendor_id')
    op.drop_column('reorder_recommendations', 'invoice_number')
    op.drop_column('reorder_recommendations', 'po_number')
    op.drop_column('reorder_recommendations', 'invoice_blob_path')
    op.drop_column('reorder_recommendations', 'po_blob_path')
