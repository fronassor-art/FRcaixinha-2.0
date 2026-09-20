import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, event, inspect, text

from app.models import MemberFinancialEntry


REVISION = "0090_member_financial_contribution_identity"
DOWN_REVISION = "0089_collection_agreement_subjects"
INDEX_NAME = "uq_member_financial_entries_one_contribution_settlement_credit"


def _upgrade(database, revision):
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


def _downgrade(database, revision):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "testsecret",
            "APP_ENV": "test",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", revision],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
def _engine(database):
    engine = create_engine(f"sqlite:///{database}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def test_member_financial_entry_metadata_declares_identity_columns_and_index():
    table = MemberFinancialEntry.__table__

    assert table.c.contribution_id.nullable is True
    assert table.c.payment_settlement_id.nullable is True
    assert table.c.contribution_id.foreign_keys
    assert table.c.payment_settlement_id.foreign_keys

    index = next(index for index in table.indexes if index.name == INDEX_NAME)
    assert index.unique is True
    assert str(index.dialect_options["sqlite"]["where"]) == (
        "payment_settlement_id IS NOT NULL AND "
        "entry_type = 'CONTRIBUTION' AND direction = 'CREDIT'"
    )


def test_migration_preserves_legacy_rows_and_enforces_settlement_identity(tmp_path):
    database = tmp_path / "mfe_identity.db"
    _upgrade(database, DOWN_REVISION)
    engine = _engine(database)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, cpf, password_hash, role, is_active, created_at) "
                "VALUES (1, 'Legacy', 'legacy@example.invalid', '00000000000', "
                "'hash', 'USER', 1, '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO groups "
                "(id, name, monthly_amount, months, due_day, active, min_cash_reserve, "
                "max_simultaneous_loans, max_installments, grace_days, "
                "max_overdue_installments) "
                "VALUES (1, 'Legacy', 100, 12, 10, 1, 0, 3, 6, 0, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO members (id, user_id, group_id, status, joined_at) "
                "VALUES (1, 1, 1, 'ACTIVE', '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO member_financial_accounts "
                "(id, member_id, created_at, updated_at) "
                "VALUES (1, 1, '2099-01-01 00:00:00', '2099-01-01 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO member_financial_entries "
                "(id, account_id, entry_type, direction, amount, created_at) "
                "VALUES (1, 1, 'ADJUSTMENT', 'CREDIT', 10, '2099-01-01 00:00:00')"
            )
        )

    _upgrade(database, REVISION)

    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == REVISION
        columns = {
            row[1]: row for row in connection.execute(
                text("PRAGMA table_info(member_financial_entries)")
            )
        }
        assert columns["contribution_id"][3] == 0
        assert columns["payment_settlement_id"][3] == 0
        assert connection.execute(
            text("SELECT entry_type FROM member_financial_entries WHERE id = 1")
        ).scalar_one() == "ADJUSTMENT"

        index_names = {
            row[1] for row in connection.execute(
                text("PRAGMA index_list(member_financial_entries)")
            )
        }
        assert INDEX_NAME in index_names

        fks = {
            row[3]: row[2]
            for row in connection.execute(
                text("PRAGMA foreign_key_list(member_financial_entries)")
            )
        }
        assert fks["contribution_id"] == "contributions"
        assert fks["payment_settlement_id"] == "payment_settlements"
        fk_rows = list(connection.execute(text(
            "PRAGMA foreign_key_list(member_financial_entries)"
        )))
        fk_delete_actions = {row[3]: row[6] for row in fk_rows}
        assert fk_delete_actions["contribution_id"] == "RESTRICT"
        assert fk_delete_actions["payment_settlement_id"] == "RESTRICT"


def test_migration_fresh_chain_and_downgrade(tmp_path):
    database = tmp_path / "mfe_identity_fresh.db"
    _upgrade(database, REVISION)
    engine = _engine(database)

    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == REVISION
        assert {row[3] for row in connection.execute(
            text("PRAGMA foreign_key_list(member_financial_entries)")
        )} >= {"contribution_id", "payment_settlement_id"}

    _downgrade(database, DOWN_REVISION)
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == DOWN_REVISION
        assert "contribution_id" not in {
            row[1] for row in connection.execute(
                text("PRAGMA table_info(member_financial_entries)")
            )
        }


def test_partial_unique_identity_allows_null_and_rejects_duplicate_credit():
    pytest.importorskip("sqlite3")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE member_financial_entries ("
            "id INTEGER PRIMARY KEY, payment_settlement_id INTEGER, "
            "entry_type VARCHAR(50) NOT NULL, direction VARCHAR(20) NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX test_unique_credit ON "
            "member_financial_entries(payment_settlement_id) WHERE "
            "payment_settlement_id IS NOT NULL AND entry_type = 'CONTRIBUTION' "
            "AND direction = 'CREDIT'"
        )
        connection.execute(
            text(
                "INSERT INTO member_financial_entries "
                "(id, payment_settlement_id, entry_type, direction) "
                "VALUES (1, NULL, 'ADJUSTMENT', 'CREDIT')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO member_financial_entries "
                "(id, payment_settlement_id, entry_type, direction) "
                "VALUES (2, NULL, 'ADJUSTMENT', 'CREDIT')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO member_financial_entries "
                "(id, payment_settlement_id, entry_type, direction) "
                "VALUES (3, 10, 'CONTRIBUTION', 'CREDIT')"
            )
        )
        with pytest.raises(Exception):
            connection.execute(
                text(
                    "INSERT INTO member_financial_entries "
                    "(id, payment_settlement_id, entry_type, direction) "
                    "VALUES (4, 10, 'CONTRIBUTION', 'CREDIT')"
                )
            )
