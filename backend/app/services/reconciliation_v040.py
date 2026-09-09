import hashlib, json
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (Contribution, Payment, WebhookEvent, LedgerEntry, Loan, LoanInstallment,
                        Expense, CollectionAgreement, AgreementInstallment, FinancialReconciliation)

CENT=Decimal('0.01')
def money(v): return str(Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP))
def bounds(d): return d.replace(day=1), d.replace(day=monthrange(d.year,d.month)[1])
def dt_start(d): return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
def dt_end(d): return dt_start(d + timedelta(days=1))

def _sum_ledger(db, direction, ref_types=None, start=None, end=None):
    q=db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction==direction)
    if ref_types: q=q.filter(LedgerEntry.reference_type.in_(ref_types))
    if start: q=q.filter(LedgerEntry.created_at>=start)
    if end: q=q.filter(LedgerEntry.created_at<end)
    return Decimal(q.scalar() or 0).quantize(CENT)

def build_advanced_reconciliation(db: Session, competence: date):
    a,b=bounds(competence); start=dt_start(a); end=dt_end(b)
    findings=[]
    def check(code, expected, observed, details):
        e=Decimal(expected or 0).quantize(CENT); o=Decimal(observed or 0).quantize(CENT)
        ok=e==o; findings.append({'code':code,'status':'PASS' if ok else 'FAIL','details':details,'expected':money(e),'observed':money(o)})
    contrib=Decimal(db.query(func.coalesce(func.sum(Contribution.amount),0)).filter(Contribution.status=='PAID',Contribution.competence.between(a,b)).scalar() or 0)
    contrib_ledger=_sum_ledger(db,'CREDIT',['CONTRIBUTION_PAYMENT'],start,end)
    check('CONTRIBUTIONS',contrib,contrib_ledger,'Contribuições pagas devem bater com créditos no Ledger no período.')
    loan_pay=_sum_ledger(db,'CREDIT',['LOAN_INSTALLMENT_PAYMENT'],start,end)
    agr_pay=_sum_ledger(db,'CREDIT',['AGREEMENT_INSTALLMENT_PAYMENT'],start,end)
    disb=_sum_ledger(db,'DEBIT',['LOAN_DISBURSEMENT'],start,end)
    exp=Decimal(db.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.status=='POSTED',Expense.expense_date.between(a,b)).scalar() or 0)
    exp_ledger=_sum_ledger(db,'DEBIT',['EXPENSE'],start,end)
    check('EXPENSES',exp,exp_ledger,'Despesas lançadas devem bater com débitos no Ledger no período.')
    check('LOAN_PAYMENT_TOTAL',loan_pay+agr_pay,_sum_ledger(db,'CREDIT',['LOAN_INSTALLMENT_PAYMENT','AGREEMENT_INSTALLMENT_PAYMENT'],start,end),'Recebimentos de empréstimos/acordos devem estar no Ledger.')
    approved=Decimal(db.query(func.coalesce(func.sum(Payment.amount),0)).filter(Payment.status=='approved',Payment.created_at>=start,Payment.created_at<end).scalar() or 0)
    posted=Decimal(db.query(func.coalesce(func.sum(Payment.amount),0)).filter(Payment.status=='approved',Payment.ledger_posted_at.is_not(None),Payment.created_at>=start,Payment.created_at<end).scalar() or 0)
    check('APPROVED_PAYMENTS',approved,posted,'Pagamentos aprovados devem estar contabilizados.')
    unprocessed=db.query(WebhookEvent).filter(WebhookEvent.processed==False).count()  # noqa
    findings.append({'code':'UNPROCESSED_WEBHOOKS','status':'PASS' if unprocessed==0 else 'FAIL','details':'Webhooks pendentes bloqueiam fechamento.','expected':'0','observed':str(unprocessed)})
    negative=db.query(LoanInstallment).filter(LoanInstallment.status!='PAID',(LoanInstallment.amount+LoanInstallment.penalty_amount-LoanInstallment.paid_amount)<0).count()
    findings.append({'code':'NEGATIVE_INSTALLMENTS','status':'PASS' if negative==0 else 'FAIL','details':'Parcelas abertas não podem ter saldo negativo.','expected':'0','observed':str(negative)})
    # Operational exposure tie-out: open loan base/penalty and agreement balances must be non-negative.
    loan_out=Decimal('0')
    for i in db.query(LoanInstallment).filter(LoanInstallment.status!='PAID').all():
        loan_out += max(Decimal('0'),Decimal(i.amount)+Decimal(i.penalty_amount or 0)-Decimal(i.paid_amount or 0))
    agr_out=Decimal('0')
    for i in db.query(AgreementInstallment).filter(AgreementInstallment.status!='PAID').all():
        agr_out += max(Decimal('0'),Decimal(i.amount)+Decimal(i.penalty_amount or 0)-Decimal(i.paid_amount or 0))
    snapshot={
      'schema':'v0.40','competence':a.isoformat(),'period_end':b.isoformat(),
      'contributions_paid':money(contrib),'contributions_ledger':money(contrib_ledger),
      'loan_payments_ledger':money(loan_pay),'agreement_payments_ledger':money(agr_pay),
      'loan_disbursements_ledger':money(disb),'expenses_posted':money(exp),'expenses_ledger':money(exp_ledger),
      'approved_payments':money(approved),'posted_payments':money(posted),
      'open_loan_exposure':money(loan_out),'open_agreement_exposure':money(agr_out),
      'ledger_credits':money(_sum_ledger(db,'CREDIT',start=start,end=end)),
      'ledger_debits':money(_sum_ledger(db,'DEBIT',start=start,end=end)),
      'ledger_net':money(_sum_ledger(db,'CREDIT',start=start,end=end)-_sum_ledger(db,'DEBIT',start=start,end=end)),
      'findings':findings
    }
    raw=json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode(); h=hashlib.sha256(raw).hexdigest()
    return {'status':'PASS' if all(x['status']=='PASS' for x in findings) else 'FAIL','findings':findings,'snapshot':snapshot,'snapshot_hash':h}

def persist_reconciliation(db: Session, competence: date, run_by: int|None=None):
    result=build_advanced_reconciliation(db,competence)
    row=FinancialReconciliation(competence=competence.replace(day=1),status=result['status'],snapshot_json=json.dumps(result['snapshot'],sort_keys=True,separators=(',',':')),snapshot_hash=result['snapshot_hash'],run_by=run_by)
    db.add(row); db.flush(); return row,result
