from datetime import datetime, date, timezone
from decimal import Decimal
from sqlalchemy import String, Integer, Boolean, DateTime, Date, Numeric, ForeignKey, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import event
from app.db.base import Base

def now_utc():
    return datetime.now(timezone.utc)

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
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    member: Mapped["Member | None"] = relationship(back_populates="user", uselist=False)


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
    max_installments: Mapped[int] = mapped_column(Integer, default=12)
    grace_days: Mapped[int] = mapped_column(Integer, default=0)
    min_on_time_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6,5), nullable=True)
    max_overdue_installments: Mapped[int] = mapped_column(Integer, default=0)
    max_installment_income_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6,5), nullable=True)
    max_quota_multiple: Mapped[Decimal | None] = mapped_column(Numeric(10,2), nullable=True)
    # v0.48: explicit per-loan limits used by the unified approval engine.
    max_loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(14,2), nullable=True)
    max_loan_income_multiple: Mapped[Decimal | None] = mapped_column(Numeric(8,3), nullable=True)

class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    declared_monthly_income: Mapped[Decimal | None] = mapped_column(Numeric(14,2), nullable=True)
    user: Mapped[User] = relationship(back_populates="member")
    quota: Mapped["Quota | None"] = relationship(back_populates="member", uselist=False)

class Quota(Base):
    __tablename__ = "quotas"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), unique=True)
    units: Mapped[Decimal] = mapped_column(Numeric(14,4), default=Decimal("1"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    member: Mapped[Member] = relationship(back_populates="quota")

class Contribution(Base):
    __tablename__ = "contributions"
    id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    competence: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("member_id", "competence", name="uq_contribution_member_competence"),)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_payment_id: Mapped[str] = mapped_column(String(150))
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14,2))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    raw_status: Mapped[str | None] = mapped_column(String(80))
    ledger_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_type: Mapped[str | None] = mapped_column(String(50), index=True)
    reference_id: Mapped[str | None] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("provider", "provider_payment_id", name="uq_provider_payment"),)

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
    principal: Mapped[Decimal] = mapped_column(Numeric(14,2))
    monthly_rate: Mapped[Decimal] = mapped_column(Numeric(8,5))
    installments: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="REQUESTED")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    collection_stage: Mapped[str] = mapped_column(String(20), default="NORMAL", index=True)
    last_collection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_attempts: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("loan_id", "number", name="uq_loan_installment_number"),)

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
    __table_args__ = (UniqueConstraint("loan_id", "status", name="uq_agreement_loan_status"), Index("ix_agreements_member_status", "member_id", "status"))

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
    installment_id: Mapped[int] = mapped_column(ForeignKey("loan_installments.id"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"))
    event_type: Mapped[str] = mapped_column(String(50))
    event_date: Mapped[date] = mapped_column(Date)
    channel: Mapped[str] = mapped_column(String(20), default="IN_APP")
    notification_id: Mapped[int | None] = mapped_column(ForeignKey("notifications.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("installment_id", "event_type", "event_date", name="uq_collection_event_day"), Index("ix_collection_events_member_date", "member_id", "event_date"))

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
    for obj in list(session.dirty):
        if isinstance(obj, LedgerEntry):
            raise RuntimeError("LedgerEntry é imutável; use uma reversão controlada.")
    for obj in list(session.deleted):
        if isinstance(obj, LedgerEntry):
            raise RuntimeError("LedgerEntry não pode ser excluído.")

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
    status: Mapped[str] = mapped_column(String(20), default='OPEN', index=True)
    stage: Mapped[str] = mapped_column(String(30), default='SOFT')
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    resolution_note: Mapped[str | None] = mapped_column(Text())

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
