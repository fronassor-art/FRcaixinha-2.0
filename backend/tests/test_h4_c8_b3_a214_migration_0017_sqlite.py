import os
import subprocess
import sys

from sqlalchemy import create_engine, text


def _alembic(database, revision):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_migration_0017_sqlite_preserves_historical_agreement_schema(tmp_path):
    database = tmp_path / "migration_0017.db"
    _alembic(database, "0017_agreements_v039")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0017_agreements_v039"
        )

        agreement_columns = {
            row[1]: row for row in connection.execute(text("PRAGMA table_info(collection_agreements)"))
        }
        installment_columns = {
            row[1]: row for row in connection.execute(text("PRAGMA table_info(agreement_installments)"))
        }

        assert set(agreement_columns) == {
            "id",
            "loan_id",
            "member_id",
            "requested_by",
            "decided_by",
            "status",
            "installments",
            "total_amount",
            "reason",
            "snapshot",
            "requested_at",
            "decided_at",
        }
        assert set(installment_columns) == {
            "id",
            "agreement_id",
            "number",
            "due_date",
            "principal",
            "penalty_amount",
            "amount",
            "paid_amount",
            "paid_penalty_amount",
            "paid_at",
            "status",
        }

        assert agreement_columns["status"][2] == "VARCHAR(20)"
        assert agreement_columns["status"][3] == 1
        assert agreement_columns["status"][4] is None
        for name in ("penalty_amount", "paid_amount", "paid_penalty_amount"):
            row = installment_columns[name]
            assert row[2] == "NUMERIC(14, 2)"
            assert row[3] == 1
            assert row[4] is None
        assert installment_columns["status"][2] == "VARCHAR(20)"
        assert installment_columns["status"][3] == 1
        assert installment_columns["status"][4] is None

        agreement_fks = {
            (row[2], row[3]) for row in connection.execute(text("PRAGMA foreign_key_list(collection_agreements)"))
        }
        installment_fks = {
            (row[2], row[3]) for row in connection.execute(text("PRAGMA foreign_key_list(agreement_installments)"))
        }
        assert {("loans", "loan_id"), ("members", "member_id"), ("users", "requested_by"), ("users", "decided_by")} <= agreement_fks
        assert {("collection_agreements", "agreement_id")} <= installment_fks

        agreement_indexes = {
            row[1] for row in connection.execute(text("PRAGMA index_list(collection_agreements)"))
        }
        installment_indexes = {
            row[1] for row in connection.execute(text("PRAGMA index_list(agreement_installments)"))
        }
        agreement_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'collection_agreements'")
        ).scalar_one()
        installment_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agreement_installments'")
        ).scalar_one()
        assert "uq_agreement_loan_status" in agreement_sql
        assert "ix_agreements_member_status" in agreement_indexes
        assert "ix_collection_agreements_loan_id" in agreement_indexes
        assert "uq_agreement_installment_number" in installment_sql
        assert "ix_agreement_installments_agreement_id" in installment_indexes

        connection.execute(
            text(
                "INSERT INTO collection_agreements "
                "(loan_id, member_id, requested_by, status, installments, total_amount, snapshot, requested_at) "
                "VALUES (1, 1, 1, 'REQUESTED', 1, 100.00, '{}', '2099-01-01 00:00:00')"
            )
        )
        connection.commit()
        connection.execute(
            text(
                "INSERT INTO agreement_installments "
                "(agreement_id, number, due_date, principal, penalty_amount, amount, "
                "paid_amount, paid_penalty_amount, status) "
                "VALUES (1, 1, '2099-02-01', 100.00, 0.00, 100.00, 0.00, 0.00, 'OPEN')"
            )
        )
        connection.commit()
        stored = connection.execute(
            text(
                "SELECT penalty_amount, paid_amount, paid_penalty_amount, status "
                "FROM agreement_installments WHERE id = 1"
            )
        ).one()
        assert tuple(stored) == (0, 0, 0, "OPEN")
