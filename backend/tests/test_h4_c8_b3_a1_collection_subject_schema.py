from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from app.db.base import Base


def schema_inspector():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return inspect(engine)


def test_collection_event_supports_loan_and_agreement_foreign_keys():
    inspector = schema_inspector()
    columns = {column["name"] for column in inspector.get_columns("collection_events")}
    foreign_keys = {
        (foreign_key["constrained_columns"][0], foreign_key["referred_table"])
        for foreign_key in inspector.get_foreign_keys("collection_events")
    }

    assert "installment_id" in columns
    assert "agreement_installment_id" in columns
    assert ("installment_id", "loan_installments") in foreign_keys
    assert ("agreement_installment_id", "agreement_installments") in foreign_keys


def test_collection_event_enforces_exactly_one_subject():
    inspector = schema_inspector()
    checks = {check["name"] for check in inspector.get_check_constraints("collection_events")}

    assert "ck_collection_events_exactly_one_subject" in checks


def test_collection_event_has_independent_subject_idempotency_constraints():
    inspector = schema_inspector()
    indexes = {index["name"] for index in inspector.get_indexes("collection_events")}

    assert "uq_collection_event_loan_day" in indexes
    assert "uq_collection_event_agreement_day" in indexes


def test_collection_case_supports_loan_and_agreement_foreign_keys():
    inspector = schema_inspector()
    columns = {column["name"] for column in inspector.get_columns("collection_cases")}
    foreign_keys = {
        (foreign_key["constrained_columns"][0], foreign_key["referred_table"])
        for foreign_key in inspector.get_foreign_keys("collection_cases")
    }

    assert "loan_id" in columns
    assert "agreement_id" in columns
    assert ("loan_id", "loans") in foreign_keys
    assert ("agreement_id", "collection_agreements") in foreign_keys


def test_collection_case_enforces_exactly_one_subject():
    inspector = schema_inspector()
    checks = {check["name"] for check in inspector.get_check_constraints("collection_cases")}

    assert "ck_collection_cases_exactly_one_subject" in checks


def test_collection_case_allows_one_open_case_per_loan_or_agreement():
    inspector = schema_inspector()
    indexes = {index["name"] for index in inspector.get_indexes("collection_cases")}

    assert "uq_collection_case_open_loan_subject" in indexes
    assert "uq_collection_case_open_agreement_subject" in indexes
