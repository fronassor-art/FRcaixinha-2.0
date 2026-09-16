import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.base import Base
from app.models import Group, User, Member, Loan, LoanInstallment, Payment, PaymentSettlement, LedgerEntry, MemberFinancialEntry
from app.services.payment_settlement import settle_confirmed_pix_payment
from app.services.reconciliation_v040 import build_advanced_reconciliation

def newdb():
    e=create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool); Base.metadata.create_all(e); return sessionmaker(bind=e)()
def make(received='120.00', penalty='0.00'):
    db=newdb(); g=Group(name='G'); u=User(name='M',email='m@x',cpf='cpf-x',password_hash='x'); db.add_all([g,u]); db.flush(); m=Member(user_id=u.id,group_id=g.id); db.add(m); db.flush(); l=Loan(member_id=m.id,principal=Decimal('100'),monthly_rate=Decimal('.20'),installments=1,status='ACTIVE'); db.add(l); db.flush(); i=LoanInstallment(loan_id=l.id,number=1,due_date=date.today()+timedelta(days=10),principal=Decimal('100'),interest=Decimal('20'),amount=Decimal('120'),penalty_amount=Decimal(penalty),status='OPEN'); db.add(i); db.flush(); p=Payment(provider='test',provider_payment_id='p'+str(i.id),idempotency_key='i'+str(i.id),amount=Decimal(received),amount_received=Decimal(received),status='approved',raw_status='approved',reference_type='LOAN_INSTALLMENT',reference_id=str(i.id)); db.add(p); db.flush(); settle_confirmed_pix_payment(db,p,confirmation_source='TEST',confirmed_at=datetime.now(timezone.utc)); db.commit(); return db,i,p
def check(db): return next(x for x in build_advanced_reconciliation(db,date.today())['findings'] if x['code']=='PIX_INSTALLMENT_SETTLEMENT')
def test_integral_partial_and_excess():
    db,_,_=make(); assert check(db)['status']=='PASS'; db.close(); db,_,_=make('50'); assert check(db)['status']=='PASS'; db.close(); db,_,_=make('150'); assert check(db)['status']=='PASS'; db.close()

def test_v3_active_to_active_has_null_paid_at_evidence():
    db, installment, payment = make('50')
    settlement = db.query(PaymentSettlement).filter_by(payment_id=payment.id).one()
    snapshot = json.loads(settlement.receipt_snapshot_json)
    assert installment.status == 'PARTIAL'
    assert settlement.receipt_version == 'v3'
    assert settlement.loan_status_before == 'ACTIVE'
    assert settlement.loan_status_after == 'ACTIVE'
    assert settlement.loan_state_revision_after == settlement.loan_state_revision_before + 1
    assert settlement.loan_paid_at_before is None
    assert settlement.loan_paid_at_after is None
    assert snapshot['loan_state']['paid_at_before'] is None
    assert snapshot['loan_state']['paid_at_after'] is None
    assert check(db)['status'] == 'PASS'
    db.close()

@pytest.mark.parametrize(
    ('status_before', 'status_after', 'paid_before', 'paid_after'),
    [
        ('ACTIVE', 'PAID', None, None),
        ('ACTIVE', 'ACTIVE', None, datetime(2026, 9, 16, tzinfo=timezone.utc)),
        ('ACTIVE', 'ACTIVE', datetime(2026, 9, 16, tzinfo=timezone.utc), None),
        ('PAID', 'ACTIVE', datetime(2026, 9, 16, tzinfo=timezone.utc), None),
    ],
)
def test_v3_paid_at_semantic_mismatches_are_detected_with_valid_hash(
    status_before, status_after, paid_before, paid_after,
):
    db, _, payment = make('50')
    settlement = db.query(PaymentSettlement).filter_by(payment_id=payment.id).one()
    settlement.loan_status_before = status_before
    settlement.loan_status_after = status_after
    settlement.loan_paid_at_before = paid_before
    settlement.loan_paid_at_after = paid_after
    snapshot = json.loads(settlement.receipt_snapshot_json)
    snapshot['loan_state'].update({
        'status_before': status_before,
        'status_after': status_after,
        'paid_at_before': paid_before.isoformat() if paid_before else None,
        'paid_at_after': paid_after.isoformat() if paid_after else None,
    })
    canonical = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    settlement.receipt_snapshot_json = canonical
    settlement.receipt_hash = hashlib.sha256(canonical.encode()).hexdigest()
    db.commit()
    assert check(db)['status'] == 'FAIL'
    db.close()
def test_two_pix_same_installment_and_principal():
    db,i,_=make('50'); p=Payment(provider='test',provider_payment_id='p2',idempotency_key='i2',amount=Decimal('70'),status='approved',reference_type='LOAN_INSTALLMENT',reference_id=str(i.id)); db.add(p); db.flush(); settle_confirmed_pix_payment(db,p,confirmation_source='TEST'); db.commit(); assert check(db)['status']=='PASS'; db.close()
def test_penalty_only_has_no_principal():
    db,i,p=make('10','10'); assert check(db)['status']=='PASS'; db.close()
@pytest.mark.parametrize('kind',['interest_missing','penalty_missing','principal_missing','duplicate','wrong_value','wrong_reference','zero_component'])
def test_component_evidence_failures(kind):
    db,i,p=make('10','10') if kind=='penalty_missing' else make()
    if kind=='interest_missing': db.query(LedgerEntry).filter_by(reference_type='LOAN_INTEREST_PAYMENT',reference_id=str(p.id)).delete()
    elif kind=='penalty_missing': db.query(LedgerEntry).filter_by(reference_type='LOAN_PENALTY_PAYMENT',reference_id=str(p.id)).delete()
    elif kind=='principal_missing': db.query(MemberFinancialEntry).filter_by(reference_id=str(p.id)).delete()
    elif kind=='duplicate':
        r=db.query(LedgerEntry).filter_by(reference_type='LOAN_INTEREST_PAYMENT',reference_id=str(p.id)).one(); db.add(LedgerEntry(account='CAIXINHA',direction='CREDIT',amount=r.amount,reference_type=r.reference_type,reference_id=r.reference_id))
    elif kind=='wrong_value': db.execute(text("update ledger_entries set amount=:v where reference_type='LOAN_INTEREST_PAYMENT' and reference_id=:r"), {'v':'19.99','r':str(p.id)})
    elif kind=='wrong_reference': db.execute(text("update ledger_entries set reference_id='999' where reference_type='LOAN_INTEREST_PAYMENT' and reference_id=:r"), {'r':str(p.id)})
    else: db.execute(text("update ledger_entries set amount='0.00' where reference_type='LOAN_INTEREST_PAYMENT' and reference_id=:r"), {'r':str(p.id)})
    db.commit(); assert check(db)['status']=='FAIL'; db.close()
def test_received_accumulated_and_legacy_do_not_mask():
    db,i,p=make(); p.amount_received=Decimal('119'); db.commit(); assert check(db)['status']=='FAIL'; db.close(); db,i,p=make(); i.paid_amount=Decimal('119'); db.commit(); assert check(db)['status']=='FAIL'; db.close(); db,i,p=make(); db.add(LedgerEntry(account='CAIXINHA',direction='CREDIT',amount=Decimal('20'),reference_type='LOAN_INSTALLMENT_PAYMENT',reference_id=str(p.id))); db.query(LedgerEntry).filter_by(reference_type='LOAN_INTEREST_PAYMENT',reference_id=str(p.id)).delete(); db.commit(); assert check(db)['status']=='FAIL'; db.close()


def test_v2_receipt_state_and_hash_mismatches_are_detected():
    db, _, payment = make()
    settlement = db.query(PaymentSettlement).filter_by(payment_id=payment.id).one()
    snapshot = json.loads(settlement.receipt_snapshot_json)
    snapshot["loan_state"]["state_revision_after"] = 99
    settlement.receipt_snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    db.commit()
    assert check(db)['status'] == 'FAIL'
    db.close()

    db, _, payment = make()
    settlement = db.query(PaymentSettlement).filter_by(payment_id=payment.id).one()
    settlement.loan_state_revision_after = settlement.loan_state_revision_before + 2
    db.commit()
    assert check(db)['status'] == 'FAIL'
    db.close()

    db, _, payment = make()
    settlement = db.query(PaymentSettlement).filter_by(payment_id=payment.id).one()
    settlement.receipt_hash = hashlib.sha256(b"wrong").hexdigest()
    db.commit()
    assert check(db)['status'] == 'FAIL'
    db.close()
