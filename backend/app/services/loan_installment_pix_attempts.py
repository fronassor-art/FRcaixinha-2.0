"""Durable, versioned LoanInstallment PIX attempt lifecycle.

Lock order on PostgreSQL is LoanInstallment then its pending Payment. TX1 commits a
local reservation before NETWORK; TX2 binds the provider response. DB/provider are
not atomically transacted.
"""
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.core.pix_attempt_v1 import (ReuseDecision, build_loan_installment_snapshot, canonical_json, evaluate_reuse, financial_date, local_expiry_utc, snapshot_hash)
from app.models import Loan, LoanInstallment, Payment, PaymentSettlement
from app.services.loan_obligation_runtime import loan_installment_obligation, materialize_daily_late_interest, materialize_fixed_penalty

PENDING="PENDING"; APPROVED="APPROVED"; EXPIRED="EXPIRED"; CANCELLED="CANCELLED"; FAILED="FAILED"; SUPERSEDED="SUPERSEDED"
TERMINAL={APPROVED,EXPIRED,CANCELLED,FAILED,SUPERSEDED}
def _utc(value):
    if value is None or value.tzinfo is not None and value.utcoffset() is not None:
        return value
    return value.replace(tzinfo=timezone.utc)

PROVIDER_CREATE_UNKNOWN="PROVIDER_CREATE_UNKNOWN"; STALE_EXPIRED="STALE_EXPIRED"; STALE_SUPERSEDED="STALE_SUPERSEDED"; SNAPSHOT_MISMATCH="SNAPSHOT_MISMATCH"; LEGACY_UNVERIFIED="LEGACY_UNVERIFIED"; RECONCILIATION_REQUIRED="RECONCILIATION_REQUIRED"

def transition(attempt, target):
    current=attempt.attempt_status
    if current==target: return
    if current!=PENDING or target not in TERMINAL: raise ValueError(f"invalid PIX attempt transition {current!r}->{target!r}")
    attempt.attempt_status=target

def _now(now=None): return now or datetime.now(timezone.utc)
def _pg(db): return db.bind is not None and db.bind.dialect.name=="postgresql"
def _lock_installment(db, iid):
    q=db.query(LoanInstallment).filter(LoanInstallment.id==iid)
    if _pg(db): q=q.with_for_update().populate_existing()
    return q.one()
def _pending(db,iid):
    q=db.query(Payment).filter(Payment.reference_type=="LOAN_INSTALLMENT",Payment.reference_id==str(iid),Payment.attempt_status==PENDING).order_by(Payment.id.desc())
    if _pg(db): q=q.with_for_update()
    return q.first()
def _snapshot(db,loan,inst,day):
    if settings.loan_late_charge_effective_date is not None:
        materialize_fixed_penalty(db,inst.id,through_date=day,late_charge_effective_date=settings.loan_late_charge_effective_date)
        materialize_daily_late_interest(db,inst.id,through_date=day,late_charge_effective_date=settings.loan_late_charge_effective_date)
    due=loan_installment_obligation(db,inst,day)
    return build_loan_installment_snapshot(loan_id=loan.id,installment_id=inst.id,installment_number=inst.number,calculated_for_date=day,principal_due=due.principal_due,normal_price_interest_due=due.normal_price_interest_due,fixed_penalty_due=due.fixed_penalty_due,late_interest_due=due.late_interest_due)
def reserve(db, installment_id, now=None):
    now=_now(now); inst=_lock_installment(db,installment_id); loan=db.get(Loan,inst.loan_id); day=financial_date(now); snap=_snapshot(db,loan,inst,day); digest=snapshot_hash(snap)
    if Decimal(snap["total_due"])<=0: raise ValueError("No open authoritative loan obligation")
    old=_pending(db,inst.id)
    if old:
        decision=evaluate_reuse(attempt_status=old.attempt_status,calculated_for_date=old.calculated_for_date,expires_at=_utc(old.expires_at),stored_snapshot_hash=old.snapshot_hash,current_financial_date=day,now=now,current_snapshot_hash=digest).decision
        if decision is ReuseDecision.REUSE: return old,False
        transition(old, EXPIRED if decision is ReuseDecision.EXPIRE else SUPERSEDED); db.commit()
    attempt=Payment(provider="mercado_pago",provider_payment_id=None,idempotency_key=f"frc-li-{inst.id}-{secrets.token_urlsafe(24)}",amount=Decimal(snap["total_due"]),status="PENDING",raw_status="PENDING",external_reference=f"loan-installment-{inst.id}",reference_type="LOAN_INSTALLMENT",reference_id=str(inst.id),attempt_status=PENDING,calculated_for_date=day,expires_at=local_expiry_utc(day),financial_snapshot_json=canonical_json(snap),snapshot_hash=digest)
    db.add(attempt)
    try: db.commit(); db.refresh(attempt)
    except IntegrityError:
        db.rollback(); winner=_pending(db,inst.id)
        if winner is None: raise
        return winner,False
    return attempt,True
def bind_provider(db,attempt_id,result):
    attempt=db.get(Payment,attempt_id)
    if not attempt or attempt.attempt_status!=PENDING: return attempt
    attempt.provider_order_id=str(result.get("order_id")) if result.get("order_id") else None
    attempt.provider_payment_id=str(result["id"])
    attempt.status=result.get("status","PENDING"); attempt.raw_status=attempt.status
    for field in ("qr_code","qr_code_base64","ticket_url"): setattr(attempt,field,result.get(field))
    attempt.reconciliation_status=None; db.commit(); db.refresh(attempt); return attempt
def mark_ambiguous(db,attempt_id):
    attempt=db.get(Payment,attempt_id)
    if attempt and attempt.attempt_status==PENDING: attempt.reconciliation_status=PROVIDER_CREATE_UNKNOWN; db.commit()
    return attempt
def valid_snapshot(payment):
    try:
        snap=json.loads(payment.financial_snapshot_json or "")
        return canonical_json(snap)==payment.financial_snapshot_json and snapshot_hash(snap)==payment.snapshot_hash
    except (ValueError,TypeError): return False
def approve_or_reconcile(db,payment,confirmed_at,remote_payload,settle):
    payment.provider_payload_json=canonical_json(remote_payload)
    if confirmed_at is not None: payment.confirmed_at=confirmed_at
    if payment.reference_type!="LOAN_INSTALLMENT": return None
    if payment.attempt_status is None or not valid_snapshot(payment): payment.reconciliation_status=LEGACY_UNVERIFIED if payment.attempt_status is None else SNAPSHOT_MISMATCH; return None
    if confirmed_at is None: payment.reconciliation_status=RECONCILIATION_REQUIRED; return None
    if payment.attempt_status==SUPERSEDED: payment.reconciliation_status=STALE_SUPERSEDED; return None
    if payment.attempt_status!=PENDING: payment.reconciliation_status=STALE_EXPIRED if payment.attempt_status==EXPIRED else RECONCILIATION_REQUIRED; return None
    if confirmed_at >= _utc(payment.expires_at): transition(payment,EXPIRED); payment.reconciliation_status=STALE_EXPIRED; return None
    inst=_lock_installment(db,int(payment.reference_id)); loan=db.get(Loan,inst.loan_id); day=financial_date(confirmed_at); snap=_snapshot(db,loan,inst,day)
    if payment.calculated_for_date!=day or snapshot_hash(snap)!=payment.snapshot_hash: payment.reconciliation_status=SNAPSHOT_MISMATCH; return None
    if db.query(PaymentSettlement).filter(PaymentSettlement.payment_id==payment.id).first(): return None
    transition(payment,APPROVED); return settle(db,payment,confirmation_source="WEBHOOK",remote_payload=remote_payload,confirmed_at=confirmed_at)
