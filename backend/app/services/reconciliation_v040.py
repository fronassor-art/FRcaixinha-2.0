import hashlib, json
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (Contribution, Payment, PaymentSettlement, WebhookEvent, LedgerEntry, Loan, LoanInstallment,
                        Expense, CollectionAgreement, AgreementInstallment, FinancialReconciliation,
                        MemberFinancialAccount, MemberFinancialEntry)

CENT=Decimal('0.01')
ZERO=Decimal('0.00')
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

def _pix_installment_settlement_findings(db):
    """Cross-check current PIX installment settlements against their evidence."""
    issues = []
    settlements = db.query(PaymentSettlement).filter(PaymentSettlement.obligation_type == "LOAN_INSTALLMENT").all()
    by_installment = {}
    def dec(value): return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)
    def issue(key, detail): issues.append(f"{key}: {detail}")
    for settlement in settlements:
        pid = settlement.payment_id
        payment = db.get(Payment, pid)
        installment = db.get(LoanInstallment, settlement.loan_installment_id)
        loan = db.get(Loan, installment.loan_id) if installment else None
        received, applied = dec(settlement.amount_received), dec(settlement.amount_applied)
        principal, interest = dec(settlement.principal_applied), dec(settlement.interest_applied)
        penalty, excess = dec(settlement.penalty_applied), dec(settlement.excess_amount)
        by_installment.setdefault(settlement.loan_installment_id, []).append(settlement)
        if payment is None:
            issue(pid, "Payment inexistente")
        else:
            if payment.status != "approved": issue(pid, f"Payment.status={payment.status!r}")
            if (payment.reference_type or "").upper() != "LOAN_INSTALLMENT": issue(pid, "Payment.reference_type incompatível")
            if payment.reference_id != str(settlement.loan_installment_id): issue(pid, "Payment.reference_id incompatível")
            expected_received = dec(payment.amount_received if payment.amount_received is not None else payment.amount)
            if expected_received != received: issue(pid, "Payment.amount_received/amount diverge do settlement")
        if installment is None: issue(pid, "LoanInstallment inexistente")
        elif loan is None: issue(pid, "Loan inexistente")
        elif loan.member_id != settlement.member_id: issue(pid, "membro do settlement diverge do empréstimo")
        if received != dec(applied + excess): issue(pid, "amount_received != amount_applied + excess_amount")
        if applied != dec(principal + interest + penalty): issue(pid, "amount_applied != soma dos componentes")
        def rows(kind): return db.query(LedgerEntry).filter(LedgerEntry.reference_type == kind, LedgerEntry.reference_id == str(pid)).all()
        ir = rows("LOAN_INTEREST_PAYMENT")
        iv = [x for x in ir if x.direction == "CREDIT" and x.account == "CAIXINHA"]
        if interest > ZERO:
            if len(ir) != 1 or len(iv) != 1 or dec(iv[0].amount) != interest: issue(pid, "ledger de juros ausente, duplicado ou incorreto")
        elif ir: issue(pid, "ledger de juros indevido para componente zero")
        pr = rows("LOAN_PENALTY_PAYMENT")
        pv = [x for x in pr if x.direction == "CREDIT" and x.account == "CAIXINHA"]
        if penalty > ZERO:
            if len(pr) != 1 or len(pv) != 1 or dec(pv[0].amount) != penalty: issue(pid, "ledger de multa ausente, duplicado ou incorreto")
        elif pr: issue(pid, "ledger de multa indevido para componente zero")
        mr = db.query(MemberFinancialEntry).join(MemberFinancialAccount, MemberFinancialEntry.account_id == MemberFinancialAccount.id).filter(MemberFinancialEntry.entry_type == "LOAN_PRINCIPAL_PAYMENT", MemberFinancialEntry.reference_type == "LOAN_PRINCIPAL_PAYMENT", MemberFinancialEntry.reference_id == str(pid)).all()
        mv = [x for x in mr if x.direction == "CREDIT" and x.account.member_id == settlement.member_id]
        if principal > ZERO:
            if len(mr) != 1 or len(mv) != 1 or dec(mv[0].amount) != principal: issue(pid, "principal ausente, duplicado ou incorreto")
        elif mr: issue(pid, "principal indevido para componente zero")
    for iid, rows in by_installment.items():
        installment = db.get(LoanInstallment, iid)
        if installment is None: continue
        if sum((dec(x.principal_applied) + dec(x.interest_applied) for x in rows), ZERO) != dec(installment.paid_amount): issue(f"installment:{iid}", "soma principal+juros diverge de paid_amount")
        if sum((dec(x.penalty_applied) for x in rows), ZERO) != dec(installment.paid_penalty_amount): issue(f"installment:{iid}", "soma multa diverge de paid_penalty_amount")
    return issues

def _payment_ledger_rows(db, payment_id):
    return db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment_id)).all()


def _approved_contribution_issues(db, payment):
    issues = []
    pid = str(payment.id)
    settlements = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).all()
    if len(settlements) != 1:
        issues.append(f"{pid}: settlement de contribuição ausente ou duplicado")
        return issues
    settlement = settlements[0]
    if settlement.obligation_type != "CONTRIBUTION":
        issues.append(f"{pid}: obligation_type incompatível")
    if settlement.loan_installment_id is not None:
        issues.append(f"{pid}: settlement aponta para parcela de empréstimo")
    contribution = db.get(Contribution, settlement.contribution_id) if settlement.contribution_id is not None else None
    if contribution is None:
        issues.append(f"{pid}: contribuição do settlement inexistente")
    else:
        if contribution.member_id != settlement.member_id:
            issues.append(f"{pid}: membro da contribuição diverge do settlement")
        if (payment.reference_type or "").upper() != "CONTRIBUTION" or payment.reference_id != str(contribution.id):
            issues.append(f"{pid}: referência da contribuição incompatível")
    expected_received = money(payment.amount_received if payment.amount_received is not None else payment.amount)
    if money(settlement.amount_received) != expected_received:
        issues.append(f"{pid}: amount_received do settlement diverge do Payment")
    if money(settlement.amount_received) != money(settlement.amount_applied + settlement.excess_amount):
        issues.append(f"{pid}: settlement recebido não fecha aplicação e excesso")
    rows = _payment_ledger_rows(db, payment.id)
    if Decimal(money(settlement.amount_applied)) > ZERO:
        expected = [row for row in rows if row.reference_type == "CONTRIBUTION_PAYMENT"]
        if len(rows) != 1 or len(expected) != 1:
            issues.append(f"{pid}: ledger de contribuição ausente, duplicado ou com tipo incorreto")
        else:
            row = expected[0]
            if row.direction != "CREDIT" or row.account != "CAIXINHA" or money(row.amount) != money(settlement.amount_applied):
                issues.append(f"{pid}: ledger de contribuição incorreto")
    elif rows:
        issues.append(f"{pid}: ledger indevido para aplicação de contribuição zero")
    return issues


def _approved_agreement_issues(db, payment):
    issues = []
    pid = str(payment.id)
    if (payment.reference_type or "").upper() != "AGREEMENT_INSTALLMENT" or not (payment.reference_id or "").isdigit():
        return [f"{pid}: referência de acordo inválida"]
    installment = db.get(AgreementInstallment, int(payment.reference_id))
    if installment is None:
        return [f"{pid}: parcela de acordo inexistente"]
    agreement = db.get(CollectionAgreement, installment.agreement_id)
    if agreement is None:
        issues.append(f"{pid}: acordo inexistente")
    rows = _payment_ledger_rows(db, payment.id)
    expected = [row for row in rows if row.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT"]
    if len(rows) != 1 or len(expected) != 1:
        issues.append(f"{pid}: ledger de acordo ausente, duplicado ou com tipo incorreto")
    else:
        row = expected[0]
        if row.direction != "CREDIT" or row.account != "CAIXINHA" or Decimal(money(row.amount)) <= ZERO:
            issues.append(f"{pid}: ledger de acordo incorreto")
        if payment.amount_received is not None and Decimal(money(payment.amount_received)) > ZERO:
            expected_applied = min(Decimal(money(payment.amount_received)), Decimal(money(payment.amount)))
            if Decimal(money(row.amount)) != expected_applied:
                issues.append(f"{pid}: ledger de acordo diverge do valor aplicado esperado")
    return issues


def _approved_loan_issues(db, payment, pix_issues):
    pid = str(payment.id)
    settlements = db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).all()
    if len(settlements) != 1:
        return [f"{pid}: settlement PIX de empréstimo ausente ou duplicado"]
    settlement = settlements[0]
    if settlement.loan_installment_id is None:
        return [f"{pid}: settlement PIX sem parcela de empréstimo"]
    matched = [issue for issue in pix_issues if issue.startswith(f"{pid}:") or issue.startswith(f"installment:{settlement.loan_installment_id}:")]
    return [f"{pid}: {issue}" for issue in matched] if matched else []


def _approved_payment_issues(db, competence_start, competence_end):
    payments = db.query(Payment).filter(
        Payment.status == "approved",
        Payment.created_at >= competence_start,
        Payment.created_at < competence_end,
    ).all()
    pix_issues = _pix_installment_settlement_findings(db)
    issues = []
    for payment in payments:
        reference_type = (payment.reference_type or "").strip().upper()
        if reference_type == "CONTRIBUTION":
            issues.extend(_approved_contribution_issues(db, payment))
        elif reference_type == "LOAN_INSTALLMENT":
            issues.extend(_approved_loan_issues(db, payment, pix_issues))
        elif reference_type == "AGREEMENT_INSTALLMENT":
            issues.extend(_approved_agreement_issues(db, payment))
        else:
            issues.append(f"{payment.id}: reference_type ausente ou desconhecido")
    return issues


def build_advanced_reconciliation(db: Session, competence: date):
    a,b=bounds(competence); start=dt_start(a); end=dt_end(b)
    findings=[]
    def check(code, expected, observed, details):
        e=Decimal(expected or 0).quantize(CENT); o=Decimal(observed or 0).quantize(CENT)
        ok=e==o; findings.append({'code':code,'status':'PASS' if ok else 'FAIL','details':details,'expected':money(e),'observed':money(o)})
    contrib=Decimal(db.query(func.coalesce(func.sum(func.coalesce(Contribution.paid_amount,0)),0)).filter(Contribution.competence.between(a,b)).scalar() or 0)
    contrib_ledger=_sum_ledger(db,'CREDIT',['CONTRIBUTION_PAYMENT'],start,end)
    check('CONTRIBUTIONS',contrib,contrib_ledger,'Contribuições pagas devem bater com créditos no Ledger no período.')
    loan_pay=_sum_ledger(db,'CREDIT',['LOAN_INSTALLMENT_PAYMENT'],start,end)
    agr_pay=_sum_ledger(db,'CREDIT',['AGREEMENT_INSTALLMENT_PAYMENT'],start,end)
    disb=_sum_ledger(db,'DEBIT',['LOAN_DISBURSEMENT'],start,end)
    exp=Decimal(db.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.status=='POSTED',Expense.expense_date.between(a,b)).scalar() or 0)
    exp_ledger=_sum_ledger(db,'DEBIT',['EXPENSE'],start,end)
    check('EXPENSES',exp,exp_ledger,'Despesas lançadas devem bater com débitos no Ledger no período.')
    check('LOAN_PAYMENT_TOTAL',loan_pay+agr_pay,_sum_ledger(db,'CREDIT',['LOAN_INSTALLMENT_PAYMENT','AGREEMENT_INSTALLMENT_PAYMENT'],start,end),'Recebimentos de empréstimos/acordos devem estar no Ledger.')
    approved_total = Decimal(db.query(func.coalesce(func.sum(func.coalesce(Payment.amount_received, Payment.amount)),0)).filter(Payment.status=='approved',Payment.created_at>=start,Payment.created_at<end).scalar() or 0)
    posted_total = Decimal(db.query(func.coalesce(func.sum(func.coalesce(Payment.amount_received, Payment.amount)),0)).filter(Payment.status=='approved',Payment.ledger_posted_at.is_not(None),Payment.created_at>=start,Payment.created_at<end).scalar() or 0)
    approved_issues = _approved_payment_issues(db, start, end)
    findings.append({'code':'APPROVED_PAYMENTS','status':'FAIL' if approved_issues else 'PASS','details':'Pagamentos aprovados possuem evidência contábil compatível.' if not approved_issues else '; '.join(approved_issues),'expected':str(money(approved_total)),'observed':str(money(posted_total))})
    unprocessed=db.query(WebhookEvent).filter(WebhookEvent.processed==False).count()  # noqa
    findings.append({'code':'UNPROCESSED_WEBHOOKS','status':'PASS' if unprocessed==0 else 'FAIL','details':'Webhooks pendentes bloqueiam fechamento.','expected':'0','observed':str(unprocessed)})
    pix_issues = _pix_installment_settlement_findings(db)
    findings.append({'code':'PIX_INSTALLMENT_SETTLEMENT','status':'FAIL' if pix_issues else 'PASS','details':'Settlements PIX atuais de parcelas consistentes.' if not pix_issues else '; '.join(pix_issues),'expected':'0','observed':str(len(pix_issues))})
    open_installments = db.query(LoanInstallment).filter(LoanInstallment.status!='PAID').all()
    negative = sum(
        1 for i in open_installments
        if (
            Decimal(i.amount or 0).quantize(CENT, rounding=ROUND_HALF_UP) < ZERO
            or Decimal(i.paid_amount or 0).quantize(CENT, rounding=ROUND_HALF_UP) < ZERO
            or Decimal(i.penalty_amount or 0).quantize(CENT, rounding=ROUND_HALF_UP) < ZERO
            or Decimal(i.paid_penalty_amount or 0).quantize(CENT, rounding=ROUND_HALF_UP) < ZERO
            or Decimal(i.paid_amount or 0).quantize(CENT, rounding=ROUND_HALF_UP)
            > Decimal(i.amount or 0).quantize(CENT, rounding=ROUND_HALF_UP)
            or Decimal(i.paid_penalty_amount or 0).quantize(CENT, rounding=ROUND_HALF_UP)
            > Decimal(i.penalty_amount or 0).quantize(CENT, rounding=ROUND_HALF_UP)
        )
    )
    findings.append({'code':'NEGATIVE_INSTALLMENTS','status':'PASS' if negative==0 else 'FAIL','details':'Parcelas abertas não podem ter saldo negativo.','expected':'0','observed':str(negative)})
    # Operational exposure tie-out: open loan base/penalty and agreement balances must be non-negative.
    loan_out=Decimal('0')
    for i in open_installments:
        base_open = max(Decimal('0.00'), Decimal(i.amount or 0) - Decimal(i.paid_amount or 0))
        penalty_open = max(Decimal('0.00'), Decimal(i.penalty_amount or 0) - Decimal(i.paid_penalty_amount or 0))
        loan_out += base_open + penalty_open
    agr_out=Decimal('0')
    for i in db.query(AgreementInstallment).filter(AgreementInstallment.status!='PAID').all():
        agr_out += max(Decimal('0'),Decimal(i.amount)+Decimal(i.penalty_amount or 0)-Decimal(i.paid_amount or 0))
    snapshot={
      'schema':'v0.40','competence':a.isoformat(),'period_end':b.isoformat(),
      'contributions_paid':money(contrib),'contributions_ledger':money(contrib_ledger),
      'loan_payments_ledger':money(loan_pay),'agreement_payments_ledger':money(agr_pay),
      'loan_disbursements_ledger':money(disb),'expenses_posted':money(exp),'expenses_ledger':money(exp_ledger),
      'approved_payments':money(approved_total),'posted_payments':money(posted_total),
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
