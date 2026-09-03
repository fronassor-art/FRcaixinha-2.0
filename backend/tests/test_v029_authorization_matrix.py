from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_admin_endpoints_require_server_side_admin_dependency():
    admin = (ROOT / 'backend/app/api/admin.py').read_text()
    finance = (ROOT / 'backend/app/api/admin_finance.py').read_text()
    reports = (ROOT / 'backend/app/api/admin_reports.py').read_text()
    for text in (admin, finance, reports):
        assert 'require_admin' in text


def test_sensitive_payment_routes_do_not_accept_client_side_status_as_authority():
    payments = (ROOT / 'backend/app/api/payments.py').read_text()
    assert 'status = remote.get("status")' in payments
    assert "status == \"approved\"" in payments or "status == 'approved'" in payments
    assert 'data.get("status")' not in payments


def test_loan_decision_and_release_are_separate_server_side_transitions():
    loans = (ROOT / 'backend/app/api/loans.py').read_text()
    assert 'require_admin' in loans
    assert 'LOAN_DECISION' in loans
    assert "status != 'APPROVED'" in (ROOT / 'backend/app/services/loan_engine_v17.py').read_text()
