"""A3.77B2-R1: deterministic annual closing preview, no financial writes."""
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import (
    AgreementInstallment, CollectionAgreement, Contribution, Cycle, CycleParticipation,
    Loan, LoanInstallment, Member, Payment, PaymentSettlement,
)
from app.services.cycle_foundation import ensure_first_cycle
from app.services.cycle_closing import (
    CashEvidence, ClosingContractGap, ClosingEvidence, ContributionEvidence,
    GainEvidence, ObligationEvidence, ParticipationEvidence,
    build_closing_evidence, calculate_cycle_closing, preview_cycle_closing,
)

D = Decimal
CUTOFF = datetime(2027, 12, 10, 18, tzinfo=timezone.utc)
BEFORE = datetime(2027, 12, 9, 12, tzinfo=timezone.utc)
AFTER = datetime(2027, 12, 11, 12, tzinfo=timezone.utc)
START = date(2026, 12, 10)


def part(member, *, status="ACTIVE", blocked_at=None, voluntary_exit_at=None, row_id=None):
    return ParticipationEvidence(row_id or member, member, status, blocked_at, voluntary_exit_at)


def paid(member, amount="100.00", *, contribution_id=None, when=BEFORE, status="PAID", events=None):
    value = D(amount)
    return ContributionEvidence(
        contribution_id or member, member, 1, value, status, None,
        events if events is not None else (CashEvidence(f"payment:{contribution_id or member}", value, when, "ORIGINAL"),),
    )


def gain(kind="LOAN_INTEREST", amount="20.00", *, source_id="gain:1", cycle_id=1, when=BEFORE, source_type="PAYMENT_SETTLEMENT"):
    return GainEvidence(source_id, source_type, kind, cycle_id, D(amount), when)


def obligation(member, amount, *, source_id=None, kind="LOAN_INSTALLMENT", loan_id=None, agreement_id=None):
    return ObligationEvidence(source_id or f"obligation:{member}", member, kind, D(amount), date(2027, 12, 1), loan_id, agreement_id)


def evidence(*, parts=None, contributions=None, gains=None, obligations=(), superseded=()):
    return ClosingEvidence(
        cycle_id=1, cycle_start_date=START, closing_cutoff_at=CUTOFF,
        participations=tuple(parts if parts is not None else [part(1)]),
        contributions=tuple(contributions if contributions is not None else [paid(1)]),
        gains=tuple(gains if gains is not None else [gain()]),
        obligations=tuple(obligations), superseded_loan_ids=tuple(superseded),
    )


def preview(**kwargs):
    return calculate_cycle_closing(evidence(**kwargs))


def members(result):
    return {row["member_id"]: row for row in result["participants"]}


def test_single_participant_fee_distribution_and_money_conservation():
    result = preview()
    row = members(result)[1]
    assert result["calculation_version"] == "cycle_closing_v1"
    assert result["financial_timezone"] == "America/Belem"
    assert result["gross_realized_result"] == "20.00"
    assert result["administration_fee_rate"] == "0.15"
    assert result["administration_fee"] == "3.00"
    assert result["distributable_result"] == "17.00"
    assert row["weight"] == "1"
    assert row["gross_share"] == "17.00"
    assert row["eligible_contributions"] == "100.00"
    assert row["gross_entitlement"] == "117.00"


def test_two_equal_participants_split_evenly():
    result = preview(parts=[part(1), part(2)], contributions=[paid(1), paid(2, contribution_id=2)])
    assert [row["gross_share"] for row in result["participants"]] == ["8.50", "8.50"]


def test_multiple_quotas_and_different_paid_values_set_proportional_weight():
    contributions = [paid(1, contribution_id=1), paid(1, contribution_id=3), paid(2, "50.00", contribution_id=2)]
    result = preview(parts=[part(1), part(2)], contributions=contributions, gains=[gain(amount="100.00")])
    assert result["total_eligible_contributions"] == "250.00"
    assert [row["gross_share"] for row in result["participants"]] == ["68.00", "17.00"]
    assert [row["eligible_contributions"] for row in result["participants"]] == ["200.00", "50.00"]


def test_partial_contribution_excluded_in_full():
    partial = paid(2, events=(CashEvidence("partial", D("99.00"), BEFORE, "ORIGINAL"),), status="PARTIAL", contribution_id=2)
    result = preview(parts=[part(1), part(2)], contributions=[paid(1), partial])
    assert members(result)[2]["eligible_contributions"] == "0.00"
    assert members(result)[2]["gross_share"] == "0.00"


def test_late_full_regularization_before_cutoff_is_eligible():
    payments = (CashEvidence("first", D("40.00"), datetime(2027, 2, 15, tzinfo=timezone.utc), "ORIGINAL"),
                CashEvidence("second", D("60.00"), BEFORE, "ORIGINAL"))
    row = paid(1, events=payments, status="PAID")
    assert preview(contributions=[row])["total_eligible_contributions"] == "100.00"


def test_payment_after_cutoff_is_excluded():
    row = paid(1, events=(CashEvidence("first", D("40.00"), BEFORE, "ORIGINAL"),
                          CashEvidence("second", D("60.00"), AFTER, "ORIGINAL")))
    assert preview(contributions=[row], gains=[])["total_eligible_contributions"] == "0.00"


def test_blocked_keeps_preblock_paid_base_but_loses_share_and_ignores_postblock_payment():
    blocked_at = datetime(2027, 6, 1, tzinfo=timezone.utc)
    blocked = part(1, status="BLOCKED_DELINQUENCY", blocked_at=blocked_at)
    early = paid(1, contribution_id=1, when=datetime(2027, 5, 1, tzinfo=timezone.utc))
    later = paid(1, contribution_id=3, when=datetime(2027, 7, 1, tzinfo=timezone.utc))
    result = preview(parts=[blocked, part(2)], contributions=[early, later, paid(2, contribution_id=2)])
    assert members(result)[1]["eligible_contributions"] == "0.00"
    assert members(result)[1]["refundable_contribution_principal"] == "100.00"
    assert members(result)[1]["gross_share"] == "0.00"
    assert members(result)[1]["gross_entitlement"] == "100.00"
    assert sum(D(row["eligible_contributions"]) for row in result["participants"]) == D(result["total_eligible_contributions"])
    assert members(result)[2]["gross_share"] == "17.00"


def test_voluntary_exit_keeps_paid_base_and_proportional_share():
    exited = part(1, status="VOLUNTARILY_EXITED", voluntary_exit_at=datetime(2027, 6, 1, tzinfo=timezone.utc))
    result = preview(parts=[exited, part(2)], contributions=[
        paid(1, when=datetime(2027, 5, 1, tzinfo=timezone.utc)),
        paid(1, contribution_id=3, when=datetime(2027, 7, 1, tzinfo=timezone.utc)),
        paid(2, contribution_id=2),
    ])
    assert members(result)[1]["eligible_contributions"] == "100.00"
    assert members(result)[1]["gross_share"] == "8.50"


def test_contribution_and_loan_principal_never_count_as_profit():
    result = preview(gains=[gain("CONTRIBUTION", "100.00", source_id="p:1"),
                            gain("LOAN_PRINCIPAL", "60.00", source_id="p:2"),
                            gain("LOAN_INTEREST", "20.00", source_id="p:3")])
    assert result["gross_realized_result"] == "20.00"
    assert len(result["source_trace"]["excluded_gains"]) == 2


def test_received_interest_fixed_penalty_late_interest_and_agreement_penalty_count():
    result = preview(gains=[gain("LOAN_INTEREST", "10.00", source_id="1"),
                            gain("LOAN_FIXED_PENALTY", "2.00", source_id="2"),
                            gain("LOAN_LATE_INTEREST", "3.00", source_id="3"),
                            gain("AGREEMENT_PENALTY", "5.00", source_id="4")])
    assert result["gross_realized_result"] == "20.00"


def test_reversal_reduces_realized_result_and_respects_cutoff():
    original = gain(amount="20.00", source_id="interest:original")
    reversal = gain(amount="-5.00", source_id="interest:reversal")
    result = preview(gains=[original, reversal])
    assert result["gross_realized_result"] == "15.00"
    assert result["administration_fee"] == "2.25"
    assert preview(gains=[original, replace(reversal, occurred_at=AFTER)])["gross_realized_result"] == "20.00"


def test_largest_remainder_and_stable_participation_id_tie_break():
    parts = [part(1, row_id=20), part(2, row_id=10), part(3, row_id=30)]
    contributions = [paid(1), paid(2, contribution_id=2), paid(3, contribution_id=3)]
    result = preview(parts=parts, contributions=contributions, gains=[gain(amount="0.02")])
    assert result["distributable_result"] == "0.02"
    assert members(result)[2]["gross_share"] == "0.01"
    assert members(result)[1]["gross_share"] == "0.01"
    assert members(result)[3]["gross_share"] == "0.00"
    assert sum((D(row["gross_share"]) for row in result["participants"]), D("0.00")) == D(result["distributable_result"])


@pytest.mark.parametrize("debt,compensation,net,residual", [
    ("5.00", "5.00", "112.00", "0.00"),
    ("117.00", "117.00", "0.00", "0.00"),
    ("200.00", "117.00", "0.00", "83.00"),
])
def test_projected_compensation_never_negative_and_preserves_residual(debt, compensation, net, residual):
    row = members(preview(obligations=[obligation(1, debt)]))[1]
    assert row["projected_compensation"] == compensation
    assert row["projected_net"] == net
    assert row["projected_residual_debt"] == residual
    assert D(row["projected_net"]) >= D("0.00")


def test_approved_agreement_supersedes_loan_installment_without_double_count():
    result = preview(obligations=[
        obligation(1, "80.00", source_id="loan:1", loan_id=7),
        obligation(1, "50.00", source_id="agreement:1", kind="AGREEMENT_INSTALLMENT", loan_id=7, agreement_id=11),
    ], superseded=[7])
    row = members(result)[1]
    assert row["compensable_obligations_total"] == "50.00"
    assert [item["source_id"] for item in row["compensable_obligations"]] == ["agreement:1"]



def test_two_applicable_agreements_for_same_loan_fail_closed():
    with pytest.raises(ClosingContractGap, match="MULTIPLE_APPLICABLE_AGREEMENTS"):
        preview(obligations=[
            obligation(1, "50.00", source_id="agreement:1", kind="AGREEMENT_INSTALLMENT", loan_id=7, agreement_id=11),
            obligation(1, "40.00", source_id="agreement:2", kind="AGREEMENT_INSTALLMENT", loan_id=7, agreement_id=12),
        ], superseded=[7])


def test_blocked_unpaid_contribution_principal_is_not_compensated():
    result = preview(parts=[part(1, status="BLOCKED_DELINQUENCY", blocked_at=BEFORE)],
                     gains=[], contributions=[], obligations=[
                         obligation(1, "100.00", kind="CONTRIBUTION_PRINCIPAL", source_id="contribution:unpaid"),
                         obligation(1, "2.00", kind="CONTRIBUTION_CHARGE", source_id="charge:1"),
                     ])
    assert members(result)[1]["compensable_obligations_total"] == "2.00"


def test_repeat_same_evidence_produces_identical_canonical_memory_and_hash():
    source = evidence(parts=[part(2), part(1)], contributions=[paid(2, contribution_id=2), paid(1)])
    first = calculate_cycle_closing(source)
    second = calculate_cycle_closing(replace(source, participations=tuple(reversed(source.participations)),
                                             contributions=tuple(reversed(source.contributions))))
    assert first == second
    assert first["calculation_hash"] == second["calculation_hash"]


def test_missing_cycle_attribution_is_reported_and_excluded():
    result = preview(gains=[gain(cycle_id=None)])
    assert result["gross_realized_result"] == "0.00"
    assert result["source_gaps"] == [{"code": "CYCLE_ATTRIBUTION_MISSING", "source": "gain:1"}]


def test_untyped_or_unpersisted_gain_is_not_invented():
    result = preview(gains=[gain(kind="UNKNOWN", source_type="REQUEST")])
    assert result["gross_realized_result"] == "0.00"
    assert result["source_gaps"][0]["code"] == "UNTYPED_GAIN"
    assert result["unavailable_result_sources"] == []



def test_typed_request_amount_and_unbacked_investment_stay_out_of_result():
    result = preview(gains=[
        gain("LOAN_INTEREST", "30.00", source_id="request:1", source_type="REQUEST"),
        gain("INVESTMENT_YIELD", "40.00", source_id="unbacked:1"),
        gain("OTHER_REALIZED_GAIN", "50.00", source_id="unbacked:2"),
    ])
    assert result["gross_realized_result"] == "0.00"
    assert [row["code"] for row in result["source_gaps"]] == [
        "UNAVAILABLE_RESULT_SOURCE", "UNAVAILABLE_RESULT_SOURCE", "UNAVAILABLE_RESULT_SOURCE"
    ]


def test_duplicate_gain_source_fails_closed():
    with pytest.raises(ClosingContractGap, match="DUPLICATE_GAIN_SOURCE"):
        preview(gains=[gain(), gain()])


def test_cutoff_is_explicit_and_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_cycle_closing(replace(evidence(), closing_cutoff_at=datetime(2027, 12, 10, 18)))
    assert preview(gains=[gain(when=CUTOFF)])["gross_realized_result"] == "20.00"
    assert preview(gains=[gain(when=AFTER)])["gross_realized_result"] == "0.00"


def test_unknown_obligation_fails_closed():
    with pytest.raises(ClosingContractGap, match="UNKNOWN_OBLIGATION_KIND"):
        preview(obligations=[obligation(1, "10.00", kind="UNDEFINED")])


def test_no_eligible_base_with_positive_result_is_contract_gap():
    with pytest.raises(ClosingContractGap, match="NO_ELIGIBLE_BASE"):
        preview(contributions=[], gains=[gain()])


@pytest.mark.parametrize("payment_status", ["approved", "PENDING"])
@pytest.mark.parametrize(("loan_cycle_id", "expected_gross", "attribution_gap"), [(None, "0.00", True), (1, "10.00", False), (2, "0.00", False)])
def test_preview_reads_persisted_settlement_without_any_write(payment_status, loan_cycle_id, expected_gross, attribution_gap):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    member = Member(user_id=1, group_id=1, status="ACTIVE")
    db.add(member)
    db.flush()
    cycle = ensure_first_cycle(db)
    participation = CycleParticipation(cycle_id=cycle.id, member_id=member.id, status="ACTIVE")
    db.add(participation)
    contribution = Contribution(
        cycle_id=cycle.id, member_id=member.id, competence=date(2027, 1, 1),
        due_date=date(2027, 1, 10), amount=D("100.00"), paid_amount=D("100.00"),
        status="PAID", paid_at=BEFORE,
    )
    db.add(contribution)
    db.flush()
    payment = Payment(
        provider="test", provider_payment_id="b2-r1-1", idempotency_key="b2-r1-1",
        amount=D("100.00"), status=payment_status, reference_type="CONTRIBUTION",
        reference_id=str(contribution.id), confirmed_at=BEFORE,
    )
    db.add(payment)
    db.flush()
    settlement = PaymentSettlement(
        payment_id=payment.id, member_id=member.id, obligation_type="CONTRIBUTION",
        contribution_id=contribution.id, amount_received=D("100.00"),
        amount_applied=D("100.00"), principal_applied=D("100.00"),
        interest_applied=D("0.00"), penalty_applied=D("0.00"), excess_amount=D("0.00"),
        obligation_status_before="PENDING", obligation_status_after="PAID",
        confirmed_at=BEFORE, confirmation_source="TEST", receipt_number="B2-R1-1",
        receipt_version="v1", receipt_snapshot_json="{}", receipt_hash="b2-r1-1",
    )
    db.add(settlement)
    if loan_cycle_id == 2:
        db.add(Cycle(start_date=date(2028, 12, 10), entry_deadline=date(2029, 1, 10),
                     closing_reference_date=date(2029, 12, 10), monthly_amount=D("150.00"),
                     months=12, max_quotas=50, status="OPEN"))
        db.flush()
        other_cycle = db.query(Cycle).filter(Cycle.start_date == date(2028, 12, 10)).one()
        db.add(CycleParticipation(cycle_id=other_cycle.id, member_id=member.id, status="ACTIVE"))
        db.flush()
    loan = Loan(member_id=member.id, cycle_id=loan_cycle_id, principal=D("50.00"),
                monthly_rate=D("0.20"), installments=1, status="RESTRUCTURED")
    db.add(loan)
    db.flush()
    installment = LoanInstallment(
        loan_id=loan.id, number=1, due_date=date(2027, 11, 10),
        principal=D("50.00"), interest=D("10.00"), amount=D("60.00"),
        paid_amount=D("10.00"), penalty_amount=D("0.00"),
        paid_penalty_amount=D("0.00"), status="AGREED",
    )
    db.add(installment)
    db.flush()
    interest_received_at = datetime(2027, 9, 1, 12, tzinfo=timezone.utc)
    agreement_decided_at = datetime(2027, 10, 10, 12, tzinfo=timezone.utc)
    interest_payment = Payment(
        provider="test", provider_payment_id="b2-r1-interest",
        idempotency_key="b2-r1-interest", amount=D("60.00"), status="approved",
        reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id),
        confirmed_at=interest_received_at,
    )
    db.add(interest_payment)
    db.flush()
    db.add(PaymentSettlement(
        payment_id=interest_payment.id, member_id=member.id,
        obligation_type="LOAN_INSTALLMENT", loan_installment_id=installment.id,
        amount_received=D("60.00"), amount_applied=D("60.00"),
        principal_applied=D("50.00"), interest_applied=D("10.00"),
        penalty_applied=D("0.00"), excess_amount=D("0.00"),
        obligation_status_before="OPEN", obligation_status_after="PARTIAL",
        confirmed_at=interest_received_at, confirmation_source="TEST", receipt_number="B2-R1-2",
        receipt_version="v1", receipt_snapshot_json="{}", receipt_hash="b2-r1-2",
    ))
    agreement = CollectionAgreement(
        loan_id=loan.id, member_id=member.id, requested_by=1, status="APPROVED",
        installments=1, total_amount=D("50.00"), snapshot="{}", decided_at=agreement_decided_at,
    )
    db.add(agreement)
    db.flush()
    db.add(AgreementInstallment(
        agreement_id=agreement.id, number=1, due_date=date(2027, 11, 10),
        principal=D("50.00"), penalty_amount=D("0.00"), amount=D("50.00"),
        paid_amount=D("0.00"), paid_penalty_amount=D("0.00"), status="OPEN",
    ))
    db.commit()
    cycle_id = cycle.id
    member.status = "SUSPENDED"  # pending in memory: no_autoflush must not persist it
    statements = []

    def inspect(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", inspect)
    try:
        if payment_status == "PENDING":
            with pytest.raises(ClosingContractGap, match="UNSUPPORTED_PAYMENT_EVIDENCE"):
                preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
            assert statements and all(command.startswith("SELECT") for command in statements)
            assert member in db.dirty
            return
        source = build_closing_evidence(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
        assert source.contributions[0].events[0].amount == D("100.00")
        assert source.superseded_loan_ids == (loan.id,)
        result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
        assert result["total_eligible_contributions"] == "100.00"
        assert result["gross_realized_result"] == expected_gross
        if expected_gross == "10.00":
            assert [gain["kind"] for gain in result["source_trace"]["included_gains"]] == ["LOAN_INTEREST"]
            assert any(gain["kind"] == "LOAN_PRINCIPAL" for gain in result["source_trace"]["excluded_gains"])
        expected_share = "8.50" if expected_gross == "10.00" else "0.00"
        assert result["participants"][0]["gross_share"] == expected_share
        assert result["participants"][0]["gross_entitlement"] == ("108.50" if expected_gross == "10.00" else "100.00")
        assert result["participants"][0]["compensable_obligations_total"] == "50.00"
        assert result["participants"][0]["projected_net"] == ("58.50" if expected_gross == "10.00" else "50.00")
        assert any(row["code"] == "CYCLE_ATTRIBUTION_MISSING" for row in result["source_gaps"]) is attribution_gap
        assert statements and all(command.startswith("SELECT") for command in statements)
        assert not db.new and member in db.dirty and not db.deleted
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
        db.close()
        engine.dispose()


def _loan_preview_db(*, own_balance_kind=None, penalty="0.00", last_penalty_date=None):
    from app.models import MemberFinancialAccount, MemberFinancialEntry

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    member = Member(user_id=1, group_id=1, status="ACTIVE")
    db.add(member)
    db.flush()
    cycle = ensure_first_cycle(db)
    db.add(CycleParticipation(cycle_id=cycle.id, member_id=member.id, status="ACTIVE"))
    loan = Loan(
        member_id=member.id, principal=D("50.00"), monthly_rate=D("0.20"),
        installments=1, status="PAID" if own_balance_kind == "OWN_BALANCE_SETTLEMENT" else "ACTIVE",
        principal_settled_with_own_balance=D("50.00") if own_balance_kind == "OWN_BALANCE_SETTLEMENT" else D("20.00") if own_balance_kind else D("0.00"),
        paid_at=BEFORE if own_balance_kind == "OWN_BALANCE_SETTLEMENT" else None,
    )
    db.add(loan)
    db.flush()
    db.add(LoanInstallment(
        loan_id=loan.id, number=1, due_date=date(2027, 11, 10),
        principal=D("50.00"), interest=D("10.00"), amount=D("60.00"),
        paid_amount=D("0.00"), penalty_amount=D(penalty),
        paid_penalty_amount=D("0.00"), last_penalty_date=last_penalty_date,
        paid_at=BEFORE if own_balance_kind == "OWN_BALANCE_SETTLEMENT" else None,
        status="PAID" if own_balance_kind == "OWN_BALANCE_SETTLEMENT" else "OPEN",
    ))
    if own_balance_kind:
        account = MemberFinancialAccount(member_id=member.id)
        db.add(account)
        db.flush()
        db.add(MemberFinancialEntry(
            account_id=account.id, entry_type=own_balance_kind,
            direction="DEBIT", amount=D("50.00") if own_balance_kind == "OWN_BALANCE_SETTLEMENT" else D("20.00"),
            reference_type=own_balance_kind, reference_id=str(loan.id),
            created_at=BEFORE,
        ))
    db.commit()
    return db, engine, cycle.id, loan.id


def test_own_balance_payoff_is_not_compensated_again():
    db, engine, cycle_id, loan_id = _loan_preview_db(own_balance_kind="OWN_BALANCE_SETTLEMENT")
    try:
        result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
        assert result["participants"][0]["compensable_obligations_total"] == "0.00"
        assert result["source_trace"]["own_balance_settled_loan_ids"] == [loan_id]
    finally:
        db.close()
        engine.dispose()


def test_partial_own_balance_renegotiation_requires_installment_allocation():
    db, engine, cycle_id, _ = _loan_preview_db(own_balance_kind="OWN_BALANCE_RENEGOTIATION")
    try:
        with pytest.raises(ClosingContractGap, match="OWN_BALANCE_PARTIAL_ALLOCATION"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


def test_legacy_penalty_assessed_on_cutoff_day_without_timestamp_is_gap():
    db, engine, cycle_id, _ = _loan_preview_db(
        penalty="3.00", last_penalty_date=date(2027, 12, 10)
    )
    try:
        with pytest.raises(ClosingContractGap, match="LEGACY_PENALTY_CUTOFF_UNPROVABLE"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


def test_persisted_penalty_components_are_classified_without_principal():
    from app.services.cycle_closing import _gain_from_event
    from app.services.payment_financial_events import PaymentFinancialEvent

    settlement = PaymentSettlement(
        id=99, fixed_penalty_applied=D("2.00"), late_interest_applied=D("3.00"),
        settlement_component_version="v1", principal_applied=D("0.00"),
    )
    event = PaymentFinancialEvent(
        event_id="settlement:99:LOAN_PENALTY:original", payment_id=1,
        settlement_id=99, payment_reversal_id=None, component="LOAN_PENALTY",
        amount=D("5.00"), occurred_at=BEFORE, source="ORIGINAL",
    )
    rows = _gain_from_event(event, settlement, None)
    assert [(row.kind, row.amount, row.cycle_id) for row in rows] == [
        ("LOAN_FIXED_PENALTY", D("2.00"), None),
        ("LOAN_LATE_INTEREST", D("3.00"), None),
    ]
    with pytest.raises(ClosingContractGap, match="PENALTY_COMPONENT_MISMATCH"):
        _gain_from_event(replace(event, amount=D("6.00")), settlement, None)


def test_blocked_partial_principal_is_refundable_without_rateio():
    blocked = part(1, status="BLOCKED_DELINQUENCY", blocked_at=BEFORE)
    partial = paid(
        1, "150.00", status="CANCELLED",
        events=(CashEvidence("settlement:1", D("60.00"), BEFORE, "ORIGINAL", 1),),
    )
    result = preview(
        parts=[blocked, part(2)], contributions=[partial, paid(2, contribution_id=2)],
        obligations=[obligation(1, "90.00", kind="CONTRIBUTION_PRINCIPAL")],
    )
    row = members(result)[1]
    assert row["rateio_eligible_contribution_principal"] == "0.00"
    assert row["refundable_contribution_principal"] == "60.00"
    assert row["gross_share"] == "0.00"
    assert row["gross_entitlement"] == "60.00"
    assert row["compensable_obligations_total"] == "0.00"
    assert row["projected_net"] == "60.00"


def test_blocked_multiple_partial_receipts_and_postblock_cash_do_not_create_rateio():
    block = datetime(2027, 6, 1, tzinfo=timezone.utc)
    row = paid(1, "150.00", status="CANCELLED", events=(
        CashEvidence("s:1", D("20.00"), datetime(2027, 5, 1, tzinfo=timezone.utc), "ORIGINAL", 1),
        CashEvidence("s:2", D("40.00"), datetime(2027, 5, 2, tzinfo=timezone.utc), "ORIGINAL", 2),
        CashEvidence("s:3", D("90.00"), datetime(2027, 7, 1, tzinfo=timezone.utc), "ORIGINAL", 3),
    ))
    result = preview(
        parts=[part(1, status="BLOCKED_DELINQUENCY", blocked_at=block), part(2)],
        contributions=[row, paid(2, contribution_id=2)],
    )
    blocked = members(result)[1]
    assert blocked["rateio_eligible_contribution_principal"] == "0.00"
    assert blocked["refundable_contribution_principal"] == "60.00"
    assert blocked["gross_share"] == "0.00"
    assert blocked["gross_entitlement"] == "60.00"
    assert result["source_trace"]["contributions"][0]["event_ids"] == ["s:1", "s:2"]


def test_blocked_reversal_of_excluded_postblock_settlement_does_not_reduce_refund():
    block = datetime(2027, 6, 1, tzinfo=timezone.utc)
    contribution = paid(1, "150.00", status="CANCELLED", events=(
        CashEvidence("s:1", D("60.00"), datetime(2027, 5, 1, tzinfo=timezone.utc), "ORIGINAL", 1),
        CashEvidence("s:2", D("90.00"), datetime(2027, 7, 1, tzinfo=timezone.utc), "ORIGINAL", 2),
        CashEvidence("s:2:reversal", D("-90.00"), datetime(2027, 8, 1, tzinfo=timezone.utc), "REVERSAL", 2),
    ))
    result = preview(
        parts=[part(1, status="BLOCKED_DELINQUENCY", blocked_at=block), part(2)],
        contributions=[contribution, paid(2, contribution_id=2)],
    )
    assert members(result)[1]["refundable_contribution_principal"] == "60.00"


def _legacy_preview_db(*, paid_at=BEFORE, linked_payment=False):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    member = Member(user_id=1, group_id=1, status="ACTIVE")
    db.add(member)
    db.flush()
    cycle = ensure_first_cycle(db)
    db.add(CycleParticipation(cycle_id=cycle.id, member_id=member.id, status="ACTIVE"))
    contribution = Contribution(
        cycle_id=cycle.id, member_id=member.id, competence=date(2027, 1, 1),
        due_date=date(2027, 1, 10), amount=D("100.00"),
        paid_amount=D("100.00"), status="PAID", paid_at=paid_at,
    )
    db.add(contribution)
    db.flush()
    if linked_payment:
        payment = Payment(
            provider="test", provider_payment_id="legacy-payment",
            idempotency_key="legacy-payment", amount=D("100.00"),
            status="approved", reference_type="CONTRIBUTION",
            reference_id=str(contribution.id), confirmed_at=BEFORE,
        )
        db.add(payment)
        db.flush()
        contribution.payment_id = payment.id
    db.commit()
    return db, engine, cycle.id, contribution.id


@pytest.mark.parametrize("paid_at,expected", [
    (BEFORE, "100.00"), (CUTOFF, "100.00"), (AFTER, "0.00"),
])
def test_legacy_paid_at_respects_inclusive_cutoff(paid_at, expected):
    db, engine, cycle_id, _ = _legacy_preview_db(paid_at=paid_at)
    try:
        result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
        assert result["total_eligible_contributions"] == expected
    finally:
        db.close()
        engine.dispose()


def test_legacy_approved_linked_payment_matches_b1_fallback():
    db, engine, cycle_id, _ = _legacy_preview_db(paid_at=None, linked_payment=True)
    try:
        result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
        assert result["total_eligible_contributions"] == "100.00"
        assert result["source_trace"]["contributions"][0]["event_ids"][0].startswith("payment:")
    finally:
        db.close()
        engine.dispose()


def test_legacy_paid_without_dated_proof_fails_closed():
    db, engine, cycle_id, _ = _legacy_preview_db(paid_at=None)
    try:
        with pytest.raises(ClosingContractGap, match="LEGACY_CONTRIBUTION_PAYMENT_CUTOFF_UNPROVABLE"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("entry_amount", ["49.00", "51.00", "49.99", "50.01"])
def test_own_balance_settlement_amount_mismatch_fails_closed(entry_amount):
    from app.models import MemberFinancialEntry

    db, engine, cycle_id, _ = _loan_preview_db(own_balance_kind="OWN_BALANCE_SETTLEMENT")
    try:
        db.query(MemberFinancialEntry).one().amount = D(entry_amount)
        db.commit()
        with pytest.raises(ClosingContractGap, match="OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


def test_own_balance_settlement_wrong_member_account_fails_closed():
    from app.models import MemberFinancialAccount, MemberFinancialEntry

    db, engine, cycle_id, _ = _loan_preview_db(own_balance_kind="OWN_BALANCE_SETTLEMENT")
    try:
        other = Member(user_id=2, group_id=1, status="ACTIVE")
        db.add(other)
        db.flush()
        account = MemberFinancialAccount(member_id=other.id)
        db.add(account)
        db.flush()
        db.query(MemberFinancialEntry).one().account_id = account.id
        db.commit()
        with pytest.raises(ClosingContractGap, match="OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


def test_own_balance_settlement_wrong_loan_fails_closed():
    from app.models import MemberFinancialEntry

    db, engine, cycle_id, loan_id = _loan_preview_db(own_balance_kind="OWN_BALANCE_SETTLEMENT")
    try:
        original = db.get(Loan, loan_id)
        other = Loan(
            member_id=original.member_id, principal=D("50.00"),
            monthly_rate=D("0.20"), installments=1, status="ACTIVE",
        )
        db.add(other)
        db.flush()
        db.query(MemberFinancialEntry).one().reference_id = str(other.id)
        db.commit()
        with pytest.raises(ClosingContractGap, match="OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


def test_own_balance_settlement_missing_evidence_fails_closed():
    from app.models import MemberFinancialEntry

    db, engine, cycle_id, _ = _loan_preview_db(own_balance_kind="OWN_BALANCE_SETTLEMENT")
    try:
        db.delete(db.query(MemberFinancialEntry).one())
        db.commit()
        with pytest.raises(ClosingContractGap, match="OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("loan_status,old_status,create_replacement", [
    ("ACTIVE", "OPEN", True),
    ("RESTRUCTURED", "OPEN", True),
    ("RESTRUCTURED", "AGREED", False),
])
def test_incomplete_agreement_transition_fails_closed(loan_status, old_status, create_replacement):
    db, engine, cycle_id, loan_id = _loan_preview_db()
    try:
        loan = db.get(Loan, loan_id)
        loan.status = loan_status
        old = db.query(LoanInstallment).one()
        old.status = old_status
        agreement = CollectionAgreement(
            loan_id=loan_id, member_id=loan.member_id, requested_by=1,
            status="APPROVED", installments=1, total_amount=D("60.00"),
            snapshot="{}", decided_at=datetime(2027, 10, 10, 12, tzinfo=timezone.utc),
        )
        db.add(agreement)
        db.flush()
        if create_replacement:
            db.add(AgreementInstallment(
                agreement_id=agreement.id, number=1, due_date=date(2027, 11, 10),
                principal=D("60.00"), penalty_amount=D("0.00"), amount=D("60.00"),
                paid_amount=D("0.00"), paid_penalty_amount=D("0.00"), status="OPEN",
            ))
        db.commit()
        with pytest.raises(ClosingContractGap, match="AGREEMENT_RESTRUCTURE_INTEGRITY_GAP"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


def test_dirty_contribution_amount_is_rejected_before_any_financial_read():
    db, engine, cycle_id, contribution_id = _legacy_preview_db()
    try:
        contribution = db.get(Contribution, contribution_id)
        contribution.amount = D("101.00")
        statements = []

        def inspect(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lstrip().upper())

        event.listen(engine, "before_cursor_execute", inspect)
        try:
            with pytest.raises(ClosingContractGap, match="UNPERSISTED_FINANCIAL_STATE"):
                preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
            assert statements == []
            assert contribution in db.dirty
        finally:
            event.remove(engine, "before_cursor_execute", inspect)
    finally:
        db.close()
        engine.dispose()


def test_negative_realized_result_fails_closed():
    with pytest.raises(ClosingContractGap, match="NEGATIVE_REALIZED_RESULT"):
        preview(gains=[gain(amount="-0.01")])


def _persisted_reversal_preview():
    from app.models import Group, User
    from app.services.payment_settlement import settle_confirmed_pix_payment
    from test_payment_reversal_contribution_v104 import _db

    db = _db()
    admin = User(
        name="Master B2 R1R1", email="master-b2-r1r1@test",
        cpf="master-b2-r1r1", password_hash="x",
        role="ADMIN", is_active=True, is_master=True,
    )
    member_user = User(
        name="Member B2 R1R1", email="member-b2-r1r1@test",
        cpf="member-b2-r1r1", password_hash="x",
    )
    group = Group(name="Group B2 R1R1", max_installments=6)
    db.add_all([admin, member_user, group])
    db.flush()
    member = Member(user_id=member_user.id, group_id=group.id)
    db.add(member)
    db.flush()
    cycle = ensure_first_cycle(db)
    db.add(CycleParticipation(cycle_id=cycle.id, member_id=member.id, status="ACTIVE"))
    contribution = Contribution(
        cycle_id=cycle.id, member_id=member.id,
        competence=date(2027, 1, 1), due_date=date(2027, 1, 10),
        amount=D("100.00"), status="PENDING",
    )
    db.add(contribution)
    db.flush()
    payment = Payment(
        provider="mercado_pago", provider_payment_id="b2-r1r1-real",
        idempotency_key="b2-r1r1-real", amount=D("100.00"),
        status="approved", raw_status="approved",
        reference_type="CONTRIBUTION", reference_id=str(contribution.id),
    )
    db.add(payment)
    db.flush()
    settlement = settle_confirmed_pix_payment(
        db, payment, confirmation_source="WEBHOOK",
        confirmed_at=datetime(2027, 6, 1, 12, tzinfo=timezone.utc),
    )
    db.commit()
    return db, admin, contribution, payment, settlement, cycle.id


@pytest.mark.parametrize("reversal_delta_hours,cutoff_delta_hours,expected", [
    (1, 2, "0.00"),
    (1, 1, "0.00"),
    (1, 0, "100.00"),
])
def test_persisted_settlement_reversal_respects_cutoff(reversal_delta_hours, cutoff_delta_hours, expected):
    from datetime import timedelta
    from app.services.payment_reversal import reverse_payment

    db, admin, _contribution, payment, settlement, cycle_id = _persisted_reversal_preview()
    try:
        settled_at = settlement.confirmed_at.replace(tzinfo=timezone.utc)
        reverse_payment(
            db, payment_id=payment.id, admin_id=admin.id,
            reason="Reversão de teste", now=settled_at + timedelta(hours=reversal_delta_hours),
        )
        db.commit()
        result = preview_cycle_closing(
            db, cycle_id=cycle_id,
            closing_cutoff_at=settled_at + timedelta(hours=cutoff_delta_hours),
        )
        assert result["total_eligible_contributions"] == expected
        assert result["gross_realized_result"] == "0.00"
        assert len(result["source_trace"]["contributions"][0]["event_ids"]) == (
            1 if expected == "100.00" else 2
        )
    finally:
        db.close()


@pytest.mark.parametrize("microseconds_before,expected", [(0, "100.00"), (1, "0.00")])
def test_persisted_settlement_exact_cutoff_and_after_cutoff(microseconds_before, expected):
    from datetime import timedelta

    db, _admin, _contribution, _payment, settlement, cycle_id = _persisted_reversal_preview()
    try:
        cutoff = settlement.confirmed_at.replace(tzinfo=timezone.utc) - timedelta(microseconds=microseconds_before)
        result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=cutoff)
        assert result["total_eligible_contributions"] == expected
        assert result["gross_realized_result"] == "0.00"
    finally:
        db.close()


def test_own_balance_settlement_with_prior_principal_receipt_reconciles():
    from app.models import MemberFinancialEntry

    db, engine, cycle_id, loan_id = _loan_preview_db(own_balance_kind="OWN_BALANCE_SETTLEMENT")
    try:
        loan = db.get(Loan, loan_id)
        installment = db.query(LoanInstallment).one()
        loan.principal_settled_with_own_balance = D("20.00")
        db.query(MemberFinancialEntry).one().amount = D("20.00")
        installment.paid_amount = D("30.00")
        payment = Payment(
            provider="test", provider_payment_id="own-balance-prior-principal",
            idempotency_key="own-balance-prior-principal",
            amount=D("30.00"), status="approved",
            reference_type="LOAN_INSTALLMENT", reference_id=str(installment.id),
            confirmed_at=BEFORE,
        )
        db.add(payment)
        db.flush()
        db.add(PaymentSettlement(
            payment_id=payment.id, member_id=loan.member_id,
            obligation_type="LOAN_INSTALLMENT", loan_installment_id=installment.id,
            amount_received=D("30.00"), amount_applied=D("30.00"),
            principal_applied=D("30.00"), interest_applied=D("0.00"),
            penalty_applied=D("0.00"), excess_amount=D("0.00"),
            obligation_status_before="OPEN", obligation_status_after="PARTIAL",
            confirmed_at=BEFORE, confirmation_source="TEST",
            receipt_number="B2-R1R1-OWN-1", receipt_version="v1",
            receipt_snapshot_json="{}", receipt_hash="b2-r1r1-own-1",
        ))
        db.commit()
        result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
        assert result["participants"][0]["compensable_obligations_total"] == "0.00"
        assert result["source_trace"]["own_balance_settled_loan_ids"] == [loan_id]
    finally:
        db.close()
        engine.dispose()


def test_contribution_reversal_without_matching_original_fails_closed():
    contribution = paid(1, events=(
        CashEvidence("original", D("100.00"), BEFORE, "ORIGINAL", 1),
        CashEvidence("orphan-reversal", D("-10.00"), BEFORE, "REVERSAL", 2),
    ))
    with pytest.raises(ClosingContractGap, match="CONTRIBUTION_REVERSAL_SOURCE_AMBIGUOUS"):
        preview(contributions=[contribution], gains=[])


def test_agreement_writer_one_cent_quote_tolerance_keeps_single_debt():
    db, engine, cycle_id, loan_id = _loan_preview_db()
    try:
        loan = db.get(Loan, loan_id)
        loan.status = "RESTRUCTURED"
        db.query(LoanInstallment).one().status = "AGREED"
        agreement = CollectionAgreement(
            loan_id=loan_id, member_id=loan.member_id, requested_by=1,
            status="APPROVED", installments=1, total_amount=D("60.01"),
            snapshot="{}", decided_at=datetime(2027, 10, 10, 12, tzinfo=timezone.utc),
        )
        db.add(agreement)
        db.flush()
        db.add(AgreementInstallment(
            agreement_id=agreement.id, number=1, due_date=date(2027, 11, 10),
            principal=D("60.00"), penalty_amount=D("0.00"), amount=D("60.00"),
            paid_amount=D("0.00"), paid_penalty_amount=D("0.00"), status="OPEN",
        ))
        db.commit()
        result = preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
        assert result["participants"][0]["compensable_obligations_total"] == "60.00"
        assert result["participants"][0]["compensable_obligations"][0]["kind"] == "AGREEMENT_INSTALLMENT"
    finally:
        db.close()
        engine.dispose()


def test_retrodated_agreement_replacement_fails_closed():
    db, engine, cycle_id, loan_id = _loan_preview_db()
    try:
        loan = db.get(Loan, loan_id)
        loan.status = "RESTRUCTURED"
        db.query(LoanInstallment).one().status = "AGREED"
        agreement = CollectionAgreement(
            loan_id=loan_id, member_id=loan.member_id, requested_by=1,
            status="APPROVED", installments=1, total_amount=D("60.00"),
            snapshot="{}", decided_at=BEFORE,
        )
        db.add(agreement)
        db.flush()
        db.add(AgreementInstallment(
            agreement_id=agreement.id, number=1, due_date=date(2027, 11, 10),
            principal=D("60.00"), penalty_amount=D("0.00"), amount=D("60.00"),
            paid_amount=D("0.00"), paid_penalty_amount=D("0.00"), status="OPEN",
        ))
        db.commit()
        with pytest.raises(ClosingContractGap, match="AGREEMENT_RESTRUCTURE_INTEGRITY_GAP"):
            preview_cycle_closing(db, cycle_id=cycle_id, closing_cutoff_at=CUTOFF)
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("status", ["PARTIAL", "PENDING"])
def test_current_partial_or_pending_with_full_cash_and_no_later_reversal_fails_closed(status):
    contribution = paid(1, status=status, events=(
        CashEvidence("settlement:1", D("100.00"), BEFORE, "ORIGINAL", 1),
    ))
    with pytest.raises(ClosingContractGap, match="CONTRIBUTION_STATUS_EVIDENCE_MISMATCH"):
        preview(contributions=[contribution], gains=[])


def test_current_pending_after_later_reversal_preserves_historical_full_payment():
    contribution = paid(1, status="PENDING", events=(
        CashEvidence("settlement:1", D("100.00"), BEFORE, "ORIGINAL", 1),
        CashEvidence("settlement:1:reversal", D("-100.00"), AFTER, "REVERSAL", 1),
    ))
    result = preview(contributions=[contribution], gains=[])
    assert result["total_eligible_contributions"] == "100.00"
    assert result["participants"][0]["refundable_contribution_principal"] == "100.00"


def test_unrelated_later_reversal_cannot_explain_partial_current_status():
    contribution = paid(1, status="PARTIAL", events=(
        CashEvidence("settlement:1", D("100.00"), BEFORE, "ORIGINAL", 1),
        CashEvidence("settlement:2:reversal", D("-40.00"), AFTER, "REVERSAL", 2),
    ))
    with pytest.raises(ClosingContractGap, match="CONTRIBUTION_STATUS_EVIDENCE_MISMATCH"):
        preview(contributions=[contribution], gains=[])
