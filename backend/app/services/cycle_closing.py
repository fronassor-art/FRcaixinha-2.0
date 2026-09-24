"""Read-only annual cycle preview from typed financial evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, localcontext
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import (
    AgreementInstallment, CollectionAgreement, Contribution,
    ContributionChargeEvent, Cycle, CycleParticipation, Loan, LoanInstallment,
    LoanLateChargeEvent, Member, MemberFinancialAccount, MemberFinancialEntry,
    Payment, PaymentReversal, PaymentReversalComponent, PaymentSettlement, CycleRealizedGainEvent,
    LedgerEntry,
)
from app.services.payment_financial_events import payment_financial_events


CALCULATION_VERSION = "cycle_closing_v1"
ADMINISTRATION_FEE_RATE = Decimal("0.15")
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
FINANCIAL_ZONE = ZoneInfo("America/Belem")
RESULT_KINDS = frozenset({
    "LOAN_INTEREST", "LOAN_FIXED_PENALTY", "LOAN_LATE_INTEREST",
    "LOAN_PENALTY", "AGREEMENT_PENALTY",
    "INVESTMENT_YIELD", "OTHER_REALIZED_GAIN",
})
UNAVAILABLE_RESULT_KINDS = frozenset({"INVESTMENT_YIELD", "OTHER_REALIZED_GAIN"})
RECEIPT_RESULT_KINDS = RESULT_KINDS - UNAVAILABLE_RESULT_KINDS
NON_RESULT_KINDS = frozenset({
    "CONTRIBUTION", "LOAN_PRINCIPAL", "AGREEMENT_PRINCIPAL",
})


class ClosingContractGap(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class CashEvidence:
    event_id: str
    amount: Decimal
    occurred_at: datetime
    source: str  # ORIGINAL or REVERSAL
    settlement_id: int | None = None


@dataclass(frozen=True)
class ParticipationEvidence:
    id: int
    member_id: int
    status: str
    blocked_at: datetime | None = None
    voluntary_exit_at: datetime | None = None


@dataclass(frozen=True)
class ContributionEvidence:
    id: int
    member_id: int
    cycle_id: int
    amount: Decimal
    status: str
    cancelled_at: datetime | None
    events: tuple[CashEvidence, ...] = ()


@dataclass(frozen=True)
class GainEvidence:
    source_id: str
    source_type: str
    kind: str
    cycle_id: int | None
    amount: Decimal
    occurred_at: datetime


@dataclass(frozen=True)
class ObligationEvidence:
    source_id: str
    member_id: int
    kind: str
    amount: Decimal
    due_date: date
    loan_id: int | None = None
    agreement_id: int | None = None


@dataclass(frozen=True)
class ClosingEvidence:
    cycle_id: int
    cycle_start_date: date
    closing_cutoff_at: datetime
    participations: tuple[ParticipationEvidence, ...]
    contributions: tuple[ContributionEvidence, ...] = ()
    gains: tuple[GainEvidence, ...] = ()
    obligations: tuple[ObligationEvidence, ...] = ()
    superseded_loan_ids: tuple[int, ...] = ()
    own_balance_settled_loan_ids: tuple[int, ...] = ()
    source_gaps: tuple[tuple[str, str], ...] = ()


def _money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("financial values must be Decimal")
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("closing cutoff and evidence timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    # SQLite drops timezone information from persisted timestamps.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else _utc(value)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _effective_status(row: ParticipationEvidence, cutoff: datetime) -> tuple[str, datetime]:
    if row.status == "BLOCKED_DELINQUENCY":
        if row.blocked_at is None:
            raise ClosingContractGap("PARTICIPATION_STATE", f"participation {row.id} has no blocked_at")
        if _stored_utc(row.blocked_at) <= cutoff:
            return row.status, _stored_utc(row.blocked_at)
    if row.status == "VOLUNTARILY_EXITED":
        if row.voluntary_exit_at is None:
            raise ClosingContractGap("PARTICIPATION_STATE", f"participation {row.id} has no voluntary_exit_at")
        if _stored_utc(row.voluntary_exit_at) <= cutoff:
            return row.status, _stored_utc(row.voluntary_exit_at)
    if row.status not in {"ACTIVE", "BLOCKED_DELINQUENCY", "VOLUNTARILY_EXITED"}:
        raise ClosingContractGap("PARTICIPATION_STATE", f"unknown status {row.status}")
    return "ACTIVE", cutoff


def _allocation_cents(
    distributable: Decimal, bases: dict[int, Decimal], tie_keys: dict[int, tuple[int, int]],
) -> dict[int, Decimal]:
    total_cents = int(distributable / CENT)
    total_base_cents = sum(int(value / CENT) for value in bases.values())
    if total_cents and not total_base_cents:
        raise ClosingContractGap("NO_ELIGIBLE_BASE", "positive result has no eligible contributions")
    if not total_base_cents:
        return {member_id: ZERO for member_id in bases}
    with localcontext() as context:
        context.prec = 60
        exact = {
            member_id: Decimal(total_cents) * Decimal(int(base / CENT)) / Decimal(total_base_cents)
            for member_id, base in bases.items()
        }
        floors = {
            member_id: int(value.to_integral_value(rounding=ROUND_DOWN))
            for member_id, value in exact.items()
        }
        residual = total_cents - sum(floors.values())
        order = sorted(bases, key=lambda member_id: (
            -(exact[member_id] - Decimal(floors[member_id])), tie_keys[member_id]
        ))
        for member_id in order[:residual]:
            floors[member_id] += 1
    return {member_id: Decimal(cents) * CENT for member_id, cents in floors.items()}


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calculate_cycle_closing(evidence: ClosingEvidence) -> dict:
    """Calculate from trusted, typed sources without accessing the database."""
    cutoff = _utc(evidence.closing_cutoff_at)
    cutoff_local = cutoff.astimezone(FINANCIAL_ZONE)
    start_at = datetime.combine(evidence.cycle_start_date, time.min, FINANCIAL_ZONE).astimezone(timezone.utc)
    if cutoff < start_at:
        raise ClosingContractGap("CUTOFF_BEFORE_CYCLE", "closing cutoff precedes cycle start")
    rows = sorted(evidence.participations, key=lambda row: (row.id, row.member_id))
    if len({row.member_id for row in rows}) != len(rows):
        raise ClosingContractGap("DUPLICATE_PARTICIPATION", "member occurs twice in the cycle")
    by_member = {row.member_id: row for row in rows}
    statuses = {row.member_id: _effective_status(row, cutoff) for row in rows}
    rateio_principal_by_member = {row.member_id: ZERO for row in rows}
    refundable_principal_by_member = {row.member_id: ZERO for row in rows}
    contribution_trace = []
    source_gaps = [{"code": code, "source": source} for code, source in evidence.source_gaps]
    for contribution in sorted(evidence.contributions, key=lambda row: row.id):
        if contribution.cycle_id != evidence.cycle_id:
            continue
        row = by_member.get(contribution.member_id)
        if row is None:
            raise ClosingContractGap("CONTRIBUTION_WITHOUT_PARTICIPATION", str(contribution.id))
        _, participation_limit = statuses[row.member_id]
        limit = participation_limit
        if contribution.cancelled_at is not None:
            limit = min(limit, _stored_utc(contribution.cancelled_at))
        signed = ZERO
        used_ids = []
        included_settlement_ids: set[int] = set()
        excluded_settlement_ids: set[int] = set()
        for event in sorted(contribution.events, key=lambda item: (_utc(item.occurred_at), item.event_id)):
            moment = _utc(event.occurred_at)
            amount = _money(event.amount)
            if moment > cutoff:
                continue
            if event.source == "ORIGINAL":
                if amount <= ZERO:
                    raise ClosingContractGap("INVALID_CONTRIBUTION_EVENT", event.event_id)
                if moment <= limit:
                    signed += amount
                    used_ids.append(event.event_id)
                    if event.settlement_id is not None:
                        included_settlement_ids.add(event.settlement_id)
                elif event.settlement_id is not None:
                    excluded_settlement_ids.add(event.settlement_id)
            elif event.source == "REVERSAL":
                if amount >= ZERO:
                    raise ClosingContractGap("INVALID_CONTRIBUTION_EVENT", event.event_id)
                if event.settlement_id in excluded_settlement_ids:
                    continue
                if event.settlement_id not in included_settlement_ids:
                    raise ClosingContractGap("CONTRIBUTION_REVERSAL_SOURCE_AMBIGUOUS", event.event_id)
                signed += amount
                used_ids.append(event.event_id)
            else:
                raise ClosingContractGap("INVALID_CONTRIBUTION_EVENT", event.event_id)
        amount_due = _money(contribution.amount)
        if signed < ZERO or signed > amount_due:
            raise ClosingContractGap("CONTRIBUTION_EVIDENCE_MISMATCH", str(contribution.id))
        fully_paid = signed == amount_due and amount_due > ZERO
        blocked = statuses[row.member_id][0] == "BLOCKED_DELINQUENCY"
        if fully_paid and not blocked and contribution.status != "PAID":
            later_reversal = any(
                event.source == "REVERSAL"
                and event.settlement_id in included_settlement_ids
                and _utc(event.occurred_at) > cutoff
                for event in contribution.events
            )
            if not later_reversal:
                raise ClosingContractGap(
                    "CONTRIBUTION_STATUS_EVIDENCE_MISMATCH", str(contribution.id)
                )
        rateio_eligible = fully_paid and not blocked
        if rateio_eligible:
            rateio_principal_by_member[row.member_id] += amount_due
        refundable = signed if blocked else (amount_due if fully_paid else ZERO)
        refundable_principal_by_member[row.member_id] += refundable
        contribution_trace.append({
            "contribution_id": contribution.id, "member_id": row.member_id,
            "amount": str(amount_due), "confirmed_net_at_cutoff": str(_money(signed)),
            "eligible": rateio_eligible, "refundable_principal": str(_money(refundable)),
            "event_ids": used_ids,
        })

    included_gains = []
    excluded_gains = []
    gross = ZERO
    seen_gain_sources: set[tuple[str, str, str]] = set()
    for gain in sorted(evidence.gains, key=lambda item: (item.source_type, item.source_id, item.kind)):
        source_key = (gain.source_type, gain.source_id, gain.kind)
        if source_key in seen_gain_sources:
            raise ClosingContractGap("DUPLICATE_GAIN_SOURCE", gain.source_id)
        seen_gain_sources.add(source_key)
        amount = _money(gain.amount)
        moment = _utc(gain.occurred_at)
        reason = None
        if moment > cutoff or moment < start_at:
            reason = "OUTSIDE_CYCLE_CUTOFF"
        elif gain.kind in NON_RESULT_KINDS:
            reason = "PRINCIPAL_IS_NOT_RESULT"
        elif gain.kind not in RESULT_KINDS or not gain.source_type or not gain.source_id:
            reason = "UNTYPED_GAIN"
            source_gaps.append({"code": reason, "source": gain.source_id})
        elif gain.kind in RECEIPT_RESULT_KINDS and gain.source_type != "PAYMENT_SETTLEMENT":
            reason = "UNAVAILABLE_RESULT_SOURCE"
            source_gaps.append({"code": reason, "source": gain.source_id})
        elif gain.kind in UNAVAILABLE_RESULT_KINDS and gain.source_type != "CYCLE_REALIZED_GAIN_EVENT":
            reason = "UNAVAILABLE_RESULT_SOURCE"
            source_gaps.append({"code": reason, "source": gain.source_id})
        elif gain.cycle_id is None:
            reason = "CYCLE_ATTRIBUTION_MISSING"
            source_gaps.append({"code": reason, "source": gain.source_id})
        elif gain.cycle_id != evidence.cycle_id:
            reason = "OTHER_CYCLE"
        target = {"source_id": gain.source_id, "source_type": gain.source_type,
                  "kind": gain.kind, "amount": str(amount), "occurred_at": _iso(moment)}
        if reason is None:
            included_gains.append(target)
            gross += amount
        else:
            excluded_gains.append({**target, "reason": reason})
    gross = _money(gross)
    if gross < ZERO:
        raise ClosingContractGap("NEGATIVE_REALIZED_RESULT", "fee and payout rule for negative result is undefined")
    fee = _money(gross * ADMINISTRATION_FEE_RATE)
    distributable = _money(gross - fee)
    rateio_bases = {
        row.member_id: rateio_principal_by_member[row.member_id]
        for row in rows if statuses[row.member_id][0] != "BLOCKED_DELINQUENCY"
    }
    total_base = _money(sum(rateio_bases.values(), ZERO))
    ties = {row.member_id: (row.id, row.member_id) for row in rows if row.member_id in rateio_bases}
    shares = _allocation_cents(distributable, rateio_bases, ties)
    superseded = set(evidence.superseded_loan_ids)
    obligations_by_member: dict[int, list[dict]] = {row.member_id: [] for row in rows}
    seen_obligations: set[str] = set()
    agreements_by_loan: dict[int, int] = {}
    for obligation in sorted(evidence.obligations, key=lambda item: item.source_id):
        if obligation.member_id not in by_member or obligation.due_date > cutoff_local.date():
            continue
        if obligation.source_id in seen_obligations:
            raise ClosingContractGap("DUPLICATE_OBLIGATION", obligation.source_id)
        seen_obligations.add(obligation.source_id)
        if obligation.kind == "AGREEMENT_INSTALLMENT":
            if obligation.loan_id is None or obligation.agreement_id is None:
                raise ClosingContractGap("AGREEMENT_SOURCE_INCOMPLETE", obligation.source_id)
            previous = agreements_by_loan.setdefault(obligation.loan_id, obligation.agreement_id)
            if previous != obligation.agreement_id:
                raise ClosingContractGap("MULTIPLE_APPLICABLE_AGREEMENTS", str(obligation.loan_id))
        if obligation.kind == "LOAN_INSTALLMENT" and obligation.loan_id in superseded:
            continue
        if obligation.kind == "CONTRIBUTION_PRINCIPAL" and statuses[obligation.member_id][0] == "BLOCKED_DELINQUENCY":
            continue
        if obligation.kind not in {
            "CONTRIBUTION_PRINCIPAL", "CONTRIBUTION_CHARGE",
            "LOAN_INSTALLMENT", "AGREEMENT_INSTALLMENT",
        }:
            raise ClosingContractGap("UNKNOWN_OBLIGATION_KIND", obligation.source_id)
        amount = _money(obligation.amount)
        if amount < ZERO:
            raise ClosingContractGap("NEGATIVE_OBLIGATION", obligation.source_id)
        if amount:
            obligations_by_member[obligation.member_id].append({
                "source_id": obligation.source_id, "kind": obligation.kind,
                "amount": str(amount), "due_date": obligation.due_date.isoformat(),
                "loan_id": obligation.loan_id, "agreement_id": obligation.agreement_id,
            })

    participants = []
    with localcontext() as context:
        context.prec = 60
        for row in rows:
            status, _limit = statuses[row.member_id]
            rateio_principal = _money(rateio_principal_by_member[row.member_id])
            refundable_principal = _money(refundable_principal_by_member[row.member_id])
            weight = Decimal(rateio_principal) / Decimal(total_base) if total_base and status != "BLOCKED_DELINQUENCY" else Decimal(0)
            share = shares.get(row.member_id, ZERO)
            # The composition of gross entitlement is explicit in the memory.
            entitlement = _money(refundable_principal + share)
            obligation_rows = obligations_by_member[row.member_id]
            owed = _money(sum((Decimal(item["amount"]) for item in obligation_rows), ZERO))
            compensation = min(entitlement, owed)
            participants.append({
                "member_id": row.member_id,
                "cycle_participation_id": row.id,
                "participation_status": status,
                "eligible_contributions": str(rateio_principal),
                "rateio_eligible_contribution_principal": str(rateio_principal),
                "refundable_contribution_principal": str(refundable_principal),
                "weight": format(weight, "f"),
                "gross_share": str(share),
                "gross_entitlement": str(entitlement),
                "compensable_obligations": obligation_rows,
                "compensable_obligations_total": str(owed),
                "projected_compensation": str(compensation),
                "projected_net": str(_money(entitlement - compensation)),
                "projected_residual_debt": str(_money(max(ZERO, owed - entitlement))),
            })
    if sum((Decimal(item["gross_share"]) for item in participants), ZERO) != distributable:
        raise AssertionError("largest remainder allocation lost cents")
    memory = {
        "calculation_version": CALCULATION_VERSION,
        "cycle_id": evidence.cycle_id,
        "cycle_start_date": evidence.cycle_start_date.isoformat(),
        "closing_cutoff_at": cutoff_local.isoformat(),
        "financial_timezone": "America/Belem",
        "gross_realized_result": str(gross),
        "administration_fee_rate": str(ADMINISTRATION_FEE_RATE),
        "administration_fee": str(fee),
        "distributable_result": str(distributable),
        "total_eligible_contributions": str(total_base),
        "participants": participants,
        "source_trace": {
            "contributions": contribution_trace,
            "included_gains": included_gains,
            "excluded_gains": excluded_gains,
            "own_balance_settled_loan_ids": sorted(evidence.own_balance_settled_loan_ids),
        },
        "source_gaps": sorted(source_gaps, key=lambda item: (item["code"], item["source"])),
        "unavailable_result_sources": [],
    }
    memory["calculation_hash"] = hashlib.sha256(_canonical(memory).encode()).hexdigest()
    return memory


def _gain_from_event(
    event, settlement: PaymentSettlement, contribution_cycle_id: int | None,
    loan_cycle_id: int | None = None,
):
    """Classify received components and use only explicit Loan.cycle_id attribution."""
    if event.component == "CONTRIBUTION":
        return (GainEvidence(
            source_id=event.event_id, source_type="PAYMENT_SETTLEMENT",
            kind="CONTRIBUTION", cycle_id=contribution_cycle_id,
            amount=Decimal(event.amount), occurred_at=event.occurred_at,
        ),)
    if event.component in {"LOAN_PRINCIPAL", "LOAN_INTEREST"}:
        return (GainEvidence(
            source_id=event.event_id, source_type="PAYMENT_SETTLEMENT",
            kind=event.component, cycle_id=loan_cycle_id,
            amount=Decimal(event.amount), occurred_at=event.occurred_at,
        ),)
    if event.component == "LOAN_PENALTY":
        fixed = _money(Decimal(settlement.fixed_penalty_applied or 0))
        late = _money(Decimal(settlement.late_interest_applied or 0))
        sign = Decimal(-1) if event.amount < ZERO else Decimal(1)
        if settlement.settlement_component_version and fixed + late != abs(Decimal(event.amount)):
            raise ClosingContractGap("PENALTY_COMPONENT_MISMATCH", str(settlement.id))
        if settlement.settlement_component_version:
            return tuple(
                GainEvidence(
                    source_id=f"{event.event_id}:{kind}", source_type="PAYMENT_SETTLEMENT",
                    kind=kind, cycle_id=loan_cycle_id, amount=sign * amount,
                    occurred_at=event.occurred_at,
                )
                for kind, amount in (
                    ("LOAN_FIXED_PENALTY", fixed), ("LOAN_LATE_INTEREST", late)
                ) if amount
            )
        return (GainEvidence(
            source_id=event.event_id, source_type="PAYMENT_SETTLEMENT",
            kind="LOAN_PENALTY", cycle_id=loan_cycle_id,
            amount=Decimal(event.amount), occurred_at=event.occurred_at,
        ),)
    if event.component == "AGREEMENT":
        sign = Decimal(-1) if event.amount < ZERO else Decimal(1)
        penalty = _money(Decimal(settlement.penalty_applied or 0))
        principal = _money(Decimal(settlement.principal_applied or 0))
        if principal + penalty != abs(Decimal(event.amount)):
            raise ClosingContractGap("AGREEMENT_COMPONENT_MISMATCH", str(settlement.id))
        return tuple(
            GainEvidence(
                source_id=f"{event.event_id}:{kind}", source_type="PAYMENT_SETTLEMENT",
                kind=kind, cycle_id=loan_cycle_id, amount=sign * amount,
                occurred_at=event.occurred_at,
            )
            for kind, amount in (
                ("AGREEMENT_PENALTY", penalty), ("AGREEMENT_PRINCIPAL", principal)
            ) if amount
        )
    return ()


def _net_component(events, component: str, cutoff: datetime) -> Decimal:
    return _money(sum(
        (Decimal(event.amount) for event in events
         if event.component == component and _utc(event.occurred_at) <= cutoff),
        ZERO,
    ))


def _loan_penalty_as_of(events: list[LoanLateChargeEvent], cutoff: datetime, civil: date) -> Decimal:
    amounts = {
        "FIXED_PENALTY_ASSESSED": Decimal(1),
        "LATE_INTEREST_ACCRUED": Decimal(1),
        "LATE_INTEREST_ADJUSTMENT_INCREASE": Decimal(1),
        "LATE_INTEREST_ADJUSTMENT_DECREASE": Decimal(-1),
    }
    return _money(sum(
        (Decimal(event.amount or 0) * amounts[event.event_type]
         for event in events
         if event.event_type in amounts
         and event.effective_date <= civil
         and _stored_utc(event.created_at) <= cutoff),
        ZERO,
    ))


def _legacy_contribution_cash(db: Session, row: Contribution) -> CashEvidence | None:
    """Reuse B1's dated paid_at/approved Payment fallback without settlements."""
    current = _money(
        Decimal(row.paid_amount) if row.paid_amount is not None
        else (Decimal(row.amount) if row.status == "PAID" else ZERO)
    )
    if current == ZERO and row.status != "PAID":
        return None
    if current != _money(Decimal(row.amount)):
        raise ClosingContractGap(
            "LEGACY_CONTRIBUTION_PAYMENT_CUTOFF_UNPROVABLE", str(row.id)
        )
    if row.paid_at is not None:
        paid_at = _stored_utc(row.paid_at)
        source_id = f"contribution:{row.id}:legacy_paid_at"
    else:
        payment = db.get(Payment, row.payment_id) if row.payment_id is not None else None
        if not (
            payment is not None and payment.status == "approved"
            and payment.confirmed_at is not None
            and (payment.reference_type or "").upper() in ("", "CONTRIBUTION")
            and (not payment.reference_id or payment.reference_id == str(row.id))
            and _money(Decimal(
                payment.amount_received if payment.amount_received is not None
                else payment.amount
            )) == current
        ):
            raise ClosingContractGap(
                "LEGACY_CONTRIBUTION_PAYMENT_CUTOFF_UNPROVABLE", str(row.id)
            )
        paid_at = _stored_utc(payment.confirmed_at)
        source_id = f"payment:{payment.id}:legacy_contribution"
    if row.payment_id is not None and db.query(PaymentReversal).filter(
        PaymentReversal.payment_id == row.payment_id
    ).first() is not None:
        raise ClosingContractGap(
            "LEGACY_CONTRIBUTION_PAYMENT_CUTOFF_UNPROVABLE", str(row.id)
        )
    return CashEvidence(source_id, current, paid_at, "ORIGINAL")


FINANCIAL_PREVIEW_MODELS = (
    Cycle, CycleParticipation, Contribution, ContributionChargeEvent,
    Payment, PaymentSettlement, PaymentReversal, PaymentReversalComponent,
    CycleRealizedGainEvent,
    Loan, LoanInstallment, LoanLateChargeEvent,
    CollectionAgreement, AgreementInstallment,
    MemberFinancialAccount, MemberFinancialEntry, LedgerEntry,
)


def _reject_unpersisted_financial_state(db: Session) -> None:
    for row in db.new | db.deleted:
        if isinstance(row, FINANCIAL_PREVIEW_MODELS):
            raise ClosingContractGap(
                "UNPERSISTED_FINANCIAL_STATE", type(row).__name__
            )
    for row in db.dirty:
        if isinstance(row, FINANCIAL_PREVIEW_MODELS) and db.is_modified(
            row, include_collections=False
        ):
            raise ClosingContractGap(
                "UNPERSISTED_FINANCIAL_STATE", type(row).__name__
            )


def build_closing_evidence(
    db: Session, *, cycle_id: int, closing_cutoff_at: datetime,
) -> ClosingEvidence:
    """Read persisted evidence only. Never infer Loan-to-Cycle from dates."""
    cutoff = _utc(closing_cutoff_at)
    _reject_unpersisted_financial_state(db)
    civil = cutoff.astimezone(FINANCIAL_ZONE).date()
    with db.no_autoflush:
        cycle = db.get(Cycle, cycle_id)
        if cycle is None:
            raise ValueError("cycle not found")
        participation_rows = (
            db.query(CycleParticipation)
            .filter(CycleParticipation.cycle_id == cycle_id)
            .order_by(CycleParticipation.id)
            .all()
        )
        contribution_rows = (
            db.query(Contribution)
            .filter(Contribution.cycle_id == cycle_id)
            .order_by(Contribution.id)
            .all()
        )
        settlements = {row.id: row for row in db.query(PaymentSettlement).all()}
        events = payment_financial_events(db)
        contribution_by_id = {row.id: row for row in contribution_rows}
        cycle_member_ids = {row.member_id for row in participation_rows}
        represented_settlements = {event.settlement_id for event in events}
        for settlement in settlements.values():
            if settlement.member_id not in cycle_member_ids:
                continue
            if settlement.obligation_type == "CONTRIBUTION" and settlement.contribution_id not in contribution_by_id:
                continue
            if _stored_utc(settlement.confirmed_at) <= cutoff and settlement.id not in represented_settlements:
                raise ClosingContractGap("UNSUPPORTED_PAYMENT_EVIDENCE", str(settlement.id))
        cash_by_contribution: dict[int, list[CashEvidence]] = {
            row.id: [] for row in contribution_rows
        }
        settled_contribution_ids = {
            settlement.contribution_id for settlement in settlements.values()
            if settlement.obligation_type == "CONTRIBUTION"
        }
        payment_events_by_installment: dict[int, list] = {}
        payment_events_by_agreement_installment: dict[int, list] = {}
        gains: list[GainEvidence] = []
        gaps: list[tuple[str, str]] = []
        for event in events:
            settlement = settlements.get(event.settlement_id)
            if settlement is None or settlement.member_id not in cycle_member_ids:
                continue
            if settlement.obligation_type == "CONTRIBUTION" and settlement.contribution_id not in contribution_by_id:
                continue
            if settlement.contribution_id in cash_by_contribution and event.component == "CONTRIBUTION":
                cash_by_contribution[settlement.contribution_id].append(CashEvidence(
                    event_id=event.event_id, amount=Decimal(event.amount),
                    occurred_at=event.occurred_at, source=event.source,
                    settlement_id=event.settlement_id,
                ))
            if settlement.loan_installment_id is not None:
                payment_events_by_installment.setdefault(settlement.loan_installment_id, []).append(event)
            if settlement.agreement_installment_id is not None:
                payment_events_by_agreement_installment.setdefault(
                    settlement.agreement_installment_id, []
                ).append(event)
            linked_contribution = contribution_by_id.get(settlement.contribution_id)
            loan_cycle_id = None
            if settlement.loan_installment_id is not None:
                linked_installment = db.get(LoanInstallment, settlement.loan_installment_id)
                linked_loan = db.get(Loan, linked_installment.loan_id) if linked_installment is not None else None
                if linked_loan is not None and linked_loan.member_id == settlement.member_id:
                    loan_cycle_id = linked_loan.cycle_id
            elif settlement.agreement_installment_id is not None:
                linked_agreement_installment = db.get(AgreementInstallment, settlement.agreement_installment_id)
                linked_agreement = (
                    db.get(CollectionAgreement, linked_agreement_installment.agreement_id)
                    if linked_agreement_installment is not None else None
                )
                linked_loan = db.get(Loan, linked_agreement.loan_id) if linked_agreement is not None else None
                if (
                    linked_agreement is not None and linked_loan is not None
                    and linked_agreement.member_id == settlement.member_id
                    and linked_loan.member_id == settlement.member_id
                ):
                    loan_cycle_id = linked_loan.cycle_id
            gains.extend(_gain_from_event(
                event, settlement,
                linked_contribution.cycle_id if linked_contribution is not None else None,
                loan_cycle_id,
            ))
            if event.component == "AGREEMENT" and Decimal(settlement.principal_applied or 0):
                gaps.append(("AGREEMENT_BASE_COMPONENT_NOT_CLASSIFIED", str(settlement.id)))

        external_gain_rows = (
            db.query(CycleRealizedGainEvent)
            .filter(CycleRealizedGainEvent.cycle_id == cycle_id)
            .order_by(CycleRealizedGainEvent.id)
            .all()
        )
        external_kind = {
            "INVESTMENT_YIELD_REALIZED": "INVESTMENT_YIELD",
            "OTHER_REALIZED_GAIN": "OTHER_REALIZED_GAIN",
        }
        for row in external_gain_rows:
            if row.event_type not in external_kind:
                raise ClosingContractGap("UNTYPED_GAIN", str(row.id))
            amount = Decimal(row.amount)
            if row.reversal_of_id is not None:
                amount = -amount
            gains.append(GainEvidence(
                source_id=f"cycle_realized_gain_event:{row.id}",
                source_type="CYCLE_REALIZED_GAIN_EVENT",
                kind=external_kind[row.event_type],
                cycle_id=row.cycle_id,
                amount=amount,
                occurred_at=_stored_utc(row.realized_at),
            ))

        for row in contribution_rows:
            if row.id not in settled_contribution_ids:
                legacy_cash = _legacy_contribution_cash(db, row)
                if legacy_cash is not None:
                    cash_by_contribution[row.id].append(legacy_cash)

        participations = tuple(ParticipationEvidence(
            id=row.id, member_id=row.member_id, status=row.status,
            blocked_at=row.blocked_at, voluntary_exit_at=row.voluntary_exit_at,
        ) for row in participation_rows)
        contributions = tuple(ContributionEvidence(
            id=row.id, member_id=row.member_id, cycle_id=row.cycle_id,
            amount=Decimal(row.amount), status=row.status, cancelled_at=row.cancelled_at,
            events=tuple(cash_by_contribution[row.id]),
        ) for row in contribution_rows)
        obligations: list[ObligationEvidence] = []
        charge_rows = (
            db.query(ContributionChargeEvent)
            .filter(ContributionChargeEvent.contribution_id.in_(list(contribution_by_id)))
            .order_by(ContributionChargeEvent.contribution_id,
                      ContributionChargeEvent.accrued_through, ContributionChargeEvent.id)
            .all()
        ) if contribution_by_id else []
        charges_by_contribution: dict[int, list[ContributionChargeEvent]] = {}
        for charge in charge_rows:
            if charge.accrued_through <= civil and _stored_utc(charge.created_at) <= cutoff:
                charges_by_contribution.setdefault(charge.contribution_id, []).append(charge)
        participation_by_member = {row.member_id: row for row in participation_rows}
        for row in contribution_rows:
            cash = cash_by_contribution[row.id]
            paid_as_of = _money(sum(
                (item.amount for item in cash if _utc(item.occurred_at) <= cutoff), ZERO
            ))
            if paid_as_of < ZERO or paid_as_of > Decimal(row.amount):
                raise ClosingContractGap("CONTRIBUTION_EVIDENCE_MISMATCH", str(row.id))
            cancelled = row.cancelled_at is not None and _stored_utc(row.cancelled_at) <= cutoff
            if row.due_date is not None and row.due_date <= civil and not cancelled:
                principal_open = _money(max(ZERO, Decimal(row.amount) - paid_as_of))
                if principal_open:
                    obligations.append(ObligationEvidence(
                        source_id=f"contribution:{row.id}:principal", member_id=row.member_id,
                        kind="CONTRIBUTION_PRINCIPAL", amount=principal_open,
                        due_date=row.due_date,
                    ))
            charges = charges_by_contribution.get(row.id, [])
            participation = participation_by_member.get(row.member_id)
            blocked = (
                participation is not None
                and participation.status == "BLOCKED_DELINQUENCY"
                and participation.blocked_at is not None
                and _stored_utc(participation.blocked_at) <= cutoff
            )
            selected = next(
                (item for item in reversed(charges)
                 if item.event_type == ("BLOCK_FREEZE" if blocked else "ACCRUAL_SNAPSHOT")),
                None,
            )
            if selected is not None:
                charge_due = _money(Decimal(selected.fixed_penalty) + Decimal(selected.daily_interest))
                if charge_due:
                    obligations.append(ObligationEvidence(
                        source_id=f"contribution_charge:{selected.id}",
                        member_id=row.member_id, kind="CONTRIBUTION_CHARGE",
                        amount=charge_due, due_date=selected.accrued_through,
                    ))
            if blocked and cancelled and selected is None and paid_as_of < Decimal(row.amount):
                raise ClosingContractGap("BLOCK_FREEZE_MISSING", str(row.id))

        member_ids = [row.member_id for row in participation_rows]
        loans = db.query(Loan).filter(Loan.member_id.in_(member_ids)).all() if member_ids else []
        loan_by_id = {row.id: row for row in loans}
        installments = (
            db.query(LoanInstallment)
            .filter(LoanInstallment.loan_id.in_(list(loan_by_id)))
            .order_by(LoanInstallment.id)
            .all()
        ) if loan_by_id else []
        late_events = (
            db.query(LoanLateChargeEvent)
            .filter(LoanLateChargeEvent.loan_installment_id.in_([row.id for row in installments]))
            .all()
        ) if installments else []
        late_by_installment: dict[int, list[LoanLateChargeEvent]] = {}
        for event in late_events:
            late_by_installment.setdefault(event.loan_installment_id, []).append(event)
        agreements = (
            db.query(CollectionAgreement)
            .filter(CollectionAgreement.member_id.in_(member_ids))
            .order_by(CollectionAgreement.id)
            .all()
        ) if member_ids else []
        applicable = [
            agreement for agreement in agreements
            if agreement.status in {"APPROVED", "SETTLED"}
            and agreement.decided_at is not None
            and _stored_utc(agreement.decided_at) <= cutoff
        ]
        agreement_installments = (
            db.query(AgreementInstallment)
            .filter(AgreementInstallment.agreement_id.in_([row.id for row in applicable]))
            .order_by(AgreementInstallment.agreement_id, AgreementInstallment.number)
            .all()
        ) if applicable else []
        replacements_by_agreement: dict[int, list[AgreementInstallment]] = {}
        for installment in agreement_installments:
            replacements_by_agreement.setdefault(installment.agreement_id, []).append(installment)
        loan_installments: dict[int, list[LoanInstallment]] = {}
        for installment in installments:
            loan_installments.setdefault(installment.loan_id, []).append(installment)
        agreement_by_loan: dict[int, int] = {}
        for agreement in applicable:
            loan = loan_by_id.get(agreement.loan_id)
            old = loan_installments.get(agreement.loan_id, [])
            replacements = replacements_by_agreement.get(agreement.id, [])
            old_open = _money(sum((
                Decimal(item.amount) - Decimal(item.paid_amount or 0)
                + Decimal(item.penalty_amount or 0) - Decimal(item.paid_penalty_amount or 0)
                for item in old if item.status != "PAID"
            ), ZERO))
            replacement_total = _money(sum((Decimal(item.amount) for item in replacements), ZERO))
            structurally_valid = (
                loan is not None and loan.member_id == agreement.member_id
                and loan.status == "RESTRUCTURED"
                and old and any(item.status == "AGREED" for item in old)
                and all(item.status in {"PAID", "AGREED"} for item in old)
                and len(replacements) == agreement.installments
                and sorted(item.number for item in replacements)
                    == list(range(1, agreement.installments + 1))
                and all(_money(Decimal(item.amount))
                        == _money(Decimal(item.principal) + Decimal(item.penalty_amount or 0))
                        for item in replacements)
                and all(
                    item.due_date > _stored_utc(agreement.decided_at)
                        .astimezone(FINANCIAL_ZONE).date()
                    for item in replacements
                )
                and abs(replacement_total - _money(Decimal(agreement.total_amount))) <= CENT
                and old_open == replacement_total
                and agreement.loan_id not in agreement_by_loan
            )
            if not structurally_valid:
                raise ClosingContractGap(
                    "AGREEMENT_RESTRUCTURE_INTEGRITY_GAP", str(agreement.id)
                )
            agreement_by_loan[agreement.loan_id] = agreement.id
        superseded_loan_ids = tuple(sorted(agreement_by_loan))
        own_balance_rows = (
            db.query(MemberFinancialEntry)
            .filter(
                MemberFinancialEntry.reference_type.in_((
                    "OWN_BALANCE_SETTLEMENT", "OWN_BALANCE_RENEGOTIATION"
                )),
                MemberFinancialEntry.reference_id.in_([str(key) for key in loan_by_id]),
            )
            .order_by(MemberFinancialEntry.id)
            .all()
        ) if loan_by_id else []
        own_balance_by_loan: dict[int, list[MemberFinancialEntry]] = {}
        for entry in own_balance_rows:
            if (
                entry.reference_id is None or not entry.reference_id.isdigit()
                or int(entry.reference_id) not in loan_by_id
                or entry.entry_type != entry.reference_type
                or entry.direction != "DEBIT"
            ):
                raise ClosingContractGap(
                    "OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP", str(entry.id)
                )
            loan = loan_by_id[int(entry.reference_id)]
            account = db.get(MemberFinancialAccount, entry.account_id)
            if account is None or account.member_id != loan.member_id:
                raise ClosingContractGap(
                    "OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP", str(entry.id)
                )
            own_balance_by_loan.setdefault(loan.id, []).append(entry)
        settled_with_own_balance: set[int] = set()
        for loan in loans:
            entries = own_balance_by_loan.get(loan.id, [])
            recorded = _money(sum((Decimal(item.amount) for item in entries), ZERO))
            projected = _money(Decimal(loan.principal_settled_with_own_balance or 0))
            if recorded != projected:
                raise ClosingContractGap(
                    "OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP", str(loan.id)
                )
            through = [item for item in entries if _stored_utc(item.created_at) <= cutoff]
            if any(item.entry_type == "OWN_BALANCE_RENEGOTIATION" for item in through):
                raise ClosingContractGap("OWN_BALANCE_PARTIAL_ALLOCATION", str(loan.id))
            settlement_amount = _money(sum((
                Decimal(item.amount) for item in through
                if item.entry_type == "OWN_BALANCE_SETTLEMENT"
            ), ZERO))
            if settlement_amount:
                if (
                    loan.status != "PAID" or loan.paid_at is None
                    or _stored_utc(loan.paid_at) > cutoff
                    or settlement_amount != projected
                    or not loan_installments.get(loan.id)
                    or any(
                        item.status != "PAID" or item.paid_at is None
                        or _stored_utc(item.paid_at) != _stored_utc(loan.paid_at)
                        for item in loan_installments[loan.id]
                    )
                    or _money(settlement_amount + sum((
                        _net_component(
                            payment_events_by_installment.get(item.id, []),
                            "LOAN_PRINCIPAL", _stored_utc(loan.paid_at)
                        ) for item in loan_installments[loan.id]
                    ), ZERO)) != _money(Decimal(loan.principal))
                ):
                    raise ClosingContractGap(
                        "OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP", str(loan.id)
                    )
                settled_with_own_balance.add(loan.id)
            elif projected and loan.paid_at is not None and _stored_utc(loan.paid_at) <= cutoff:
                raise ClosingContractGap(
                    "OWN_BALANCE_SETTLEMENT_RECONCILIATION_GAP", str(loan.id)
                )
        for installment in installments:
            if installment.loan_id in superseded_loan_ids or installment.loan_id in settled_with_own_balance:
                continue
            loan = loan_by_id[installment.loan_id]
            if installment.due_date > civil:
                continue
            payment_events = payment_events_by_installment.get(installment.id, [])
            base_paid = _net_component(payment_events, "LOAN_PRINCIPAL", cutoff)
            base_paid += _net_component(payment_events, "LOAN_INTEREST", cutoff)
            penalty_paid = _net_component(payment_events, "LOAN_PENALTY", cutoff)
            if installment.late_charge_version is None:
                penalty_assessed = _money(Decimal(installment.penalty_amount or 0))
                if penalty_assessed and (installment.last_penalty_date is None
                                         or installment.last_penalty_date >= civil):
                    raise ClosingContractGap(
                        "LEGACY_PENALTY_CUTOFF_UNPROVABLE", str(installment.id)
                    )
            else:
                penalty_assessed = _loan_penalty_as_of(
                    late_by_installment.get(installment.id, []), cutoff, civil
                )
            open_amount = _money(
                Decimal(installment.amount) - base_paid + penalty_assessed - penalty_paid
            )
            if open_amount < ZERO:
                raise ClosingContractGap("LOAN_OBLIGATION_EVIDENCE_MISMATCH", str(installment.id))
            if open_amount:
                obligations.append(ObligationEvidence(
                    source_id=f"loan_installment:{installment.id}",
                    member_id=loan_by_id[installment.loan_id].member_id,
                    kind="LOAN_INSTALLMENT", amount=open_amount,
                    due_date=installment.due_date, loan_id=installment.loan_id,
                ))

        agreement_by_id = {row.id: row for row in applicable}
        for installment in agreement_installments:
            if installment.due_date > civil:
                continue
            payment_events = payment_events_by_agreement_installment.get(installment.id, [])
            paid_as_of = _net_component(payment_events, "AGREEMENT", cutoff)
            open_amount = _money(
                Decimal(installment.principal) + Decimal(installment.penalty_amount or 0)
                - paid_as_of
            )
            if open_amount < ZERO:
                raise ClosingContractGap("AGREEMENT_OBLIGATION_EVIDENCE_MISMATCH", str(installment.id))
            if open_amount:
                agreement = agreement_by_id[installment.agreement_id]
                obligations.append(ObligationEvidence(
                    source_id=f"agreement_installment:{installment.id}",
                    member_id=agreement.member_id, kind="AGREEMENT_INSTALLMENT",
                    amount=open_amount, due_date=installment.due_date,
                    loan_id=agreement.loan_id, agreement_id=agreement.id,
                ))
        return ClosingEvidence(
            cycle_id=cycle.id, cycle_start_date=cycle.start_date,
            closing_cutoff_at=cutoff, participations=participations,
            contributions=contributions, gains=tuple(gains),
            obligations=tuple(obligations),
            superseded_loan_ids=superseded_loan_ids,
            own_balance_settled_loan_ids=tuple(sorted(settled_with_own_balance)),
            source_gaps=tuple(gaps),
        )


def preview_cycle_closing(
    db: Session, *, cycle_id: int, closing_cutoff_at: datetime,
) -> dict:
    """Produce a deterministic preview using SELECT only."""
    with db.no_autoflush:
        evidence = build_closing_evidence(
            db, cycle_id=cycle_id, closing_cutoff_at=closing_cutoff_at
        )
        return calculate_cycle_closing(evidence)
