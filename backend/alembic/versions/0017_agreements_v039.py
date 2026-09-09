"""financial collection agreements v0.39"""
from alembic import op
import sqlalchemy as sa
revision="0017_agreements_v039"
down_revision="0016_collection_dunning_v038"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("collection_agreements",
        sa.Column("id",sa.Integer(),primary_key=True), sa.Column("loan_id",sa.Integer(),sa.ForeignKey("loans.id"),nullable=False),
        sa.Column("member_id",sa.Integer(),sa.ForeignKey("members.id"),nullable=False), sa.Column("requested_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=False),
        sa.Column("decided_by",sa.Integer(),sa.ForeignKey("users.id"),nullable=True), sa.Column("status",sa.String(20),nullable=False,server_default="REQUESTED"),
        sa.Column("installments",sa.Integer(),nullable=False), sa.Column("total_amount",sa.Numeric(14,2),nullable=False), sa.Column("reason",sa.Text()),
        sa.Column("snapshot",sa.Text(),nullable=False), sa.Column("requested_at",sa.DateTime(timezone=True),nullable=False), sa.Column("decided_at",sa.DateTime(timezone=True)),
        sa.UniqueConstraint("loan_id","status",name="uq_agreement_loan_status"))
    op.create_index("ix_agreements_member_status","collection_agreements",["member_id","status"]); op.create_index("ix_collection_agreements_loan_id","collection_agreements",["loan_id"])
    op.create_table("agreement_installments",
        sa.Column("id",sa.Integer(),primary_key=True), sa.Column("agreement_id",sa.Integer(),sa.ForeignKey("collection_agreements.id"),nullable=False),
        sa.Column("number",sa.Integer(),nullable=False), sa.Column("due_date",sa.Date(),nullable=False), sa.Column("principal",sa.Numeric(14,2),nullable=False),
        sa.Column("penalty_amount",sa.Numeric(14,2),nullable=False,server_default="0"), sa.Column("amount",sa.Numeric(14,2),nullable=False),
        sa.Column("paid_amount",sa.Numeric(14,2),nullable=False,server_default="0"), sa.Column("paid_penalty_amount",sa.Numeric(14,2),nullable=False,server_default="0"),
        sa.Column("paid_at",sa.DateTime(timezone=True)), sa.Column("status",sa.String(20),nullable=False,server_default="OPEN"),
        sa.UniqueConstraint("agreement_id","number",name="uq_agreement_installment_number"))
    op.create_index("ix_agreement_installments_agreement_id","agreement_installments",["agreement_id"])
    op.alter_column("collection_agreements","status",server_default=None); op.alter_column("agreement_installments","penalty_amount",server_default=None); op.alter_column("agreement_installments","paid_amount",server_default=None); op.alter_column("agreement_installments","paid_penalty_amount",server_default=None); op.alter_column("agreement_installments","status",server_default=None)
def downgrade():
    op.drop_table("agreement_installments"); op.drop_index("ix_agreements_member_status",table_name="collection_agreements"); op.drop_index("ix_collection_agreements_loan_id",table_name="collection_agreements"); op.drop_table("collection_agreements")
