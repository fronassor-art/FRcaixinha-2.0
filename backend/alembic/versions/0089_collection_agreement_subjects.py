"""add Agreement subjects to collection events and cases"""

from alembic import op
import sqlalchemy as sa


revision = "0089_collection_agreement_subjects"
down_revision = "0088_monthly_closing_snapshot_schema_h3c_b2"
branch_labels = None
depends_on = None


_EVENT_XOR = (
    "(installment_id IS NOT NULL AND agreement_installment_id IS NULL) "
    "OR (installment_id IS NULL AND agreement_installment_id IS NOT NULL)"
)
_CASE_XOR = (
    "(loan_id IS NOT NULL AND agreement_id IS NULL) "
    "OR (loan_id IS NULL AND agreement_id IS NOT NULL)"
)


def _create_partial_indexes():
    op.create_index(
        "uq_collection_event_loan_day",
        "collection_events",
        ["installment_id", "event_type", "event_date"],
        unique=True,
        sqlite_where=sa.text("installment_id IS NOT NULL"),
        postgresql_where=sa.text("installment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_collection_event_agreement_day",
        "collection_events",
        ["agreement_installment_id", "event_type", "event_date"],
        unique=True,
        sqlite_where=sa.text("agreement_installment_id IS NOT NULL"),
        postgresql_where=sa.text("agreement_installment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_collection_case_open_loan_subject",
        "collection_cases",
        ["member_id", "loan_id"],
        unique=True,
        sqlite_where=sa.text("status = 'OPEN' AND loan_id IS NOT NULL"),
        postgresql_where=sa.text("status = 'OPEN' AND loan_id IS NOT NULL"),
    )
    op.create_index(
        "uq_collection_case_open_agreement_subject",
        "collection_cases",
        ["member_id", "agreement_id"],
        unique=True,
        sqlite_where=sa.text("status = 'OPEN' AND agreement_id IS NOT NULL"),
        postgresql_where=sa.text("status = 'OPEN' AND agreement_id IS NOT NULL"),
    )


def _preflight(bind):
    null_event_subjects = bind.execute(
        sa.text("SELECT COUNT(*) FROM collection_events WHERE installment_id IS NULL")
    ).scalar_one()
    if int(null_event_subjects):
        raise RuntimeError(
            "Cannot upgrade 0089: legacy collection_events contain rows without installment_id"
        )
    null_loan_cases = bind.execute(
        sa.text("SELECT COUNT(*) FROM collection_cases WHERE loan_id IS NULL")
    ).scalar_one()
    if int(null_loan_cases):
        raise RuntimeError(
            "Cannot upgrade 0089: legacy collection_cases contain rows without loan_id; "
            "no Agreement subject can be inferred"
        )
    duplicate_open_loan_case = bind.execute(
        sa.text(
            "SELECT member_id, loan_id, COUNT(*) AS case_count "
            "FROM collection_cases "
            "WHERE status = 'OPEN' AND loan_id IS NOT NULL "
            "GROUP BY member_id, loan_id "
            "HAVING COUNT(*) > 1 "
            "LIMIT 1"
        )
    ).first()
    if duplicate_open_loan_case is not None:
        raise RuntimeError(
            "Cannot upgrade 0089: collection_cases contain duplicate OPEN loan "
            "subjects for the same member_id and loan_id. Resolve legacy "
            "duplicates explicitly before migration; no case will be selected, "
            "deleted, merged, or resolved automatically"
        )


def upgrade():
    bind = op.get_bind()
    _preflight(bind)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("collection_events", recreate="always") as batch_op:
            batch_op.drop_constraint("uq_collection_event_day", type_="unique")
            batch_op.alter_column("installment_id", existing_type=sa.Integer(), nullable=True)
            batch_op.add_column(sa.Column("agreement_installment_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_collection_events_agreement_installment_id",
                "agreement_installments",
                ["agreement_installment_id"],
                ["id"],
            )
            batch_op.create_check_constraint("ck_collection_events_exactly_one_subject", _EVENT_XOR)
        with op.batch_alter_table("collection_cases", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("agreement_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_collection_cases_agreement_id",
                "collection_agreements",
                ["agreement_id"],
                ["id"],
            )
            batch_op.create_check_constraint("ck_collection_cases_exactly_one_subject", _CASE_XOR)
    else:
        op.drop_constraint("uq_collection_event_day", "collection_events", type_="unique")
        op.alter_column("collection_events", "installment_id", existing_type=sa.Integer(), nullable=True)
        op.add_column("collection_events", sa.Column("agreement_installment_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_collection_events_agreement_installment_id",
            "collection_events",
            "agreement_installments",
            ["agreement_installment_id"],
            ["id"],
        )
        op.create_check_constraint("ck_collection_events_exactly_one_subject", "collection_events", _EVENT_XOR)
        op.add_column("collection_cases", sa.Column("agreement_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_collection_cases_agreement_id",
            "collection_cases",
            "collection_agreements",
            ["agreement_id"],
            ["id"],
        )
        op.create_check_constraint("ck_collection_cases_exactly_one_subject", "collection_cases", _CASE_XOR)
    _create_partial_indexes()


def downgrade():
    op.drop_index("uq_collection_case_open_agreement_subject", table_name="collection_cases")
    op.drop_index("uq_collection_case_open_loan_subject", table_name="collection_cases")
    op.drop_index("uq_collection_event_agreement_day", table_name="collection_events")
    op.drop_index("uq_collection_event_loan_day", table_name="collection_events")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("collection_cases", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_collection_cases_exactly_one_subject", type_="check")
            batch_op.drop_constraint("fk_collection_cases_agreement_id", type_="foreignkey")
            batch_op.drop_column("agreement_id")
        with op.batch_alter_table("collection_events", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_collection_events_exactly_one_subject", type_="check")
            batch_op.drop_constraint("fk_collection_events_agreement_installment_id", type_="foreignkey")
            batch_op.drop_column("agreement_installment_id")
            batch_op.alter_column("installment_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_unique_constraint("uq_collection_event_day", ["installment_id", "event_type", "event_date"])
    else:
        op.drop_constraint("ck_collection_cases_exactly_one_subject", "collection_cases", type_="check")
        op.drop_constraint("fk_collection_cases_agreement_id", "collection_cases", type_="foreignkey")
        op.drop_column("collection_cases", "agreement_id")
        op.drop_constraint("ck_collection_events_exactly_one_subject", "collection_events", type_="check")
        op.drop_constraint("fk_collection_events_agreement_installment_id", "collection_events", type_="foreignkey")
        op.drop_column("collection_events", "agreement_installment_id")
        op.alter_column("collection_events", "installment_id", existing_type=sa.Integer(), nullable=False)
        op.create_unique_constraint("uq_collection_event_day", "collection_events", ["installment_id", "event_type", "event_date"])
