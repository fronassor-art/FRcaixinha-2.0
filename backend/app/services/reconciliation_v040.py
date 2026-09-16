import hashlib, json
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (Contribution, Payment, PaymentSettlement, WebhookEvent, LedgerEntry, Loan, LoanInstallment,
                        Expense, CollectionAgreement, AgreementInstallment, FinancialReconciliation,
                        MemberFinancialAccount, MemberFinancialEntry)
from app.services.payment_settlement import _canonical_json, _ledger_snapshot, _receipt_snapshot

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
        if settlement.receipt_version not in {"v1", "v2"}:
            issue(pid, "receipt_version inválido")
        if settlement.receipt_version == "v2":
            if any(getattr(settlement, field) is None for field in (
                "loan_status_before", "loan_status_after",
                "loan_state_revision_before", "loan_state_revision_after",
            )):
                issue(pid, "evidência de estado do Loan incompleta")
            else:
                if settlement.loan_state_revision_before < 0 or settlement.loan_state_revision_after < 0:
                    issue(pid, "revisão do Loan negativa")
                if settlement.loan_state_revision_after != settlement.loan_state_revision_before + 1:
                    issue(pid, "delta de revisão do Loan inválido")
        elif any(getattr(settlement, field) is not None for field in (
            "loan_status_before", "loan_status_after",
            "loan_state_revision_before", "loan_state_revision_after",
        )):
            issue(pid, "settlement v1 possui evidência de estado do Loan")
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
        if settlement.receipt_version == "v2":
            if settlement.receipt_number != f"PIX-V2-{pid:012d}":
                issue(pid, "receipt_number v2 inválido")
            try:
                snapshot = json.loads(settlement.receipt_snapshot_json)
                expected_snapshot = _receipt_snapshot(
                    payment=payment,
                    settlement=settlement,
                    ledger=_ledger_snapshot(db, pid),
                )
                canonical_snapshot = _canonical_json(expected_snapshot)
                if snapshot != expected_snapshot or settlement.receipt_snapshot_json != canonical_snapshot:
                    issue(pid, "receipt_snapshot_json v2 inválido")
                expected_hash = hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest()
                if settlement.receipt_hash != expected_hash:
                    issue(pid, "receipt_hash v2 inválido")
            except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
                issue(pid, "receipt_snapshot_json v2 ilegível")
    for iid, rows in by_installment.items():
        installment = db.get(LoanInstallment, iid)
        if installment is None: continue
        if sum((dec(x.principal_applied) + dec(x.interest_applied) for x in rows), ZERO) != dec(installment.paid_amount): issue(f"installment:{iid}", "soma principal+juros diverge de paid_amount")
        if sum((dec(x.penalty_applied) for x in rows), ZERO) != dec(installment.paid_penalty_amount): issue(f"installment:{iid}", "soma multa diverge de paid_penalty_amount")
    return issues

def _payment_ledger_rows(db, payment_id):
    return db.query(LedgerEntry).filter(LedgerEntry.reference_id == str(payment_id)).all()


def _agreement_settlement_issue(code, settlement, detail):
    return f"{code}: settlement {settlement.id} (payment {settlement.payment_id}): {detail}"


def _agreement_settlement_findings(db):
    """Validate agreement settlements while leaving settlement-less legacy payments unchanged."""
    invalid = []
    ledger_mismatch = []
    cumulative = []
    settlements = db.query(PaymentSettlement).order_by(
        PaymentSettlement.agreement_installment_id,
        PaymentSettlement.confirmed_at,
        PaymentSettlement.id,
    ).all()
    by_installment = {}
    allowed_statuses = {"OPEN", "PENDING", "PARTIAL", "OVERDUE", "PAID"}

    def dec(value):
        return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)

    def add(code, settlement, detail):
        target = invalid if code == "AGREEMENT_SETTLEMENT_INVALID" else ledger_mismatch
        target.append(_agreement_settlement_issue(code, settlement, detail))

    for settlement in settlements:
        pid = settlement.payment_id
        payment = db.get(Payment, pid)
        payment_is_agreement = payment is not None and (payment.reference_type or "").strip().upper() == "AGREEMENT_INSTALLMENT"
        settlement_is_agreement = settlement.obligation_type == "AGREEMENT_INSTALLMENT"
        if not (payment_is_agreement or settlement_is_agreement):
            continue
        installment = db.get(AgreementInstallment, settlement.agreement_installment_id) if settlement.agreement_installment_id is not None else None
        agreement = db.get(CollectionAgreement, installment.agreement_id) if installment else None
        if settlement_is_agreement and settlement.agreement_installment_id is not None:
            by_installment.setdefault(settlement.agreement_installment_id, []).append(settlement)

        if payment is None:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "Payment inexistente")
            continue
        if payment.status != "approved":
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, f"Payment.status={payment.status!r}")
        if not payment_is_agreement:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "Payment.reference_type incompatível")
        if not settlement_is_agreement:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "obligation_type incompatível com Payment de acordo")
        if not payment.reference_id or payment.reference_id != str(settlement.agreement_installment_id):
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "Payment.reference_id incompatível")
        if not settlement_is_agreement or settlement.agreement_installment_id is None:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "agreement_installment_id incompatível ou ausente")
        elif installment is None:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "AgreementInstallment inexistente")
        elif agreement is None:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "CollectionAgreement inexistente")
        elif settlement.member_id != agreement.member_id:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "member_id diverge do membro da obrigação")
        if settlement.payment_id != payment.id:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "payment_id diverge do Payment.id")

        received = dec(settlement.amount_received)
        applied = dec(settlement.amount_applied)
        principal = dec(settlement.principal_applied)
        interest = dec(settlement.interest_applied)
        penalty = dec(settlement.penalty_applied)
        excess = dec(settlement.excess_amount)
        if any(value < ZERO for value in (received, applied, principal, interest, penalty, excess)):
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "valores negativos")
        if interest != ZERO:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "interest_applied deve ser zero")
        if received != dec(applied + excess):
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "amount_received != amount_applied + excess_amount")
        if applied != dec(principal + interest + penalty):
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "amount_applied != soma dos componentes")

        for field in ("obligation_status_before", "obligation_status_after"):
            if getattr(settlement, field) not in allowed_statuses:
                add("AGREEMENT_SETTLEMENT_INVALID", settlement, f"{field} inválido")
        if applied > ZERO and settlement.obligation_status_after not in {"PARTIAL", "PAID"}:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "pagamento aplicado não pode terminar em OPEN/PENDING/OVERDUE")
        if installment is not None and dec(installment.principal) < ZERO:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "principal da obrigação negativo")
        if installment is not None and dec(installment.penalty_amount) < ZERO:
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "penalty_amount da obrigação negativo")

        if payment is not None:
            expected_received = dec(payment.amount_received if payment.amount_received is not None else payment.amount)
            if received != expected_received:
                add("AGREEMENT_SETTLEMENT_INVALID", settlement, "amount_received diverge do Payment")
            rows = _payment_ledger_rows(db, payment.id)
            expected_rows = [row for row in rows if row.reference_type == "AGREEMENT_INSTALLMENT_PAYMENT"]
            valid_rows = [row for row in expected_rows if row.direction == "CREDIT" and row.account == "CAIXINHA" and dec(row.amount) == applied]
            if len(rows) != 1 or len(expected_rows) != 1 or len(valid_rows) != 1:
                ledger_mismatch.append(_agreement_settlement_issue(
                    "AGREEMENT_SETTLEMENT_LEDGER_MISMATCH", settlement,
                    "ledger ausente, duplicado, extra, ou com direção/conta/valor incompatível"))

        # Receipt fields are part of the immutable evidence generated by payment_settlement.py.
        if settlement.receipt_number != f"PIX-V1-{pid:012d}":
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "receipt_number inválido")
        if settlement.receipt_version != "v1":
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "receipt_version inválido")
        try:
            snapshot = json.loads(settlement.receipt_snapshot_json)
            expected_snapshot = _receipt_snapshot(payment=payment, settlement=settlement, ledger=_ledger_snapshot(db, pid))
            canonical_snapshot = _canonical_json(expected_snapshot)
            if snapshot != expected_snapshot or settlement.receipt_snapshot_json != canonical_snapshot:
                add("AGREEMENT_SETTLEMENT_INVALID", settlement, "receipt_snapshot_json inválido")
            expected_hash = hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest()
            if settlement.receipt_hash != expected_hash:
                add("AGREEMENT_SETTLEMENT_INVALID", settlement, "receipt_hash inválido")
        except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
            add("AGREEMENT_SETTLEMENT_INVALID", settlement, "receipt_snapshot_json ilegível")

    for installment_id, rows in by_installment.items():
        installment = db.get(AgreementInstallment, installment_id)
        if installment is None:
            continue
        legacy = db.query(Payment).filter(
            Payment.status == "approved",
            Payment.reference_type == "AGREEMENT_INSTALLMENT",
            Payment.reference_id == str(installment_id),
            ~db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == Payment.id).exists(),
        ).count()
        settled_principal = sum((dec(row.principal_applied) for row in rows), ZERO)
        settled_penalty = sum((dec(row.penalty_applied) for row in rows), ZERO)
        settled_applied = sum((dec(row.amount_applied) for row in rows), ZERO)
        settled_excess = sum((dec(row.excess_amount) for row in rows), ZERO)
        principal_open = max(ZERO, dec(installment.principal) - settled_principal)
        penalty_open = max(ZERO, dec(installment.penalty_amount) - settled_penalty)
        cumulative_mismatch = (
            settled_principal > dec(installment.principal)
            or settled_penalty > dec(installment.penalty_amount)
            or settled_applied != dec(settled_principal + settled_penalty)
            or dec(installment.paid_amount) != settled_principal
            or dec(installment.paid_penalty_amount) != settled_penalty
        )
        if not legacy and cumulative_mismatch:
            cumulative.append(
                f"AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH: installment {installment_id}: "
                f"principal/penalty settlements={money(settled_principal)}/{money(settled_penalty)} "
                f"paid={money(installment.paid_amount)}/{money(installment.paid_penalty_amount)} "
                f"open={money(principal_open)}/{money(penalty_open)} "
                f"applied={money(settled_applied)} excess={money(settled_excess)}"
            )

        timestamps_complete = all(row.confirmed_at is not None for row in rows)
        timestamps_unique = timestamps_complete and len({row.confirmed_at for row in rows}) == len(rows)
        if timestamps_unique:
            ordered = sorted(rows, key=lambda row: (row.confirmed_at, row.id))
            for previous, current in zip(ordered, ordered[1:]):
                if previous.obligation_status_after != current.obligation_status_before:
                    invalid.append(_agreement_settlement_issue(
                        "AGREEMENT_SETTLEMENT_INVALID", current,
                        "sequência histórica de status incompatível"))
    return invalid, ledger_mismatch, cumulative


def _agreement_collection_status_findings(db):
    issues = []
    agreements = db.query(CollectionAgreement).all()
    for agreement in agreements:
        installments = db.query(AgreementInstallment).filter(AgreementInstallment.agreement_id == agreement.id).all()
        all_paid = bool(installments) and all(item.status == "PAID" for item in installments)
        if agreement.status == "SETTLED" and not all_paid:
            issues.append(f"AGREEMENT_COLLECTION_STATUS_MISMATCH: acordo {agreement.id} SETTLED sem todas installments PAID")
        if all_paid and agreement.status != "SETTLED":
            issues.append(f"AGREEMENT_COLLECTION_STATUS_MISMATCH: acordo {agreement.id} com todas installments PAID sem status SETTLED")
    return issues


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
    if settlement.receipt_version != "v1":
        issues.append(f"{pid}: contribuição deve usar receipt_version v1")
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
    if db.query(PaymentSettlement).filter(PaymentSettlement.payment_id == payment.id).first() is not None:
        # Settlement-backed agreements are validated by the structured v1 findings below.
        return issues
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
    interest_received=_sum_ledger(db,'CREDIT',['LOAN_INTEREST_PAYMENT'],start,end)
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
    agreement_invalid, agreement_ledger, agreement_cumulative = _agreement_settlement_findings(db)
    agreement_collection = _agreement_collection_status_findings(db)
    for code, issues in (
        ('AGREEMENT_SETTLEMENT_INVALID', agreement_invalid),
        ('AGREEMENT_SETTLEMENT_LEDGER_MISMATCH', agreement_ledger),
        ('AGREEMENT_SETTLEMENT_CUMULATIVE_MISMATCH', agreement_cumulative),
        ('AGREEMENT_COLLECTION_STATUS_MISMATCH', agreement_collection),
    ):
        findings.append({
            'code': code,
            'status': 'FAIL' if issues else 'PASS',
            'details': 'Settlements de acordos consistentes.' if not issues else '; '.join(issues),
            'expected': '0',
            'observed': str(len(issues)),
        })
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
        principal_open = max(Decimal('0.00'), Decimal(i.principal or 0) - Decimal(i.paid_amount or 0))
        penalty_open = max(Decimal('0.00'), Decimal(i.penalty_amount or 0) - Decimal(i.paid_penalty_amount or 0))
        agr_out += principal_open + penalty_open
    snapshot={
      'schema':'v0.40','competence':a.isoformat(),'period_end':b.isoformat(),
      'contributions_paid':money(contrib),'contributions_ledger':money(contrib_ledger),
      'loan_payments_ledger':money(loan_pay),'agreement_payments_ledger':money(agr_pay),
      'interest_received':money(interest_received),
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
