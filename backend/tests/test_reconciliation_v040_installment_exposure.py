from datetime import date
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.base import Base
from app.models import Group, User, Member, Loan, LoanInstallment , CollectionAgreement, AgreementInstallment
from app.services.reconciliation_v040 import build_advanced_reconciliation

def db():
    e=create_engine('sqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool); Base.metadata.create_all(e); return sessionmaker(bind=e)()
def installment(s, amount='120.00', penalty='10.00', paid='0.00', paid_penalty='0.00', status='OPEN'):
    g=Group(name='G'); u=User(name='M',email='m@x',cpf='cpf-'+str(s),password_hash='x'); s.add_all([g,u]); s.flush(); m=Member(user_id=u.id,group_id=g.id); s.add(m); s.flush(); l=Loan(member_id=m.id,principal=Decimal('100'),monthly_rate=Decimal('.20'),installments=1,status='ACTIVE'); s.add(l); s.flush(); i=LoanInstallment(loan_id=l.id,number=1,due_date=date(2026,1,10),principal=Decimal('100'),interest=Decimal('20'),amount=Decimal(amount),penalty_amount=Decimal(penalty),paid_amount=Decimal(paid),paid_penalty_amount=Decimal(paid_penalty),status=status); s.add(i); s.flush(); return i
def test_exposure_semantics():
    for kwargs, expected in [({},'130.00'), ({'paid':'40.00','paid_penalty':'10.00'},'80.00'), ({'paid_penalty':'4.00'},'126.00'), ({'paid':'120.00'},'10.00'), ({'paid':'40.00'},'90.00'), ({'paid':'120.00','paid_penalty':'10.00'},'0.00')]:
        s=db(); i=installment(s,**kwargs); s.commit(); assert build_advanced_reconciliation(s,date(2026,1,1))['snapshot']['open_loan_exposure']==expected; s.close()
def test_cent_decimal_and_agreement_formula_uses_components():
    s=db(); i=installment(s, amount='120.005', penalty='10.005', paid='40.005', paid_penalty='4.005'); loan=s.get(Loan, i.loan_id); agreement=CollectionAgreement(loan_id=loan.id, member_id=loan.member_id, requested_by=loan.member_id, installments=1, total_amount=Decimal('110.00'), snapshot='{}', status='ACTIVE'); s.add(agreement); s.flush(); s.add(AgreementInstallment(agreement_id=agreement.id, number=1, due_date=date(2026,1,10), principal=Decimal('100'), amount=Decimal('100'), penalty_amount=Decimal('10'), paid_amount=Decimal('40'), paid_penalty_amount=Decimal('4'), status='OPEN')); s.commit(); snap=build_advanced_reconciliation(s,date(2026,1,1))['snapshot']; assert snap['open_loan_exposure']=='86.00'; assert snap['open_agreement_exposure']=='66.00'; s.close()

def negative_status(s):
    result = build_advanced_reconciliation(s, date(2026, 1, 1))
    return next(f for f in result['findings'] if f['code'] == 'NEGATIVE_INSTALLMENTS')

def test_negative_installments_detects_invalid_and_overpaid_values():
    cases = [
        {'paid': '120.01'},
        {'paid_penalty': '10.01'},
        {'amount': '-0.01'},
        {'paid': '-0.01'},
        {'penalty': '-0.01'},
        {'paid_penalty': '-0.01'},
    ]
    for kwargs in cases:
        s = db(); installment(s, **kwargs); s.commit()
        assert negative_status(s)['status'] == 'FAIL'
        s.close()

    s = db(); installment(s, paid='40.00', paid_penalty='4.00'); s.commit()
    assert negative_status(s)['status'] == 'PASS'
    s.close()

def test_negative_installments_preserves_paid_behavior():
    s = db(); installment(s, paid='120.01', paid_penalty='10.01', status='PAID'); s.commit()
    finding = negative_status(s)
    assert finding['status'] == 'PASS'
    assert finding['observed'] == '0'
    s.close()
