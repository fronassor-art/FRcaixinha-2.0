from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    AgreementInstallment, CollectionAgreement, Contribution, Group, LedgerEntry,
    Loan, LoanInstallment, Member, Payment, User,
)
from app.services.agreement_payments_v039 import apply_confirmed_agreement_payment
from app.services.payment_settlement import settle_confirmed_pix_payment
from app.services.reconciliation_v040 import build_advanced_reconciliation


def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def member(db, suffix="x"):
    group = Group(name=f"G-{suffix}")
    user = User(name=f"M-{suffix}", email=f"m-{suffix}@test", cpf=f"cpf-{suffix}", password_hash="x")
    db.add_all([group, user]); db.flush()
    row = Member(user_id=user.id, group_id=group.id); db.add(row); db.flush()
    return row


def contribution_payment(db, *, amount="100.00", received="100.00", suffix="c"):
    m = member(db, suffix)
    c = Contribution(member_id=m.id, competence=date.today().replace(day=1), amount=Decimal("100.00"), paid_amount=Decimal("0.00"), status="PENDING")
    db.add(c); db.flush()
    p = Payment(provider="mercado_pago", provider_payment_id=f"p-{suffix}", idempotency_key=f"i-{suffix}", amount=Decimal(amount), amount_received=Decimal(received), status="approved", raw_status="approved", reference_type="CONTRIBUTION", reference_id=str(c.id))
    db.add(p); db.flush()
    settle_confirmed_pix_payment(db, p, confirmation_source="TEST")
    db.commit()
    return p


def loan_payment(db, *, valid=True):
    m = member(db, "loan")
    loan = Loan(member_id=m.id, principal=Decimal("100.00"), monthly_rate=Decimal("0.20"), installments=1, status="ACTIVE")
    db.add(loan); db.flush()
    i = LoanInstallment(loan_id=loan.id, number=1, due_date=date.today()+timedelta(days=10), principal=Decimal("100.00"), interest=Decimal("20.00"), amount=Decimal("120.00"), penalty_amount=Decimal("0.00"), status="OPEN")
    db.add(i); db.flush()
    p = Payment(provider="mercado_pago", provider_payment_id="p-loan", idempotency_key="i-loan", amount=Decimal("120.00"), amount_received=Decimal("120.00"), status="approved", raw_status="approved", reference_type="LOAN_INSTALLMENT", reference_id=str(i.id))
    db.add(p); db.flush()
    settle_confirmed_pix_payment(db, p, confirmation_source="TEST")
    if not valid:
        db.query(LedgerEntry).filter_by(reference_type="LOAN_INTEREST_PAYMENT", reference_id=str(p.id)).delete()
    db.commit()
    return p


def agreement_payment(db, *, amount="100.00", received="100.00", principal=None, suffix="a"):
    m = member(db, suffix)
    loan = Loan(member_id=m.id, principal=Decimal("100.00"), monthly_rate=Decimal("0.20"), installments=1, status="RESTRUCTURED")
    db.add(loan); db.flush()
    ag = CollectionAgreement(loan_id=loan.id, member_id=m.id, requested_by=m.user_id, status="APPROVED", installments=1, total_amount=Decimal("100.00"), snapshot="{}")
    db.add(ag); db.flush()
    principal = Decimal(amount) if principal is None else Decimal(principal)
    i = AgreementInstallment(agreement_id=ag.id, number=1, due_date=date.today()+timedelta(days=10), principal=principal, amount=principal, status="OPEN")
    db.add(i); db.flush()
    p = Payment(provider="mercado_pago", provider_payment_id=f"p-{suffix}", idempotency_key=f"i-{suffix}", amount=Decimal(amount), amount_received=Decimal(received), status="approved", raw_status="approved", reference_type="AGREEMENT_INSTALLMENT", reference_id=str(i.id))
    db.add(p); db.flush()
    apply_confirmed_agreement_payment(db, p, i)
    db.commit()
    return p


def finding(db):
    return next(x for x in build_advanced_reconciliation(db, date.today())['findings'] if x['code'] == 'APPROVED_PAYMENTS')


def test_contribution_valid_and_timestamp_is_not_evidence():
    s = db(); p = contribution_payment(s); assert finding(s)['status'] == 'PASS'; p.ledger_posted_at = None; s.commit(); assert finding(s)['status'] == 'PASS'; s.close()


def test_contribution_missing_or_duplicate_or_wrong_value_ledger_fails():
    s = db(); p = contribution_payment(s, suffix='missing'); s.query(LedgerEntry).filter_by(reference_id=str(p.id)).delete(); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()
    s = db(); p = contribution_payment(s, suffix='duplicate'); row = s.query(LedgerEntry).filter_by(reference_id=str(p.id)).one(); s.add(LedgerEntry(account='CAIXINHA', direction='CREDIT', amount=row.amount, reference_type='CONTRIBUTION_PAYMENT', reference_id=str(p.id))); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()
    s = db(); p = contribution_payment(s, suffix='wrong-value'); row = s.query(LedgerEntry).filter_by(reference_id=str(p.id)).one(); s.execute(text("update ledger_entries set amount='99.00' where id=:id"), {'id': row.id}); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()


def test_contribution_excess_and_inconsistent_settlement():
    s = db(); p = contribution_payment(s, amount='150.00', received='150.00', suffix='excess'); assert finding(s)['status'] == 'PASS'; s.close()
    s = db(); p = contribution_payment(s, suffix='settlement'); p.amount_received = Decimal('99.00'); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()


def test_loan_reuses_pix_installment_validation():
    s = db(); loan_payment(s); assert finding(s)['status'] == 'PASS'; s.close()
    s = db(); loan_payment(s, valid=False); assert finding(s)['status'] == 'FAIL'; s.close()


def test_agreement_valid_and_missing_or_duplicate_ledger_fails():
    s = db(); p = agreement_payment(s); assert finding(s)['status'] == 'PASS'; s.close()
    s = db(); p = agreement_payment(s, suffix='agreement-missing'); s.query(LedgerEntry).filter_by(reference_id=str(p.id)).delete(); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()
    s = db(); p = agreement_payment(s, suffix='agreement-duplicate'); row = s.query(LedgerEntry).filter_by(reference_id=str(p.id)).one(); s.add(LedgerEntry(account='CAIXINHA', direction='CREDIT', amount=row.amount, reference_type='AGREEMENT_INSTALLMENT_PAYMENT', reference_id=str(p.id))); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()


def test_agreement_expected_applied_covers_insufficient_partial_excess_and_overstated_ledger():
    # A: cobrança e recebimento de 100 devem ter ledger de 100, não 40.
    s = db(); p = agreement_payment(s, amount='100.00', received='100.00', suffix='agreement-insufficient'); row = s.query(LedgerEntry).filter_by(reference_id=str(p.id)).one(); s.execute(text("update ledger_entries set amount='40.00' where id=:id"), {'id': row.id}); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()
    # B: cobrança de 40, recebimento de 100 e saldo de 40 representam excesso legítimo.
    s = db(); p = agreement_payment(s, amount='40.00', received='100.00', principal='40.00', suffix='agreement-excess'); assert s.query(LedgerEntry).filter_by(reference_id=str(p.id)).one().amount == Decimal('40.00'); assert finding(s)['status'] == 'PASS'; s.close()
    # C: cobrança e recebimento parciais de 40 devem coincidir no ledger.
    s = db(); p = agreement_payment(s, amount='100.00', received='40.00', suffix='agreement-partial'); assert s.query(LedgerEntry).filter_by(reference_id=str(p.id)).one().amount == Decimal('40.00'); assert finding(s)['status'] == 'PASS'; s.close()
    # D: ledger acima do recebimento diverge do valor aplicado esperado.
    s = db(); p = agreement_payment(s, amount='100.00', received='40.00', suffix='agreement-overstated'); row = s.query(LedgerEntry).filter_by(reference_id=str(p.id)).one(); s.execute(text("update ledger_entries set amount='41.00' where id=:id"), {'id': row.id}); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()


def test_unknown_and_null_reference_types_fail_without_aggregate_compensation():
    for ref, suffix in ((None, 'null'), ('UNKNOWN', 'unknown')):
        s = db(); m = member(s, suffix); p = Payment(provider='test', provider_payment_id=f'p-{suffix}', idempotency_key=f'i-{suffix}', amount=Decimal('10.00'), amount_received=Decimal('10.00'), status='approved', reference_type=ref); s.add(p); s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()
    s = db(); p1 = contribution_payment(s, suffix='comp-one'); s.query(LedgerEntry).filter_by(reference_id=str(p1.id)).delete(); p2 = contribution_payment(s, suffix='comp-two'); p2.ledger_posted_at = None; s.commit(); assert finding(s)['status'] == 'FAIL'; s.close()
