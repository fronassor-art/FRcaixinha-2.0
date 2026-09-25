from datetime import datetime, date, timezone
from decimal import Decimal
from sqlalchemy import String, Integer, Boolean, DateTime, Date, Numeric, ForeignKey, ForeignKeyConstraint, Text, UniqueConstraint, PrimaryKeyConstraint, Index, CheckConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import event, inspect
from app.db.base import Base
from app.core.loan_rules import (
    LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION,
    LATE_CHARGE_VERSION,
    MAX_LOAN_INSTALLMENTS,
)

def now_utc():
    return datetime.now(timezone.utc)


CYCLE_EXTERNAL_GAIN_SOURCE_TYPES = ("BANK_STATEMENT", "BANK_CORRECTION")
_CYCLE_EXTERNAL_GAIN_SOURCE_TYPES_SQL = ", ".join(
    f"'{source_type}'" for source_type in CYCLE_EXTERNAL_GAIN_SOURCE_TYPES
)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    cpf: Mapped[str] = mapped_column(String(14), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(Text())
    role: Mapped[str] = mapped_column(String(20), default="USER")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    member: Mapped["Member | None"] = relationship(back_populates="user", uselist=False)
    payment_reversals: Mapped[list["PaymentReversal"]] = relationship(back_populates="admin")
    __table_args__ = (
        Index(
            "uq_users_one_active_master",
            "is_master",
            unique=True,
            postgresql_where=text("is_master = true AND is_active = true AND role = 'ADMIN'"),
            sqlite_where=text("is_master = 1 AND is_active = 1 AND role = 'ADMIN'"),
        ),
        CheckConstraint(
            "is_master = false OR (role = 'ADMIN' AND is_active = true)",
            name="ck_users_master_requires_active_admin",
        ),
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))

class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class UserSecurity(Base):
    __tablename__ = "user_security"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    totp_secret: Mapped[str | None] = mapped_column(Text())
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_codes: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class TrustedDevice(Base):
    __tablename__ = "trusted_devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(120))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class SecurityEvent(Base):
    __tablename__ = "security_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="INFO")
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    monthly_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("150.00"))
    months: Mapped[int] = mapped_column(Integer, default=12)
    due_day: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    min_cash_reserve: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0.00"))
    max_member_exposure: Mapped[Decimal | None] = mapped_column(Numeric(14,2), nullable=True)
    max_global_exposure: Mapped[Decimal | None] = mapped_column(Numeric(14,2), nullable=True)
    max_exposure_ratio: Mapped[Decimal | None] = mapped_column(Numeric(8,5), nullable=True)
    max_simultaneous_loans: Mapped[int] = mapped_column(Integer, default=1)
    max_installments: Mapped[int] = mapped_column(Integer, default=MAX_LOAN_INSTALLMENTS)
    grace_days: Mapped[int] = mapped_column(Integer, default=0)
    min_on_time_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6,5), nullable=True)
    max_overdue_installments: Mapped[int] = mapped_column(Integer, default=0)
    max_installment_income_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6,5), nullable=True)
    max_quota_multiple: Mapped[Decimal | None] = mapped_column(Numeric(10,2), nullable=True)
    # v0.48: explicit per-loan limits used by the unified approval engine.
    max_loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(14,2), nullable=True)
    max_loan_income_multiple: Mapped[Decimal | None] = mapped_column(Numeric(8,3), nullable=True)
    __table_args__ = (
        CheckConstraint(f"max_installments >= 1 AND max_installments <= {MAX_LOAN_INSTALLMENTS}", name="ck_groups_max_installments_1_6"),
    )

class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    declared_monthly_income: Mapped[Decimal | None] = mapped_column(Numeric(14,2), nullable=True)
    user: Mapped[User] = relationship(back_populates="member")
    quotas: Mapped[list["Quota"]] = relationship(back_populates="member")

    @property
    def quota(self):
        return self.quotas[0] if self.quotas else None

    financial_account: Mapped["MemberFinancialAccount | None"] = relationship(back_populates="member", uselist=False)


class MemberPayoutDestination(Base):
    """One immutable PIX key version; lifecycle changes are limited to revocation."""

    __tablename__ = "member_payout_destinations"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", name="fk_mpd_member", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    key_type: Mapped[str] = mapped_column(String(10), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text(), nullable=False)
    masked_value: Mapped[str] = mapped_column(String(255), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNVERIFIED",
        server_default=text("'UNVERIFIED'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc,
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_mpd_created_by", ondelete="RESTRICT"),
        nullable=False,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_mpd_verified_by", ondelete="RESTRICT"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_mpd_revoked_by", ondelete="RESTRICT"),
    )

    __table_args__ = (
        UniqueConstraint("member_id", "version", name="uq_mpd_member_version"),
        Index(
            "uq_mpd_one_active_member", "member_id", unique=True,
            postgresql_where=text("verification_status <> 'REVOKED'"),
            sqlite_where=text("verification_status <> 'REVOKED'"),
        ),
        CheckConstraint("version >= 1", name="ck_mpd_version_positive"),
        CheckConstraint(
            "key_type IN ('CPF', 'PHONE', 'EMAIL', 'EVP')",
            name="ck_mpd_key_type",
        ),
        CheckConstraint(
            "verification_status IN ('UNVERIFIED', 'VERIFIED', 'REVOKED')",
            name="ck_mpd_verification_status",
        ),
        CheckConstraint(
            "(verification_status = 'UNVERIFIED' AND verified_at IS NULL AND verified_by IS NULL "
            "AND revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(verification_status = 'VERIFIED' AND verified_at IS NOT NULL AND verified_by IS NOT NULL "
            "AND revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(verification_status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL "
            "AND ((verified_at IS NULL AND verified_by IS NULL) OR "
            "(verified_at IS NOT NULL AND verified_by IS NOT NULL)))",
            name="ck_mpd_lifecycle_fields",
        ),
    )

class Cycle(Base):
    __tablename__ = "cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    entry_deadline: Mapped[date] = mapped_column(Date)
    closing_reference_date: Mapped[date] = mapped_column(Date)
    monthly_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    months: Mapped[int] = mapped_column(Integer)
    max_quotas: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    quotas: Mapped[list["Quota"]] = relationship(back_populates="cycle")
    contributions: Mapped[list["Contribution"]] = relationship(back_populates="cycle")
    participations: Mapped[list["CycleParticipation"]] = relationship(back_populates="cycle")
    __table_args__ = (
        CheckConstraint("entry_deadline >= start_date", name="ck_cycles_entry_deadline_after_start"),
        CheckConstraint("closing_reference_date >= start_date", name="ck_cycles_closing_after_start"),
        CheckConstraint("monthly_amount > 0 AND months >= 1 AND max_quotas >= 1", name="ck_cycles_positive_terms"),
    )


class Quota(Base):
    __tablename__ = "quotas"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), nullable=True, index=True)
    units: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("1"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    member: Mapped[Member] = relationship(back_populates="quotas")
    cycle: Mapped["Cycle | None"] = relationship(back_populates="quotas")
    __table_args__ = (
        CheckConstraint("cycle_id IS NULL OR (units >= 1 AND units = CAST(units AS INTEGER))", name="ck_quotas_new_units_discrete"),
    )

class CycleParticipation(Base):
    __tablename__ = "cycle_participations"
    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id", name="fk_cycle_participations_cycle"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", name="fk_cycle_participations_member"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voluntary_exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    block_reason: Mapped[str | None] = mapped_column(String(80))
    cycle: Mapped[Cycle] = relationship(back_populates="participations")
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_cycle_participations"),
        UniqueConstraint("member_id", "cycle_id", name="uq_cycle_participations_member_cycle"),
        CheckConstraint("(status = 'ACTIVE' AND voluntary_exit_at IS NULL AND blocked_at IS NULL AND block_reason IS NULL) OR (status = 'VOLUNTARILY_EXITED' AND voluntary_exit_at IS NOT NULL AND blocked_at IS NULL AND block_reason IS NULL) OR (status = 'BLOCKED_DELINQUENCY' AND voluntary_exit_at IS NULL AND blocked_at IS NOT NULL AND block_reason IS NOT NULL)", name="ck_cycle_participations_state"),
        Index("ix_cycle_participations_cycle_status", "cycle_id", "status"),
    )

class Contribution(Base):
    __tablename__ = "contributions"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), nullable=True, index=True)
    competence: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))
    pix_idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14,2))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    cycle: Mapped["Cycle | None"] = relationship(back_populates="contributions")
    __table_args__ = (
        Index("uq_contribution_legacy_member_competence", "member_id", "competence", unique=True, sqlite_where=text("cycle_id IS NULL"), postgresql_where=text("cycle_id IS NULL")),
        Index("uq_contribution_member_cycle_competence", "member_id", "cycle_id", "competence", unique=True, sqlite_where=text("cycle_id IS NOT NULL"), postgresql_where=text("cycle_id IS NOT NULL")),
        Index("ix_contributions_status_due_date", "status", "due_date"),
        Index("ix_contributions_member_status_due_date", "member_id", "status", "due_date"),
    )

class ContributionChargeEvent(Base):
    __tablename__ = "contribution_charge_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    contribution_id: Mapped[int] = mapped_column(ForeignKey("contributions.id", name="fk_contribution_charge_events_contribution"), nullable=False)
    participation_id: Mapped[int] = mapped_column(ForeignKey("cycle_participations.id", name="fk_contribution_charge_events_participation"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    accrued_through: Mapped[date] = mapped_column(Date, nullable=False)
    fixed_penalty: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    daily_interest: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cancelled_principal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_contribution_charge_events"),
        UniqueConstraint("contribution_id", "event_type", "accrued_through", name="uq_contribution_charge_events_snapshot"),
        CheckConstraint("event_type IN ('ACCRUAL_SNAPSHOT', 'BLOCK_FREEZE')", name="ck_contribution_charge_events_type"),
        CheckConstraint("fixed_penalty >= 0 AND daily_interest >= 0 AND cancelled_principal >= 0", name="ck_contribution_charge_events_nonnegative"),
        Index("ix_contribution_charge_events_participation", "participation_id"),
    )

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_order_id: Mapped[str | None] = mapped_column(String(150), index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    raw_status: Mapped[str | None] = mapped_column(String(80))
    qr_code: Mapped[str | None] = mapped_column(Text())
    qr_code_base64: Mapped[str | None] = mapped_column(Text())
    ticket_url: Mapped[str | None] = mapped_column(Text())
    external_reference: Mapped[str | None] = mapped_column(String(150), index=True)
    pix_txid: Mapped[str | None] = mapped_column(String(100))
    end_to_end_id: Mapped[str | None] = mapped_column(String(100))
    provider_status_detail: Mapped[str | None] = mapped_column(String(150))
    provider_payload_json: Mapped[str | None] = mapped_column(Text())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    amount_received: Mapped[Decimal | None] = mapped_column(Numeric(14,2))
    ledger_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_type: Mapped[str | None] = mapped_column(String(50), index=True)
    reference_id: Mapped[str | None] = mapped_column(String(80), index=True)
    attempt_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    calculated_for_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    financial_snapshot_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reconciliation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    payment_reversal: Mapped["PaymentReversal | None"] = relationship(back_populates="payment", uselist=False)
    __table_args__ = (
        CheckConstraint(
            "provider_payment_id IS NOT NULL OR (reference_type = 'LOAN_INSTALLMENT' "
            "AND reference_id IS NOT NULL AND attempt_status IS NOT NULL "
            "AND idempotency_key IS NOT NULL AND calculated_for_date IS NOT NULL "
            "AND financial_snapshot_json IS NOT NULL AND snapshot_hash IS NOT NULL "
            "AND expires_at IS NOT NULL)",
            name="ck_payments_provider_id_or_versioned_loan_attempt",
        ),
        UniqueConstraint("provider", "provider_payment_id", name="uq_provider_payment"),
        Index("uq_payments_provider_pix_txid", "provider", "pix_txid", unique=True),
        Index("uq_payments_provider_end_to_end_id", "provider", "end_to_end_id", unique=True),
        Index("ix_payments_status_expires_at", "status", "expires_at"),
        Index(
            "uq_payments_reference_pending",
            "reference_type",
            "reference_id",
            unique=True,
            postgresql_where=text("attempt_status = 'PENDING'"),
            sqlite_where=text("attempt_status = 'PENDING'"),
        ),
    )

class PaymentSettlement(Base):
    __tablename__ = "payment_settlements"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    obligation_type: Mapped[str] = mapped_column(String(30))
    contribution_id: Mapped[int | None] = mapped_column(ForeignKey("contributions.id"))
    loan_installment_id: Mapped[int | None] = mapped_column(ForeignKey("loan_installments.id"))
    agreement_installment_id: Mapped[int | None] = mapped_column(ForeignKey("agreement_installments.id"))
    amount_received: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0.00"))
    amount_applied: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0.00"))
    principal_applied: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0.00"))
    interest_applied: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0.00"))
    penalty_applied: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0.00"))
    settlement_component_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    normal_interest_applied: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    late_interest_applied: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fixed_penalty_applied: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    excess_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0.00"))
    obligation_status_before: Mapped[str] = mapped_column(String(20))
    obligation_status_after: Mapped[str] = mapped_column(String(20))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmation_source: Mapped[str] = mapped_column(String(30))
    webhook_event_id: Mapped[int | None] = mapped_column(ForeignKey("webhook_events.id"))
    receipt_number: Mapped[str] = mapped_column(String(80), unique=True)
    receipt_version: Mapped[str] = mapped_column(String(20))
    receipt_snapshot_json: Mapped[str] = mapped_column(Text())
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    loan_status_before: Mapped[str | None] = mapped_column(String(30))
    loan_status_after: Mapped[str | None] = mapped_column(String(30))
    loan_state_revision_before: Mapped[int | None] = mapped_column(Integer)
    loan_state_revision_after: Mapped[int | None] = mapped_column(Integer)
    loan_paid_at_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loan_paid_at_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loan_installment_status_before: Mapped[str | None] = mapped_column(String(20))
    loan_installment_status_after: Mapped[str | None] = mapped_column(String(20))
    loan_installment_paid_at_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loan_installment_paid_at_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agreement_installment_status_before: Mapped[str | None] = mapped_column(String(20))
    agreement_installment_status_after: Mapped[str | None] = mapped_column(String(20))
    agreement_installment_paid_at_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agreement_installment_paid_at_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agreement_installment_paid_amount_before: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    agreement_installment_paid_amount_after: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    agreement_installment_paid_penalty_amount_before: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    agreement_installment_paid_penalty_amount_after: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    collection_agreement_status_before: Mapped[str | None] = mapped_column(String(20))
    collection_agreement_status_after: Mapped[str | None] = mapped_column(String(20))
    collection_agreement_state_revision_before: Mapped[int | None] = mapped_column(Integer)
    collection_agreement_state_revision_after: Mapped[int | None] = mapped_column(Integer)
    payment_reversal: Mapped["PaymentReversal | None"] = relationship(back_populates="settlement", uselist=False)
    __table_args__ = (
        CheckConstraint("amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0", name="ck_payment_settlements_nonnegative_amounts"),
        CheckConstraint("amount_received = amount_applied + excess_amount", name="ck_payment_settlements_received_allocation"),
        CheckConstraint("amount_applied = principal_applied + interest_applied + penalty_applied", name="ck_payment_settlements_applied_components"),
        CheckConstraint(
            f"settlement_component_version IS NULL OR settlement_component_version = '{LATE_CHARGE_SETTLEMENT_COMPONENT_VERSION}'",
            name="ck_payment_settlements_component_version",
        ),
        CheckConstraint(
            "(settlement_component_version IS NULL AND normal_interest_applied IS NULL AND "
            "late_interest_applied IS NULL AND fixed_penalty_applied IS NULL) OR "
            "(settlement_component_version IS NOT NULL AND normal_interest_applied IS NOT NULL AND "
            "late_interest_applied IS NOT NULL AND fixed_penalty_applied IS NOT NULL)",
            name="ck_payment_settlements_component_presence",
        ),
        CheckConstraint(
            "normal_interest_applied IS NULL OR normal_interest_applied >= 0",
            name="ck_payment_settlements_normal_interest_nonnegative",
        ),
        CheckConstraint(
            "late_interest_applied IS NULL OR late_interest_applied >= 0",
            name="ck_payment_settlements_late_interest_nonnegative",
        ),
        CheckConstraint(
            "fixed_penalty_applied IS NULL OR fixed_penalty_applied >= 0",
            name="ck_payment_settlements_fixed_penalty_nonnegative",
        ),
        CheckConstraint(
            "settlement_component_version IS NULL OR obligation_type = 'LOAN_INSTALLMENT'",
            name="ck_payment_settlements_components_loan_only",
        ),
        CheckConstraint(
            "settlement_component_version IS NULL OR "
            "(interest_applied = normal_interest_applied AND "
            "penalty_applied = late_interest_applied + fixed_penalty_applied)",
            name="ck_payment_settlements_component_aggregates",
        ),
        CheckConstraint("(obligation_type = 'CONTRIBUTION' AND contribution_id IS NOT NULL AND loan_installment_id IS NULL AND agreement_installment_id IS NULL) OR (obligation_type = 'LOAN_INSTALLMENT' AND contribution_id IS NULL AND loan_installment_id IS NOT NULL AND agreement_installment_id IS NULL) OR (obligation_type = 'AGREEMENT_INSTALLMENT' AND contribution_id IS NULL AND loan_installment_id IS NULL AND agreement_installment_id IS NOT NULL)", name="ck_payment_settlements_single_obligation"),
        CheckConstraint("obligation_status_before IN ('OPEN', 'PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_before"),
        CheckConstraint("obligation_status_after IN ('OPEN', 'PENDING', 'PARTIAL', 'OVERDUE', 'PAID')", name="ck_payment_settlements_status_after"),
        CheckConstraint("obligation_type != 'AGREEMENT_INSTALLMENT' OR interest_applied = 0", name="ck_payment_settlements_agreement_no_interest"),
        CheckConstraint("receipt_version IN ('v1', 'v2', 'v3', 'v4', 'v5')", name="ck_payment_settlements_receipt_version"),
        CheckConstraint("receipt_version IN ('v3', 'v4') OR (loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL)", name="ck_payment_settlements_paid_at_version"),
        CheckConstraint("receipt_version NOT IN ('v3', 'v4') OR obligation_type = 'LOAN_INSTALLMENT'", name="ck_payment_settlements_v3_loan_only"),
        CheckConstraint("receipt_version = 'v4' OR (loan_installment_status_before IS NULL AND loan_installment_status_after IS NULL AND loan_installment_paid_at_before IS NULL AND loan_installment_paid_at_after IS NULL)", name="ck_payment_settlements_installment_state_version"),
        CheckConstraint("receipt_version != 'v4' OR (loan_installment_status_before IS NOT NULL AND loan_installment_status_after IS NOT NULL)", name="ck_payment_settlements_v4_installment_state_complete"),
        CheckConstraint("(loan_status_before IS NULL AND loan_status_after IS NULL AND loan_state_revision_before IS NULL AND loan_state_revision_after IS NULL) OR (loan_status_before IS NOT NULL AND loan_status_after IS NOT NULL AND loan_state_revision_before IS NOT NULL AND loan_state_revision_after IS NOT NULL)", name="ck_payment_settlements_loan_evidence_complete"),
        CheckConstraint("loan_status_before IS NULL OR loan_status_before IN ('REQUESTED', 'APPROVED', 'REJECTED', 'ACTIVE', 'OVERDUE', 'IN_COLLECTION', 'PAID', 'RESTRUCTURED')", name="ck_payment_settlements_loan_status_before"),
        CheckConstraint("loan_status_after IS NULL OR loan_status_after IN ('REQUESTED', 'APPROVED', 'REJECTED', 'ACTIVE', 'OVERDUE', 'IN_COLLECTION', 'PAID', 'RESTRUCTURED')", name="ck_payment_settlements_loan_status_after"),
        CheckConstraint("loan_state_revision_before IS NULL OR loan_state_revision_before >= 0", name="ck_payment_settlements_loan_revision_before"),
        CheckConstraint("loan_state_revision_after IS NULL OR loan_state_revision_after >= 0", name="ck_payment_settlements_loan_revision_after"),
        CheckConstraint("loan_state_revision_before IS NULL OR loan_state_revision_after > loan_state_revision_before", name="ck_payment_settlements_loan_revision_order"),
        CheckConstraint("receipt_version != 'v5' OR obligation_type = 'AGREEMENT_INSTALLMENT'", name="ck_payment_settlements_v5_agreement_only"),
        CheckConstraint("receipt_version = 'v5' OR (agreement_installment_status_before IS NULL AND agreement_installment_status_after IS NULL AND agreement_installment_paid_at_before IS NULL AND agreement_installment_paid_at_after IS NULL AND agreement_installment_paid_amount_before IS NULL AND agreement_installment_paid_amount_after IS NULL AND agreement_installment_paid_penalty_amount_before IS NULL AND agreement_installment_paid_penalty_amount_after IS NULL AND collection_agreement_status_before IS NULL AND collection_agreement_status_after IS NULL AND collection_agreement_state_revision_before IS NULL AND collection_agreement_state_revision_after IS NULL)", name="ck_payment_settlements_agreement_evidence_version"),
        CheckConstraint("receipt_version != 'v5' OR (agreement_installment_status_before IS NOT NULL AND agreement_installment_status_after IS NOT NULL AND agreement_installment_paid_amount_before IS NOT NULL AND agreement_installment_paid_amount_after IS NOT NULL AND agreement_installment_paid_penalty_amount_before IS NOT NULL AND agreement_installment_paid_penalty_amount_after IS NOT NULL AND collection_agreement_status_before IS NOT NULL AND collection_agreement_status_after IS NOT NULL AND collection_agreement_state_revision_before IS NOT NULL AND collection_agreement_state_revision_after IS NOT NULL)", name="ck_payment_settlements_v5_agreement_evidence_complete"),
        CheckConstraint("receipt_version != 'v5' OR (loan_status_before IS NULL AND loan_status_after IS NULL AND loan_state_revision_before IS NULL AND loan_state_revision_after IS NULL AND loan_paid_at_before IS NULL AND loan_paid_at_after IS NULL AND loan_installment_status_before IS NULL AND loan_installment_status_after IS NULL AND loan_installment_paid_at_before IS NULL AND loan_installment_paid_at_after IS NULL)", name="ck_payment_settlements_v5_no_loan_evidence"),
        CheckConstraint("receipt_version != 'v5' OR collection_agreement_state_revision_after = collection_agreement_state_revision_before + 1", name="ck_payment_settlements_v5_agreement_revision_order"),
        CheckConstraint("receipt_version != 'v5' OR (agreement_installment_paid_amount_before >= 0 AND agreement_installment_paid_amount_after >= 0 AND agreement_installment_paid_penalty_amount_before >= 0 AND agreement_installment_paid_penalty_amount_after >= 0)", name="ck_payment_settlements_v5_agreement_amounts_nonnegative"),
        CheckConstraint("receipt_version != 'v5' OR (agreement_installment_status_before IN ('OPEN', 'PARTIAL', 'PAID') AND agreement_installment_status_after IN ('OPEN', 'PARTIAL', 'PAID'))", name="ck_payment_settlements_v5_installment_statuses"),
        CheckConstraint("receipt_version != 'v5' OR (collection_agreement_status_before IN ('APPROVED', 'SETTLED') AND collection_agreement_status_after IN ('APPROVED', 'SETTLED'))", name="ck_payment_settlements_v5_agreement_statuses"),
        CheckConstraint("receipt_version != 'v5' OR agreement_installment_paid_amount_after = agreement_installment_paid_amount_before + principal_applied", name="ck_payment_settlements_v5_paid_amount_equation"),
        CheckConstraint("receipt_version != 'v5' OR agreement_installment_paid_penalty_amount_after = agreement_installment_paid_penalty_amount_before + penalty_applied", name="ck_payment_settlements_v5_paid_penalty_equation"),
        Index("ix_payment_settlements_member_confirmed", "member_id", "confirmed_at"),
        Index("ix_payment_settlements_contribution_id", "contribution_id"),
        Index("ix_payment_settlements_loan_installment_id", "loan_installment_id"),
        Index("ix_payment_settlements_agreement_installment_id", "agreement_installment_id"),
        Index("ix_payment_settlements_webhook_event_id", "webhook_event_id"),
    )

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    event_id: Mapped[str] = mapped_column(String(150))
    event_type: Mapped[str | None] = mapped_column(String(100))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),)

class Loan(Base):
    __tablename__ = "loans"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    cycle_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    principal: Mapped[Decimal] = mapped_column(Numeric(14,2))
    principal_settled_with_own_balance: Mapped[Decimal] = mapped_column(
        Numeric(14,2),
        default=Decimal("0.00"),
    )
    monthly_rate: Mapped[Decimal] = mapped_column(Numeric(8,5))
    installments: Mapped[int] = mapped_column(Integer)
    calculation_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="REQUESTED")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    __table_args__ = (
        CheckConstraint("state_revision >= 0", name="ck_loans_state_revision_nonnegative"),
        ForeignKeyConstraint(
            ["member_id", "cycle_id"],
            ["cycle_participations.member_id", "cycle_participations.cycle_id"],
            name="fk_loans_member_cycle_participation",
        ),
        Index("ix_loans_cycle_id", "cycle_id"),
    )


class LoanSimulation(Base):
    __tablename__ = "loan_simulations"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    principal: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    monthly_rate: Mapped[Decimal] = mapped_column(Numeric(8, 5))
    installments: Mapped[int] = mapped_column(Integer)
    calculation_version: Mapped[str] = mapped_column(String(60))
    schedule_json: Mapped[str] = mapped_column(Text())
    schedule_hash: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="SIMULATED", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loan_id: Mapped[int | None] = mapped_column(ForeignKey("loans.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        CheckConstraint("installments >= 1 AND installments <= 6", name="ck_loan_simulations_installments_1_6"),
        CheckConstraint("monthly_rate = 0.20", name="ck_loan_simulations_official_rate"),
    )


class LoanInstallment(Base):
    __tablename__ = "loan_installments"
    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"))
    number: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    principal: Mapped[Decimal] = mapped_column(Numeric(14,2))
    interest: Mapped[Decimal] = mapped_column(Numeric(14,2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    penalty_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    paid_penalty_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    last_penalty_date: Mapped[date | None] = mapped_column(Date)
    late_charge_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fixed_penalty_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    paid_fixed_penalty_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    late_interest_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    paid_late_interest_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    late_interest_accrued_through_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    collection_stage: Mapped[str] = mapped_column(String(20), default="NORMAL", index=True)
    last_collection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_attempts: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        UniqueConstraint("loan_id", "number", name="uq_loan_installment_number"),
        CheckConstraint(
            "late_charge_version IS NULL OR length(trim(late_charge_version)) > 0",
            name="ck_loan_installments_late_charge_version",
        ),
        CheckConstraint(
            "(late_charge_version IS NULL AND fixed_penalty_amount IS NULL AND "
            "paid_fixed_penalty_amount IS NULL AND late_interest_amount IS NULL AND "
            "paid_late_interest_amount IS NULL AND late_interest_accrued_through_date IS NULL) OR "
            "(late_charge_version IS NOT NULL AND fixed_penalty_amount IS NOT NULL AND "
            "paid_fixed_penalty_amount IS NOT NULL AND late_interest_amount IS NOT NULL AND "
            "paid_late_interest_amount IS NOT NULL)",
            name="ck_loan_installments_late_charge_presence",
        ),
        CheckConstraint(
            "fixed_penalty_amount IS NULL OR fixed_penalty_amount >= 0",
            name="ck_loan_installments_fixed_penalty_nonnegative",
        ),
        CheckConstraint(
            "paid_fixed_penalty_amount IS NULL OR paid_fixed_penalty_amount >= 0",
            name="ck_loan_installments_paid_fixed_penalty_nonnegative",
        ),
        CheckConstraint(
            "late_interest_amount IS NULL OR late_interest_amount >= 0",
            name="ck_loan_installments_late_interest_nonnegative",
        ),
        CheckConstraint(
            "paid_late_interest_amount IS NULL OR paid_late_interest_amount >= 0",
            name="ck_loan_installments_paid_late_interest_nonnegative",
        ),
        CheckConstraint(
            "paid_fixed_penalty_amount IS NULL OR paid_fixed_penalty_amount <= fixed_penalty_amount",
            name="ck_loan_installments_paid_fixed_penalty_lte_assessed",
        ),
        CheckConstraint(
            "paid_late_interest_amount IS NULL OR paid_late_interest_amount <= late_interest_amount",
            name="ck_loan_installments_paid_late_interest_lte_accrued",
        ),
        Index("ix_loan_installments_status_due_date", "status", "due_date"),
    )


class LoanLateChargeEvent(Base):
    __tablename__ = "loan_late_charge_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    loan_installment_id: Mapped[int] = mapped_column(
        ForeignKey("loan_installments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    late_charge_version: Mapped[str] = mapped_column(String(60), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        nullable=False,
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    eligible_principal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    payment_settlement_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_settlements.id", ondelete="RESTRICT"),
        nullable=True,
    )
    payment_reversal_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_reversals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    metadata_json: Mapped[str | None] = mapped_column(Text(), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "length(trim(late_charge_version)) > 0",
            name="ck_loan_late_charge_events_version_nonempty",
        ),
        CheckConstraint(
            "event_type IN ('FIXED_PENALTY_ASSESSED', 'LATE_INTEREST_ACCRUED', "
            "'PRINCIPAL_BASE_REDUCED', 'PRINCIPAL_BASE_RESTORED', "
            "'LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE')",
            name="ck_loan_late_charge_events_type",
        ),
        CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_loan_late_charge_events_amount_nonnegative",
        ),
        CheckConstraint(
            "eligible_principal IS NULL OR eligible_principal >= 0",
            name="ck_loan_late_charge_events_principal_nonnegative",
        ),
        CheckConstraint(
            "event_type != 'FIXED_PENALTY_ASSESSED' OR amount IS NOT NULL",
            name="ck_loan_late_charge_events_fixed_penalty_amount",
        ),
        CheckConstraint(
            "event_type != 'LATE_INTEREST_ACCRUED' OR "
            "(amount IS NOT NULL AND eligible_principal IS NOT NULL)",
            name="ck_loan_late_charge_events_accrual_values",
        ),
        CheckConstraint(
            "event_type NOT IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE') OR amount IS NOT NULL",
            name="ck_loan_late_charge_events_adjustment_amount",
        ),
        CheckConstraint(
            "event_type NOT IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE') OR eligible_principal IS NULL",
            name="ck_loan_late_charge_events_adjustment_no_principal",
        ),
        CheckConstraint(
            "event_type NOT IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
            "'LATE_INTEREST_ADJUSTMENT_DECREASE') OR "
            "((payment_settlement_id IS NOT NULL AND payment_reversal_id IS NULL) OR "
            "(payment_settlement_id IS NULL AND payment_reversal_id IS NOT NULL))",
            name="ck_loan_late_charge_events_adjustment_cause",
        ),
        Index(
            "uq_loan_late_charge_events_fixed_penalty",
            "loan_installment_id",
            "late_charge_version",
            unique=True,
            postgresql_where=text("event_type = 'FIXED_PENALTY_ASSESSED'"),
            sqlite_where=text("event_type = 'FIXED_PENALTY_ASSESSED'"),
        ),
        Index(
            "uq_llce_late_interest_day",
            "loan_installment_id",
            "late_charge_version",
            "effective_date",
            unique=True,
            postgresql_where=text("event_type = 'LATE_INTEREST_ACCRUED'"),
            sqlite_where=text("event_type = 'LATE_INTEREST_ACCRUED'"),
        ),
        Index(
            "uq_llce_adjustment_settlement",
            "loan_installment_id",
            "late_charge_version",
            "payment_settlement_id",
            unique=True,
            postgresql_where=text(
                "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
                "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
                "payment_settlement_id IS NOT NULL"
            ),
            sqlite_where=text(
                "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
                "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
                "payment_settlement_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_llce_adjustment_reversal",
            "loan_installment_id",
            "late_charge_version",
            "payment_reversal_id",
            unique=True,
            postgresql_where=text(
                "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
                "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
                "payment_reversal_id IS NOT NULL"
            ),
            sqlite_where=text(
                "event_type IN ('LATE_INTEREST_ADJUSTMENT_INCREASE', "
                "'LATE_INTEREST_ADJUSTMENT_DECREASE') AND "
                "payment_reversal_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_loan_late_charge_events_installment_effective",
            "loan_installment_id",
            "effective_date",
        ),
        Index("ix_loan_late_charge_events_settlement_id", "payment_settlement_id"),
        Index("ix_loan_late_charge_events_reversal_id", "payment_reversal_id"),
    )

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(String(80), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    reference_type: Mapped[str] = mapped_column(String(50))
    reference_id: Mapped[str] = mapped_column(String(80))
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_entries.id"))
    previous_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (Index("ix_ledger_reference", "reference_type", "reference_id"),)


class PaymentReversal(Base):
    __tablename__ = "payment_reversals"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"))
    settlement_id: Mapped[int] = mapped_column(ForeignKey("payment_settlements.id"))
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text())
    reversed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reversal_competence: Mapped[date] = mapped_column(Date)
    original_competence: Mapped[date | None] = mapped_column(Date)
    original_due_date: Mapped[date | None] = mapped_column(Date)
    original_date_kind: Mapped[str] = mapped_column(String(40))
    amount_received: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    amount_applied: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    principal_applied: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    interest_applied: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    penalty_applied: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    excess_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    receipt_number: Mapped[str] = mapped_column(String(80))
    receipt_version: Mapped[str] = mapped_column(String(20))
    receipt_snapshot_json: Mapped[str] = mapped_column(Text())
    receipt_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    payment: Mapped["Payment"] = relationship(back_populates="payment_reversal")
    settlement: Mapped["PaymentSettlement"] = relationship(back_populates="payment_reversal")
    admin: Mapped["User"] = relationship(back_populates="payment_reversals")
    components: Mapped[list["PaymentReversalComponent"]] = relationship(back_populates="payment_reversal")

    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_payment_reversals_payment_id"),
        UniqueConstraint("settlement_id", name="uq_payment_reversals_settlement_id"),
        UniqueConstraint("receipt_number", name="uq_payment_reversals_receipt_number"),
        UniqueConstraint("receipt_hash", name="uq_payment_reversals_receipt_hash"),
        CheckConstraint("length(trim(reason)) >= 5", name="ck_payment_reversals_reason"),
        CheckConstraint("amount_received >= 0 AND amount_applied >= 0 AND principal_applied >= 0 AND interest_applied >= 0 AND penalty_applied >= 0 AND excess_amount >= 0", name="ck_payment_reversals_nonnegative_amounts"),
        CheckConstraint("amount_received = amount_applied + excess_amount", name="ck_payment_reversals_received_allocation"),
        CheckConstraint("amount_applied = principal_applied + interest_applied + penalty_applied", name="ck_payment_reversals_applied_components"),
        CheckConstraint("((original_date_kind = 'CONTRIBUTION_COMPETENCE' AND original_competence IS NOT NULL AND original_due_date IS NULL) OR (original_date_kind = 'LOAN_INSTALLMENT_DUE_DATE' AND original_competence IS NULL AND original_due_date IS NOT NULL) OR (original_date_kind = 'AGREEMENT_INSTALLMENT_DUE_DATE' AND original_competence IS NULL AND original_due_date IS NOT NULL))", name="ck_payment_reversals_original_date"),
        CheckConstraint("receipt_version = 'v1'", name="ck_payment_reversals_receipt_version"),
        Index("ix_payment_reversals_admin_id", "admin_id"),
        Index("ix_payment_reversals_reversed_at", "reversed_at"),
        Index("ix_payment_reversals_reversal_competence", "reversal_competence"),
    )


class PaymentReversalComponent(Base):
    __tablename__ = "payment_reversal_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_reversal_id: Mapped[int] = mapped_column(ForeignKey("payment_reversals.id"))
    original_ledger_entry_id: Mapped[int] = mapped_column(ForeignKey("ledger_entries.id"))
    compensating_ledger_entry_id: Mapped[int] = mapped_column(ForeignKey("ledger_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    payment_reversal: Mapped["PaymentReversal"] = relationship(back_populates="components")
    original_ledger_entry: Mapped["LedgerEntry"] = relationship(foreign_keys=[original_ledger_entry_id])
    compensating_ledger_entry: Mapped["LedgerEntry"] = relationship(foreign_keys=[compensating_ledger_entry_id])

    __table_args__ = (
        UniqueConstraint("original_ledger_entry_id", name="uq_payment_reversal_components_original_ledger"),
        UniqueConstraint("compensating_ledger_entry_id", name="uq_payment_reversal_components_compensating_ledger"),
        CheckConstraint("original_ledger_entry_id <> compensating_ledger_entry_id", name="ck_payment_reversal_components_distinct_ledgers"),
        Index("ix_payment_reversal_components_reversal_id", "payment_reversal_id"),
    )


class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    expense_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(80), default="GENERAL")
    status: Mapped[str] = mapped_column(String(20), default="POSTED")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (Index("ix_expenses_date", "expense_date"), Index("ix_expenses_category", "category"))

class MonthlyClosing(Base):
    __tablename__ = "monthly_closings"
    id: Mapped[int] = mapped_column(primary_key=True)
    competence: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    total_contributions: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    total_expenses: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    total_interest_received: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    ledger_balance: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    snapshot_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

class FinancialReconciliation(Base):
    __tablename__ = "financial_reconciliations"
    id: Mapped[int] = mapped_column(primary_key=True)
    competence: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    run_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("competence", "snapshot_hash", name="uq_fin_recon_comp_hash"),)

class GovernanceSnapshot(Base):
    __tablename__ = "governance_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PASS", index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ExecutiveDashboardSnapshot(Base):
    __tablename__ = "executive_dashboard_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PASS", index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)



class FinancialProjectionSnapshot(Base):
    __tablename__ = "financial_projection_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    horizon_months: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PASS", index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("as_of_date", "horizon_months", "scenario", name="uq_fin_projection_scope"),)

class ReportSnapshot(Base):
    __tablename__ = "report_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_type: Mapped[str] = mapped_column(String(30), index=True)
    competence: Mapped[date] = mapped_column(Date, index=True)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("report_type", "competence", "scope_id", name="uq_report_snapshot_scope"),)

class CycleAnnualClosing(Base):
    __tablename__ = "cycle_annual_closings"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("cycles.id", name="fk_cycle_annual_closings_cycle", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ASSESSING")
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_cycle_annual_closings_created_by", ondelete="RESTRICT"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_cycle_annual_closings_updated_by", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_cycle_annual_closings_approved_by", ondelete="RESTRICT"))
    approved_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("cycle_annual_closing_reviews.id", name="fk_cycle_annual_closings_approved_review", ondelete="RESTRICT"),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_cycle_annual_closings_closed_by", ondelete="RESTRICT"))

    __table_args__ = (
        UniqueConstraint("cycle_id", name="uq_cycle_annual_closings_cycle"),
        UniqueConstraint("id", "cycle_id", name="uq_cycle_annual_closings_id_cycle"),
        CheckConstraint(
            "status IN ('ASSESSING', 'READY_FOR_REVIEW', 'MASTER_APPROVED', 'CLOSED', 'PAYING', 'LIQUIDATED')",
            name="ck_cycle_annual_closings_status",
        ),
        CheckConstraint("state_revision >= 0", name="ck_cycle_annual_closings_revision"),
        Index("ix_cycle_annual_closings_status", "status"),
    )


class CycleAnnualClosingCashEvidence(Base):
    __tablename__ = "cycle_annual_closing_cash_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    closing_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_id: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("workflow_execution_evidence_files.id", ondelete="RESTRICT"), nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_cash_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closing_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    attested_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    __table_args__ = (
        ForeignKeyConstraint(["closing_id", "cycle_id"], ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"], name="fk_cycle_cash_evidence_closing_cycle", ondelete="RESTRICT"),
        UniqueConstraint("id", "closing_id", "cycle_id", name="uq_cycle_cash_evidence_id_closing_cycle"),
        CheckConstraint("declared_cash_balance >= 0", name="ck_cycle_cash_evidence_balance"),
        CheckConstraint("length(file_sha256) = 64", name="ck_cycle_cash_evidence_hash"),
    )


class CycleAnnualClosingReview(Base):
    __tablename__ = "cycle_annual_closing_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    closing_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("cycles.id", ondelete="RESTRICT"), nullable=False)
    cash_evidence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    review_version: Mapped[int] = mapped_column(Integer, nullable=False)
    process_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    closing_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(60), nullable=False)
    calculation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ledger_cash_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    actual_cash_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reconciliation_difference: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    participant_payout_liability: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    administration_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    required_liquidity: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    liquidity_surplus: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cash_evidence_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    cash_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reconciliation_payload: Mapped[str] = mapped_column(Text(), nullable=False)
    reconciliation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["closing_id", "cycle_id"],
            ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"],
            name="fk_cycle_annual_reviews_closing_cycle", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["cash_evidence_id", "closing_id", "cycle_id"],
            ["cycle_annual_closing_cash_evidence.id", "cycle_annual_closing_cash_evidence.closing_id", "cycle_annual_closing_cash_evidence.cycle_id"],
            name="fk_cycle_annual_reviews_cash_evidence", ondelete="RESTRICT",
        ),
        UniqueConstraint("closing_id", "review_version", name="uq_cycle_annual_reviews_version"),
        UniqueConstraint("id", "closing_id", name="uq_cycle_annual_reviews_id_closing"),
        UniqueConstraint("closing_id", "review_hash", name="uq_cycle_annual_reviews_hash"),
        CheckConstraint("review_version >= 1 AND process_revision >= 1", name="ck_cycle_annual_reviews_versions"),
        CheckConstraint("actual_cash_balance >= 0 AND reconciliation_difference = 0 AND liquidity_surplus >= 0", name="ck_cycle_annual_reviews_gates"),
        CheckConstraint("required_liquidity = participant_payout_liability + administration_fee", name="ck_cycle_annual_reviews_liquidity_equation"),
        CheckConstraint("length(calculation_hash) = 64 AND length(cash_evidence_hash) = 64 AND length(reconciliation_hash) = 64 AND length(review_hash) = 64", name="ck_cycle_annual_reviews_hash_lengths"),
        Index("ix_cycle_annual_reviews_closing_created", "closing_id", "created_at"),
    )


class CycleAnnualClosingSnapshot(Base):
    __tablename__ = "cycle_annual_closing_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    closing_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("cycles.id", name="fk_cycle_annual_snapshots_cycle", ondelete="RESTRICT"),
        nullable=False,
    )
    snapshot_version: Mapped[str] = mapped_column(String(60), nullable=False)
    closing_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(60), nullable=False)
    canonical_payload: Mapped[str] = mapped_column(Text(), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    gross_realized_result: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    administration_fee_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    administration_fee: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    distributable_result: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_eligible_contributions: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_cycle_annual_snapshots_created_by", ondelete="RESTRICT"))

    __table_args__ = (
        UniqueConstraint("closing_id", name="uq_cycle_annual_snapshots_closing"),
        ForeignKeyConstraint(
            ["closing_id", "cycle_id"],
            ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"],
            name="fk_cycle_annual_snapshots_closing_cycle", ondelete="RESTRICT",
        ),
        UniqueConstraint("cycle_id", name="uq_cycle_annual_snapshots_cycle"),
        UniqueConstraint("payload_hash", name="uq_cycle_annual_snapshots_hash"),
        CheckConstraint("length(payload_hash) = 64", name="ck_cycle_annual_snapshots_hash_length"),
        CheckConstraint(
            "gross_realized_result >= 0 AND administration_fee >= 0 AND distributable_result >= 0 "
            "AND total_eligible_contributions >= 0 AND administration_fee_rate >= 0 "
            "AND administration_fee_rate <= 1",
            name="ck_cycle_annual_snapshots_nonnegative",
        ),
        CheckConstraint(
            "gross_realized_result = administration_fee + distributable_result",
            name="ck_cycle_annual_snapshots_result_equation",
        ),
        Index("ix_cycle_annual_snapshots_cycle_created", "cycle_id", "created_at"),
    )


class CycleAnnualClosingPayoutObligation(Base):
    """Immutable individual amount owed according to an official closing snapshot."""

    __tablename__ = "cycle_annual_closing_payout_obligations"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("cycle_annual_closing_snapshots.id", name="fk_cpo_snapshot", ondelete="RESTRICT"),
        nullable=False,
    )
    closing_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_id: Mapped[int] = mapped_column(Integer, nullable=False)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", name="fk_cpo_member", ondelete="RESTRICT"), nullable=False,
    )
    cycle_participation_id: Mapped[int] = mapped_column(
        ForeignKey("cycle_participations.id", name="fk_cpo_participation", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)

    __table_args__ = (
        ForeignKeyConstraint(
            ["closing_id", "cycle_id"],
            ["cycle_annual_closings.id", "cycle_annual_closings.cycle_id"],
            name="fk_cpo_closing_cycle", ondelete="RESTRICT",
        ),
        UniqueConstraint("snapshot_id", "member_id", name="uq_cpo_snapshot_member"),
        UniqueConstraint("snapshot_id", "cycle_participation_id", name="uq_cpo_snapshot_part"),
        CheckConstraint("amount >= 0", name="ck_cpo_amount_nonnegative"),
        CheckConstraint("length(source_payload_hash) = 64", name="ck_cpo_hash_length"),
    )


class CycleRealizedGainEvent(Base):
    __tablename__ = "cycle_realized_gain_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("cycles.id", name="fk_cycle_realized_gain_events_cycle", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    realized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)
    source_id: Mapped[str] = mapped_column(String(150), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(150), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_cycle_realized_gain_events_created_by", ondelete="RESTRICT"))
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("cycle_realized_gain_events.id", name="fk_cycle_realized_gain_events_reversal", ondelete="RESTRICT")
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('INVESTMENT_YIELD_REALIZED', 'OTHER_REALIZED_GAIN')",
            name="ck_cycle_realized_gain_events_type",
        ),
        CheckConstraint("amount > 0", name="ck_cycle_realized_gain_events_positive"),
        CheckConstraint("length(trim(source_type)) > 0 AND length(trim(source_id)) > 0", name="ck_cycle_realized_gain_events_source"),
        CheckConstraint(
            f"source_type IN ({_CYCLE_EXTERNAL_GAIN_SOURCE_TYPES_SQL})",
            name="ck_cycle_realized_gain_events_external_source_only",
        ),
        CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_cycle_realized_gain_events_idempotency"),
        CheckConstraint("length(trim(evidence_reference)) > 0", name="ck_cycle_realized_gain_events_evidence"),
        CheckConstraint("length(evidence_hash) = 64", name="ck_cycle_realized_gain_events_evidence_hash"),
        UniqueConstraint("idempotency_key", name="uq_cycle_realized_gain_events_idempotency"),
        UniqueConstraint("cycle_id", "event_type", "source_type", "source_id", name="uq_cycle_realized_gain_events_source"),
        UniqueConstraint("reversal_of_id", name="uq_cycle_realized_gain_events_one_full_reversal"),
        Index("ix_cycle_realized_gain_events_cycle_realized", "cycle_id", "realized_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(80))
    details: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class CollectionAgreement(Base):
    __tablename__ = "collection_agreements"
    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED", index=True)
    installments: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    reason: Mapped[str | None] = mapped_column(Text())
    snapshot: Mapped[str] = mapped_column(Text())
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    __table_args__ = (UniqueConstraint("loan_id", "status", name="uq_agreement_loan_status"), Index("ix_agreements_member_status", "member_id", "status"), CheckConstraint("state_revision >= 0", name="ck_collection_agreements_state_revision_nonnegative"))

class AgreementInstallment(Base):
    __tablename__ = "agreement_installments"
    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("collection_agreements.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    principal: Mapped[Decimal] = mapped_column(Numeric(14,2))
    penalty_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    paid_penalty_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal("0"))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    __table_args__ = (UniqueConstraint("agreement_id", "number", name="uq_agreement_installment_number"),)

class CollectionEvent(Base):
    __tablename__ = "collection_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    installment_id: Mapped[int | None] = mapped_column(ForeignKey("loan_installments.id"), nullable=True)
    agreement_installment_id: Mapped[int | None] = mapped_column(ForeignKey("agreement_installments.id"), nullable=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    event_type: Mapped[str] = mapped_column(String(50))
    event_date: Mapped[date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(20), default="IN_APP")
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        CheckConstraint(
            "(installment_id IS NOT NULL AND agreement_installment_id IS NULL) "
            "OR (installment_id IS NULL AND agreement_installment_id IS NOT NULL)",
            name="ck_collection_events_exactly_one_subject",
        ),
        Index(
            "uq_collection_event_loan_day",
            "installment_id", "event_type", "event_date",
            unique=True,
            postgresql_where=text("installment_id IS NOT NULL"),
            sqlite_where=text("installment_id IS NOT NULL"),
        ),
        Index(
            "uq_collection_event_agreement_day",
            "agreement_installment_id", "event_type", "event_date",
            unique=True,
            postgresql_where=text("agreement_installment_id IS NOT NULL"),
            sqlite_where=text("agreement_installment_id IS NOT NULL"),
        ),
        Index("ix_collection_events_member_date", "member_id", "event_date"),
    )

class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    loan_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    collection_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    account_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    error: Mapped[str | None] = mapped_column(Text())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="IN_APP")
    type: Mapped[str] = mapped_column(String(60), index=True)
    title: Mapped[str] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text())
    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)


# v0.35: Ledger is append-only. Corrections must be represented by a reversal entry.
@event.listens_for(__import__("sqlalchemy").orm.Session, "before_flush")
def _protect_ledger_mutations(session, flush_context, instances):
    immutable_types = (LedgerEntry, CycleAnnualClosingSnapshot, CycleAnnualClosingPayoutObligation, CycleAnnualClosingReview, CycleAnnualClosingCashEvidence, CycleRealizedGainEvent)
    for obj in list(session.dirty):
        if isinstance(obj, MemberPayoutDestination):
            state = inspect(obj)
            immutable_fields = (
                "id", "member_id", "version", "key_type", "encrypted_value",
                "masked_value", "created_at", "created_by",
            )
            if any(state.attrs[name].history.has_changes() for name in immutable_fields):
                raise RuntimeError("MemberPayoutDestination: campos da versão são imutáveis.")
            lifecycle_fields = (
                "verification_status", "verified_at", "verified_by", "revoked_at", "revoked_by",
            )
            if any(state.attrs[name].history.has_changes() for name in lifecycle_fields):
                status_history = state.attrs["verification_status"].history
                revoked_at_history = state.attrs["revoked_at"].history
                revoked_by_history = state.attrs["revoked_by"].history
                can_revoke = (
                    status_history.has_changes()
                    and status_history.deleted
                    and status_history.deleted[0] in {"UNVERIFIED", "VERIFIED"}
                    and obj.verification_status == "REVOKED"
                    and not state.attrs["verified_at"].history.has_changes()
                    and not state.attrs["verified_by"].history.has_changes()
                    and revoked_at_history.has_changes()
                    and revoked_at_history.deleted
                    and revoked_at_history.deleted[0] is None
                    and obj.revoked_at is not None
                    and revoked_by_history.has_changes()
                    and revoked_by_history.deleted
                    and revoked_by_history.deleted[0] is None
                    and obj.revoked_by is not None
                )
                if not can_revoke:
                    raise RuntimeError(
                        "MemberPayoutDestination: ciclo de vida só permite revogação."
                    )
            continue
        if isinstance(obj, immutable_types):
            raise RuntimeError(f"{type(obj).__name__} é imutável; use uma linha compensatória quando aplicável.")
    for obj in list(session.deleted):
        if isinstance(obj, immutable_types) or isinstance(obj, MemberPayoutDestination):
            raise RuntimeError(f"{type(obj).__name__} não pode ser excluído.")

class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    consent_type: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(30))
    granted: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(30), default="APP")
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class DataAccessLog(Base):
    __tablename__ = "data_access_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    subject_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    resource: Mapped[str] = mapped_column(String(80))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class PrivacyRequest(Base):
    __tablename__ = "privacy_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED", index=True)
    reason: Mapped[str | None] = mapped_column(Text())
    decision_note: Mapped[str | None] = mapped_column(Text())
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class FinancialRiskAssessment(Base):
    __tablename__ = 'financial_risk_assessments'
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(30), index=True)
    subject_id: Mapped[str] = mapped_column(String(80), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey('members.id'), index=True)
    score: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), index=True)
    reasons: Mapped[str] = mapped_column(Text())
    rules_json: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class CollectionCase(Base):
    __tablename__ = 'collection_cases'
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey('members.id'), index=True)
    loan_id: Mapped[int | None] = mapped_column(ForeignKey('loans.id'), nullable=True, index=True)
    agreement_id: Mapped[int | None] = mapped_column(ForeignKey('collection_agreements.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    stage: Mapped[str] = mapped_column(String(30), default='SOFT')
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    resolution_note: Mapped[str | None] = mapped_column(Text())
    __table_args__ = (
        CheckConstraint(
            "(loan_id IS NOT NULL AND agreement_id IS NULL) "
            "OR (loan_id IS NULL AND agreement_id IS NOT NULL)",
            name="ck_collection_cases_exactly_one_subject",
        ),
        Index(
            "uq_collection_case_open_loan_subject",
            "member_id", "loan_id",
            unique=True,
            postgresql_where=text("status = 'OPEN' AND loan_id IS NOT NULL"),
            sqlite_where=text("status = 'OPEN' AND loan_id IS NOT NULL"),
        ),
        Index(
            "uq_collection_case_open_agreement_subject",
            "member_id", "agreement_id",
            unique=True,
            postgresql_where=text("status = 'OPEN' AND agreement_id IS NOT NULL"),
            sqlite_where=text("status = 'OPEN' AND agreement_id IS NOT NULL"),
        ),
    )

class PaymentPromise(Base):
    __tablename__ = 'payment_promises'
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey('collection_cases.id'), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey('members.id'), index=True)
    promised_amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    promised_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text())

class ScenarioSimulationSnapshot(Base):
    __tablename__ = 'scenario_simulation_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    horizon_months: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class LoanCapacitySnapshot(Base):
    __tablename__ = 'loan_capacity_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey('groups.id'), nullable=True, index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey('members.id'), nullable=True, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    horizon_months: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[str] = mapped_column(String(20), index=True)
    decision: Mapped[str] = mapped_column(String(20), index=True)
    capacity: Mapped[Decimal] = mapped_column(Numeric(14,2))
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ResourceAllocationSnapshot(Base):
    __tablename__ = 'resource_allocation_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'), index=True)
    capacity: Mapped[Decimal] = mapped_column(Numeric(14,2))
    allocated_total: Mapped[Decimal] = mapped_column(Numeric(14,2))
    decision: Mapped[str] = mapped_column(String(20), index=True)
    method: Mapped[str] = mapped_column(String(50))
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AllocationPolicy(Base):
    __tablename__ = "allocation_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="Política padrão")
    quota_weight: Mapped[Decimal] = mapped_column(Numeric(8,3), default=Decimal("1.000"))
    payment_history_weight: Mapped[Decimal] = mapped_column(Numeric(8,3), default=Decimal("1.000"))
    tenure_weight: Mapped[Decimal] = mapped_column(Numeric(8,3), default=Decimal("0.250"))
    risk_weight: Mapped[Decimal] = mapped_column(Numeric(8,3), default=Decimal("1.000"))
    review_factor: Mapped[Decimal] = mapped_column(Numeric(6,3), default=Decimal("0.500"))
    tie_breaker: Mapped[str] = mapped_column(String(30), default="OLDEST_MEMBER")
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class AllocationTransparencySnapshot(Base):
    __tablename__ = "allocation_transparency_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_allocation_snapshot_id: Mapped[int] = mapped_column(ForeignKey("resource_allocation_snapshots.id"), unique=True, index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    policy_version: Mapped[int] = mapped_column(Integer)
    policy_snapshot_json: Mapped[str] = mapped_column(Text())
    input_snapshot_json: Mapped[str] = mapped_column(Text())
    explanation_json: Mapped[str] = mapped_column(Text())
    explanation_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class AllocationDecisionRecord(Base):
    __tablename__ = "allocation_decision_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    transparency_snapshot_id: Mapped[int] = mapped_column(ForeignKey("allocation_transparency_snapshots.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    analyzed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20), index=True)
    policy_version: Mapped[int] = mapped_column(Integer)
    transparency_hash: Mapped[str] = mapped_column(String(64))
    decision_input_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    exception_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    exception_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class IntegratedGovernanceSnapshot(Base):
    __tablename__ = "integrated_governance_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    final_decision: Mapped[str] = mapped_column(String(20), index=True)
    scenario: Mapped[str] = mapped_column(String(30))
    horizon_months: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class SecureReleaseAuthorization(Base):
    __tablename__ = "secure_release_authorizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    governance_hash: Mapped[str] = mapped_column(String(64), index=True)
    governance_snapshot: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(String(20), default="AUTHORIZED", index=True)
    authorized_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_count: Mapped[int] = mapped_column(Integer, default=1)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class OperationalActionRecord(Base):
    __tablename__ = "operational_action_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("operational_control_snapshots.id"), nullable=True, index=True)
    action_code: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    source_task_id: Mapped[int | None] = mapped_column(ForeignKey('operational_workflow_tasks.id'), nullable=True, index=True)
    escalation_level: Mapped[str] = mapped_column(String(20), default='NONE', index=True)


class OperationalControlSnapshot(Base):
    __tablename__ = "operational_control_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="PASS", index=True)
    action_count: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class OperationalWorkflowTask(Base):
    __tablename__ = 'operational_workflow_tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    action_code: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    priority: Mapped[str] = mapped_column(String(20), default='MEDIUM', index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    sla_status: Mapped[str] = mapped_column(String(20), default='ON_TRACK', index=True)
    escalation_level: Mapped[str] = mapped_column(String(20), default='NONE', index=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class OperationalWorkflowOrchestration(Base):
    __tablename__ = 'operational_workflow_orchestrations'
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('operational_workflow_tasks.id'), unique=True, index=True)
    queue_status: Mapped[str] = mapped_column(String(20), default='READY', index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(20), index=True)
    sla_status: Mapped[str] = mapped_column(String(20), index=True)
    escalation_level: Mapped[str] = mapped_column(String(20), default='NONE', index=True)
    orchestration_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    execution_state: Mapped[str] = mapped_column(String(24), default='PENDING_ACCEPTANCE', index=True)
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowExecutionEvidence(Base):
    __tablename__ = 'workflow_execution_evidence'
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('operational_workflow_tasks.id'), index=True)
    added_by: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    evidence_type: Mapped[str] = mapped_column(String(20), default='NOTE')
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content: Mapped[str] = mapped_column(Text())
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class WorkflowExecutionEvidenceFile(Base):
    __tablename__ = 'workflow_execution_evidence_files'
    __table_args__ = (
        UniqueConstraint('evidence_id', 'version', name='uq_workflow_evidence_file_version'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey('workflow_execution_evidence.id'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class WorkflowExecutionChecklistItem(Base):
    __tablename__ = 'workflow_execution_checklist_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('operational_workflow_tasks.id'), index=True)
    label: Mapped[str] = mapped_column(String(240))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    completed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class OperationalWorkflowEvent(Base):
    __tablename__ = 'operational_workflow_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('operational_workflow_tasks.id'), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text(), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class WorkflowEvidenceIntegrityEvent(Base):
    __tablename__ = 'workflow_evidence_integrity_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey('workflow_execution_evidence_files.id'), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('operational_workflow_tasks.id'), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    expected_sha256: Mapped[str] = mapped_column(String(64))
    observed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), index=True)
    details: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class WorkflowIncident(Base):
    __tablename__ = "workflow_incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    check_code: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    remediation_plan: Mapped[str | None] = mapped_column(Text(), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text(), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class WorkflowComplianceSnapshot(Base):
    __tablename__ = 'workflow_compliance_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='PASS', index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class OperationalRiskTrendSnapshot(Base):
    __tablename__ = 'operational_risk_trend_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='PASS', index=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class CorrectiveActionPlan(Base):
    __tablename__ = 'corrective_action_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey('workflow_incidents.id'), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default='OPEN', index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    priority: Mapped[str] = mapped_column(String(16), default='HIGH', index=True)
    objective: Mapped[str] = mapped_column(Text())
    root_cause: Mapped[str | None] = mapped_column(Text(), nullable=True)
    effectiveness_criteria: Mapped[str | None] = mapped_column(Text(), nullable=True)
    effectiveness_result: Mapped[str | None] = mapped_column(Text(), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class CorrectiveAction(Base):
    __tablename__ = 'corrective_actions'
    id: Mapped[int] = mapped_column(primary_key=True)
    capa_id: Mapped[int] = mapped_column(ForeignKey('corrective_action_plans.id'), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default='OPEN', index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    evidence_required: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class CapaEffectivenessReview(Base):
    __tablename__ = 'capa_effectiveness_reviews'
    id: Mapped[int] = mapped_column(primary_key=True)
    capa_id: Mapped[int] = mapped_column(ForeignKey('corrective_action_plans.id'), index=True)
    result: Mapped[str] = mapped_column(Text())
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class CapaRecurrenceEvent(Base):
    __tablename__ = 'capa_recurrence_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    capa_id: Mapped[int] = mapped_column(ForeignKey('corrective_action_plans.id'), index=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey('workflow_incidents.id'), index=True)
    source_check_code: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(20))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint('capa_id','incident_id',name='uq_capa_recurrence_capa_incident'),)

class OperationalRiskAlert(Base):
    __tablename__ = 'operational_risk_alerts'
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    alert_type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    threshold: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text())
    recommended_action: Mapped[str] = mapped_column(Text())
    source_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey('operational_risk_trend_snapshots.id'), nullable=True, index=True)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
class OperationalRiskResponsePlan(Base):
    __tablename__ = 'operational_risk_response_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey('operational_risk_alerts.id'), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default='OPEN', index=True)
    priority: Mapped[str] = mapped_column(String(20), default='MEDIUM', index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    workflow_task_id: Mapped[int | None] = mapped_column(ForeignKey('operational_workflow_tasks.id'), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan: Mapped[str] = mapped_column(Text(), default='')
    evidence_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text(), nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String(64), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class ExecutiveRiskDecision(Base):
    __tablename__ = 'executive_risk_decisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey('executive_risk_response_snapshots.id'), nullable=True, index=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey('operational_risk_alerts.id'), nullable=True, index=True)
    response_plan_id: Mapped[int | None] = mapped_column(ForeignKey('operational_risk_response_plans.id'), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    priority: Mapped[str] = mapped_column(String(20), default='MEDIUM', index=True)
    decision_type: Mapped[str] = mapped_column(String(40), default='OPERATIONAL_REVIEW')
    recommendation: Mapped[str] = mapped_column(Text(), default='')
    rationale: Mapped[str | None] = mapped_column(Text(), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    conditions: Mapped[str | None] = mapped_column(Text(), nullable=True)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ExecutiveRiskDecisionGovernance(Base):
    __tablename__ = 'executive_risk_decision_governance'
    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey('executive_risk_decisions.id'), unique=True, index=True)
    required_approvals: Mapped[int] = mapped_column(Integer, default=1)
    approvals_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default='PENDING', index=True)
    conflict_status: Mapped[str] = mapped_column(String(20), default='NOT_CHECKED')
    conditions_required: Mapped[bool] = mapped_column(Boolean, default=False)
    primary_approver_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    secondary_approver_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    validated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class ExecutiveRiskResponseSnapshot(Base):
    __tablename__ = 'executive_risk_response_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='PASS', index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ExecutiveRiskDecisionExecution(Base):
    __tablename__ = 'executive_risk_decision_executions'
    id: Mapped[int] = mapped_column(primary_key=True)
    governance_id: Mapped[int] = mapped_column(ForeignKey('executive_risk_decision_governance.id'), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default='PENDING', index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    started_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text(), nullable=True)
    execution_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    evidence_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ExecutiveRiskEffectiveness(Base):
    __tablename__ = 'executive_risk_effectiveness'
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey('executive_risk_decision_executions.id'), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    indicator_code: Mapped[str] = mapped_column(String(60), default='RISK_SCORE')
    baseline_score: Mapped[float | None] = mapped_column(nullable=True)
    followup_score: Mapped[float | None] = mapped_column(nullable=True)
    delta_score: Mapped[float | None] = mapped_column(nullable=True)
    effectiveness_criteria: Mapped[str] = mapped_column(Text())
    effectiveness_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContinuousImprovementRecommendation(Base):
    __tablename__ = 'continuous_improvement_recommendations'
    id: Mapped[int] = mapped_column(primary_key=True)
    indicator_code: Mapped[str] = mapped_column(String(60), index=True)
    pattern_code: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    effective_count: Mapped[int] = mapped_column(Integer, default=0)
    partial_count: Mapped[int] = mapped_column(Integer, default=0)
    ineffective_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_delta: Mapped[float | None] = mapped_column(nullable=True)
    recommendation: Mapped[str] = mapped_column(Text())
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    implementation_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    implemented_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    implemented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContinuousImprovementPlan(Base):
    __tablename__ = 'continuous_improvement_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_recommendations.id'), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    indicator_code: Mapped[str] = mapped_column(String(60), index=True)
    baseline_value: Mapped[float | None] = mapped_column(nullable=True)
    target_value: Mapped[float | None] = mapped_column(nullable=True)
    target_direction: Mapped[str] = mapped_column(String(20), default='DECREASE')
    objective: Mapped[str] = mapped_column(Text())
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    implementation_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    implemented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContinuousImprovementMeasurement(Base):
    __tablename__ = 'continuous_improvement_measurements'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_plans.id'), index=True)
    measurement_type: Mapped[str] = mapped_column(String(30), default='FOLLOW_UP')
    value: Mapped[float] = mapped_column()
    baseline_value: Mapped[float | None] = mapped_column(nullable=True)
    delta: Mapped[float | None] = mapped_column(nullable=True)
    result: Mapped[str] = mapped_column(String(20), default='PENDING')
    evidence_note: Mapped[str] = mapped_column(Text())
    measured_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    integrity_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContinuousImprovementPrioritySnapshot(Base):
    __tablename__ = 'continuous_improvement_priority_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='PASS', index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContinuousImprovementDashboardSnapshot(Base):
    __tablename__ = 'continuous_improvement_dashboard_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementAssignmentCapacity(Base):
    __tablename__ = 'continuous_improvement_assignment_capacities'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), unique=True, index=True)
    max_active_items: Mapped[int] = mapped_column(Integer, default=5)
    max_critical_items: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementAssignmentSnapshot(Base):
    __tablename__ = 'continuous_improvement_assignment_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementAssignmentDecision(Base):
    __tablename__ = 'continuous_improvement_assignment_decisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_assignment_snapshots.id'), index=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_recommendations.id'), index=True)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(20), index=True)
    decision_note: Mapped[str] = mapped_column(Text())
    decided_by: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    decision_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementExecution(Base):
    __tablename__ = 'continuous_improvement_executions'
    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_assignment_decisions.id'), unique=True, index=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_recommendations.id'), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_plans.id'), index=True)
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    assigned_to: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    evidence_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    execution_hash: Mapped[str] = mapped_column(String(64), default='', unique=True, index=True)
    evidence_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContinuousImprovementExecutionEvidenceFile(Base):
    __tablename__ = 'continuous_improvement_execution_evidence_files'
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_executions.id'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(240), unique=True, index=True)
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ContinuousImprovementEvidenceIntegrityEvent(Base):
    __tablename__ = 'continuous_improvement_evidence_integrity_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_execution_evidence_files.id'), index=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_executions.id'), index=True)
    event_type: Mapped[str] = mapped_column(String(20))
    expected_sha256: Mapped[str] = mapped_column(String(64))
    observed_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    details: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class ContinuousImprovementCertification(Base):
    __tablename__ = 'continuous_improvement_certifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_executions.id'), unique=True, index=True)
    certificate_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='CERTIFIED', index=True)
    package_json: Mapped[str] = mapped_column(Text())
    package_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    certified_by: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    certified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    certification_note: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class ContinuousImprovementExecutiveAuditSnapshot(Base):
    __tablename__ = 'continuous_improvement_executive_audit_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementAuditSnapshot(Base):
    __tablename__ = 'continuous_improvement_audit_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(ForeignKey('continuous_improvement_executions.id'), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementDashboardExecutiveSnapshot(Base):
    __tablename__ = 'continuous_improvement_dashboard_executive_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementActionQueueSnapshot(Base):
    __tablename__ = 'continuous_improvement_action_queue_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementKpiSnapshot(Base):
    __tablename__ = 'continuous_improvement_kpi_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementSlaSnapshot(Base):
    __tablename__ = 'continuous_improvement_sla_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementComplianceSnapshot(Base):
    __tablename__ = 'continuous_improvement_compliance_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementExportSnapshot(Base):
    __tablename__ = 'continuous_improvement_export_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementProductionReadinessSnapshot(Base):
    __tablename__ = 'continuous_improvement_production_readiness_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date(), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class ContinuousImprovementProgramReleaseSnapshot(Base):
    __tablename__ = 'continuous_improvement_program_release_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    release_version: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text())
    snapshot_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    generated_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class MemberFinancialAccount(Base):
    __tablename__ = "member_financial_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id"),
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )

    member: Mapped["Member"] = relationship(
        back_populates="financial_account",
    )
    entries: Mapped[list["MemberFinancialEntry"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="MemberFinancialEntry.id",
    )


class MemberFinancialEntry(Base):
    __tablename__ = "member_financial_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("member_financial_accounts.id"),
        index=True,
    )
    entry_type: Mapped[str] = mapped_column(String(50), index=True)
    direction: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        index=True,
    )
    contribution_id: Mapped[int | None] = mapped_column(
        ForeignKey("contributions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    payment_settlement_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_settlements.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    payment_reversal_id: Mapped[int | None] = mapped_column(ForeignKey("payment_reversals.id"))

    account: Mapped["MemberFinancialAccount"] = relationship(
        back_populates="entries",
    )
    payment_reversal: Mapped["PaymentReversal | None"] = relationship()

    __table_args__ = (
        Index(
            "ix_member_financial_entries_reference",
            "reference_type",
            "reference_id",
        ),
        Index(
            "uq_member_financial_entries_one_payment_reversal",
            "payment_reversal_id",
            unique=True,
            postgresql_where=text("payment_reversal_id IS NOT NULL"),
            sqlite_where=text("payment_reversal_id IS NOT NULL"),
        ),
        Index(
            "uq_member_financial_entries_one_contribution_settlement_credit",
            "payment_settlement_id",
            unique=True,
            postgresql_where=text(
                "payment_settlement_id IS NOT NULL AND "
                "entry_type = 'CONTRIBUTION' AND direction = 'CREDIT'"
            ),
            sqlite_where=text(
                "payment_settlement_id IS NOT NULL AND "
                "entry_type = 'CONTRIBUTION' AND direction = 'CREDIT'"
            ),
        ),
    )
