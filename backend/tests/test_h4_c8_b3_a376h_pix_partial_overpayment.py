import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
import pytest
from app.api.loan_installment_payments import create_installment_pix
from app.api import payments as payments_api
from app.core.config import settings
from app.models import Contribution, LedgerEntry, LoanInstallment, MemberFinancialEntry, Payment, PaymentSettlement, WebhookEvent
from app.services.loan_obligation_runtime import loan_installment_obligation, materialize_daily_late_interest, materialize_fixed_penalty
from app.services.loan_installment_pix_attempts import _utc as normalize_utc
from app.services.member_financial import get_member_financial_position
from app.services.payment_settlement import settle_confirmed_pix_payment

# Reuse the established A3.76G fixture without duplicating its allocator.
_spec=importlib.util.spec_from_file_location("a376g", __file__.replace("a376h_pix_partial_overpayment.py", "a376g_versioned_settlement_reversal_payoff.py"));a376g=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(a376g)

def test_new_pix_after_partial_payment_uses_residual_obligation(monkeypatch):
    monkeypatch.setattr(settings,"loan_late_charge_effective_date",None)
    db=a376g._db(); member,loan,inst=a376g._single_installment(db,"r6a2c-residual")
    original=a376g._payment(db,inst,"r6a2c-original","5.00",confirmed_at=datetime(2026,1,1,12,tzinfo=timezone.utc))
    settlement=settle_confirmed_pix_payment(db,original,confirmation_source="TEST",confirmed_at=datetime(2026,1,1,12,tzinfo=timezone.utc))
    inst.status="PARTIAL" if inst.status!="PAID" else inst.status;db.commit();old_id=original.id;old_key=original.idempotency_key
    residual=loan_installment_obligation(db,inst,datetime(2026,1,1,12,tzinfo=timezone.utc)).total_due
    user=member.user
    async def provider(self,**kwargs): return {"id":"r6a2c-new","order_id":"order-r6a2c-new","status":"pending","qr_code":"qr","qr_code_base64":"b64","ticket_url":"url"}
    monkeypatch.setattr("app.api.loan_installment_payments.MercadoPagoClient.create_pix_payment",provider)
    result=asyncio.run(create_installment_pix(inst.id,user,db));new=db.get(Payment,result["payment_id"])
    import json
    snapshot=json.loads(new.financial_snapshot_json)
    assert new.id!=old_id and new.idempotency_key!=old_key and new.snapshot_hash and Decimal(snapshot["total_due"])==residual and Decimal(snapshot["total_due"])<Decimal("120.00")
    assert db.get(Payment,old_id).id==old_id

def test_partial_payment_webhook_retry_does_not_double_apply_components(monkeypatch):
    # Reuse the complete LoanInstallment v1 fixture and Request shape exercised
    # by the R5 webhook tests; only the provider amount is partial here.
    spec = importlib.util.spec_from_file_location(
        "a376h_webhook_fixture",
        __file__.replace("a376h_pix_partial_overpayment.py", "a376h_pix_webhook_state_machine.py"),
    )
    webhook_fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(webhook_fixture)
    db = webhook_fixture._db()
    payment, confirmed = webhook_fixture._versioned_payment(db, "r6a2c-retry")
    event_id = "event-r6a2c-partial"
    provider_calls = []
    monkeypatch.setattr(payments_api, "validate_mercado_pago_signature", lambda *a, **k: True)

    async def order(self, order_id):
        provider_calls.append(order_id)
        return webhook_fixture._remote(
            "r6a2c-retry",
            "approved",
            transaction_amount="5.00",
            date_approved="2026-01-01T15:30:00Z",
        )

    monkeypatch.setattr(payments_api.MercadoPagoClient, "get_order", order)
    request = webhook_fixture.EventRequest(event_id, "r6a2c-retry")

    # First delivery goes through the real webhook endpoint and real A3.76G
    # settlement writer, rather than calling the writer directly.
    first_response = asyncio.run(payments_api.mercado_pago_webhook(request, db))
    db.refresh(payment)
    installment = db.get(a376g.LoanInstallment, int(payment.reference_id))
    db.refresh(installment)
    first_event = db.query(WebhookEvent).filter_by(provider="mercado_pago", event_id=event_id).one()
    first_settlement = db.query(PaymentSettlement).filter_by(payment_id=payment.id).one()
    first_snapshot = {
        "settlement_ids": tuple(row.id for row in db.query(PaymentSettlement).order_by(PaymentSettlement.id)),
        "principal": first_settlement.principal_applied,
        "interest": first_settlement.interest_applied,
        "late_interest": first_settlement.late_interest_applied,
        "fixed_penalty": first_settlement.fixed_penalty_applied,
        "paid_amount": installment.paid_amount,
        "paid_penalty_amount": installment.paid_penalty_amount,
        "ledger_ids": tuple(row.id for row in db.query(LedgerEntry).order_by(LedgerEntry.id)),
        "mfe_ids": tuple(row.id for row in db.query(MemberFinancialEntry).order_by(MemberFinancialEntry.id)),
    }
    assert first_response["received"] is True
    assert first_event.processed is True
    assert first_settlement.amount_received == Decimal("5.00")
    stored_confirmed = normalize_utc(payment.confirmed_at)
    assert stored_confirmed == confirmed

    # The identical event is handled by WebhookEvent idempotency.  No provider
    # lookup, settlement, component allocation, or ledger/MFE write repeats.
    second_response = asyncio.run(payments_api.mercado_pago_webhook(request, db))
    db.refresh(installment)
    second_snapshot = {
        "settlement_ids": tuple(row.id for row in db.query(PaymentSettlement).order_by(PaymentSettlement.id)),
        "principal": first_settlement.principal_applied,
        "interest": first_settlement.interest_applied,
        "late_interest": first_settlement.late_interest_applied,
        "fixed_penalty": first_settlement.fixed_penalty_applied,
        "paid_amount": installment.paid_amount,
        "paid_penalty_amount": installment.paid_penalty_amount,
        "ledger_ids": tuple(row.id for row in db.query(LedgerEntry).order_by(LedgerEntry.id)),
        "mfe_ids": tuple(row.id for row in db.query(MemberFinancialEntry).order_by(MemberFinancialEntry.id)),
    }
    assert second_response == {"received": True, "duplicate": True}
    assert db.query(WebhookEvent).filter_by(provider="mercado_pago", event_id=event_id).count() == 1
    assert provider_calls == ["order-r6a2c-retry"]
    assert second_snapshot == first_snapshot
    assert db.query(PaymentSettlement).filter_by(payment_id=payment.id).count() == 1


def _prepared_overpayment(monkeypatch, suffix):
    monkeypatch.setattr(settings, "loan_late_charge_effective_date", date(2026, 1, 1))
    db = a376g._db()
    member, loan, installment = a376g._single_installment(db, suffix)
    confirmed = datetime(2026, 1, 3, 15, tzinfo=timezone.utc)
    payment = a376g._payment(db, installment, suffix, "140.00", confirmed_at=confirmed)
    payload = {
        "id": "provider-" + suffix,
        "status": "approved",
        "transactions": {"payments": [{"id": "provider-" + suffix, "status": "approved", "transaction_amount": "140.00"}]},
    }
    materialize_fixed_penalty(db, installment.id, through_date=confirmed, late_charge_effective_date=settings.loan_late_charge_effective_date)
    materialize_daily_late_interest(db, installment.id, through_date=confirmed, late_charge_effective_date=settings.loan_late_charge_effective_date)
    due_before = loan_installment_obligation(db, installment, confirmed)
    before_position = get_member_financial_position(db, member)
    return db, member, loan, installment, payment, due_before, payload, confirmed, before_position


def _settled_overpayment(monkeypatch, suffix):
    db, member, loan, installment, payment, due_before, payload, confirmed, before_position = _prepared_overpayment(monkeypatch, suffix)
    settlement = settle_confirmed_pix_payment(
        db, payment, confirmation_source="WEBHOOK", remote_payload=payload, confirmed_at=confirmed
    )
    db.commit()
    return db, member, loan, installment, payment, settlement, due_before, payload, confirmed, before_position


def test_pix_overpayment_excess_is_not_credited_to_own_balance(monkeypatch):
    db, member, loan, installment, payment, settlement, due_before, payload, confirmed, before_position = _settled_overpayment(monkeypatch, "r6a2d-own")
    after_position = get_member_financial_position(db, member)
    principal_entries = db.query(MemberFinancialEntry).filter(
        MemberFinancialEntry.reference_type == "LOAN_PRINCIPAL_PAYMENT",
        MemberFinancialEntry.reference_id == str(payment.id),
        MemberFinancialEntry.entry_type == "LOAN_PRINCIPAL_PAYMENT",
    ).all()
    principal_credit = sum((entry.amount for entry in principal_entries), Decimal("0.00"))
    assert principal_credit == settlement.principal_applied
    assert settlement.amount_received > settlement.amount_applied
    assert settlement.excess_amount > Decimal("0.00")
    assert after_position["own_balance"] - before_position["own_balance"] == principal_credit
    assert after_position["own_balance"] - before_position["own_balance"] != principal_credit + settlement.excess_amount
    assert not any(entry.amount == settlement.excess_amount for entry in principal_entries)
    assert settlement.amount_applied <= due_before.total_due


def test_pix_overpayment_excess_does_not_pay_other_installment(monkeypatch):
    db, member, loan, installment_a, payment, due_before, payload, confirmed, before_position = _prepared_overpayment(monkeypatch, "r6a2d-cross")
    installment_b = LoanInstallment(
        loan_id=loan.id, number=2, due_date=date(2026, 2, 1), principal=Decimal("50.00"),
        interest=Decimal("0.00"), amount=Decimal("50.00"), paid_amount=Decimal("0.00"),
        penalty_amount=Decimal("0.00"), paid_penalty_amount=Decimal("0.00"), status="OPEN",
    )
    db.add(installment_b)
    db.commit()
    before = (installment_b.paid_amount, installment_b.paid_penalty_amount, installment_b.status, loan_installment_obligation(db, installment_b, confirmed).total_due)
    settlement = settle_confirmed_pix_payment(
        db, payment, confirmation_source="WEBHOOK", remote_payload=payload, confirmed_at=confirmed
    )
    db.commit()
    db.refresh(installment_b)
    after = (installment_b.paid_amount, installment_b.paid_penalty_amount, installment_b.status, loan_installment_obligation(db, installment_b, confirmed).total_due)
    assert after == before
    assert db.query(PaymentSettlement).filter(PaymentSettlement.loan_installment_id == installment_b.id).count() == 0
    assert db.query(Payment).filter(Payment.reference_type == "LOAN_INSTALLMENT", Payment.reference_id == str(installment_b.id)).count() == 0
    assert settlement.excess_amount > Decimal("0.00")


def test_pix_overpayment_excess_does_not_pay_contribution(monkeypatch):
    db, member, loan, installment, payment, due_before, payload, confirmed, before_position = _prepared_overpayment(monkeypatch, "r6a2d-contribution")
    contribution = Contribution(
        member_id=member.id, competence=date(2026, 2, 1), amount=Decimal("25.00"),
        due_date=date(2026, 2, 1), paid_amount=Decimal("0.00"), status="PENDING",
    )
    db.add(contribution)
    db.commit()
    before = (contribution.status, contribution.amount, contribution.paid_amount, db.query(PaymentSettlement).filter(PaymentSettlement.contribution_id == contribution.id).count())
    settlement = settle_confirmed_pix_payment(
        db, payment, confirmation_source="WEBHOOK", remote_payload=payload, confirmed_at=confirmed
    )
    db.commit()
    db.refresh(contribution)
    after = (contribution.status, contribution.amount, contribution.paid_amount, db.query(PaymentSettlement).filter(PaymentSettlement.contribution_id == contribution.id).count())
    assert after == before
    assert db.query(Payment).filter(Payment.reference_type == "CONTRIBUTION", Payment.reference_id == str(contribution.id)).count() == 0
    assert db.query(MemberFinancialEntry).filter(MemberFinancialEntry.contribution_id == contribution.id).count() == 0
    assert settlement.excess_amount > Decimal("0.00")


def test_pix_overpayment_excess_remains_auditable(monkeypatch):
    db, member, loan, installment, payment, settlement, due_before, payload, confirmed, before_position = _settled_overpayment(monkeypatch, "r6a2d-audit")
    db.refresh(payment)
    assert settlement.amount_received == settlement.amount_applied + settlement.excess_amount
    assert settlement.excess_amount > Decimal("0.00")
    assert settlement.amount_applied <= due_before.total_due
    assert payment.provider_payment_id == "provider-r6a2d-audit"
    assert payment.provider_payload_json
    stored_confirmed = normalize_utc(payment.confirmed_at)
    assert stored_confirmed == confirmed
    assert settlement.receipt_snapshot_json and settlement.receipt_hash
    assert db.query(PaymentSettlement).filter_by(id=settlement.id).count() == 1
    assert settlement.principal_applied <= due_before.principal_due
    assert settlement.normal_interest_applied <= due_before.normal_price_interest_due
    assert settlement.fixed_penalty_applied <= due_before.fixed_penalty_due
    assert settlement.late_interest_applied <= due_before.late_interest_due
    assert settlement.amount_applied == (settlement.principal_applied + settlement.normal_interest_applied + settlement.fixed_penalty_applied + settlement.late_interest_applied)
