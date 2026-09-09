from datetime import datetime, timezone
from email.message import EmailMessage
import smtplib
from sqlalchemy.orm import Session
from app.models import Notification, User, NotificationPreference, NotificationDelivery
from app.core.config import settings
from app.db.session import SessionLocal


def _now():
    return datetime.now(timezone.utc)


def create_notification(db: Session, user_id: int, ntype: str, title: str, message: str,
                         reference_type: str | None = None, reference_id: str | None = None,
                         channel: str = "IN_APP") -> Notification:
    n = Notification(user_id=user_id, type=ntype, title=title, message=message,
                     reference_type=reference_type, reference_id=reference_id, channel=channel)
    db.add(n)
    db.flush()
    return n


def send_email(user: User, subject: str, body: str) -> None:
    """Best-effort SMTP sender. Disabled unless SMTP_HOST is configured."""
    if not settings.smtp_host or not user.email:
        raise RuntimeError("SMTP não configurado")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = user.email
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)


def deliver_email_notification(notification_id: int) -> bool:
    db = SessionLocal()
    n = db.get(Notification, notification_id)
    if not n or n.channel not in {"EMAIL", "BOTH"} or n.status == "SENT":
        db.close()
        return False
    user = db.get(User, n.user_id)
    if not user:
        db.close()
        return False
    n.attempts += 1
    try:
        send_email(user, n.title, n.message)
        n.status = "SENT"
        n.sent_at = _now()
        n.last_error = None
        record_delivery(db, n.id, "EMAIL", "SENT", n.attempts)
        db.commit()
        db.close()
        return True
    except Exception as exc:
        n.status = "FAILED"
        n.last_error = str(exc)[:1000]
        record_delivery(db, n.id, "EMAIL", "FAILED", n.attempts, str(exc)[:1000])
        db.commit()
        db.close()
        return False


def queue_installment_reminders(db: Session, days_ahead: int = 3) -> int:
    from datetime import date, timedelta
    from app.models import LoanInstallment, Loan, Member
    today = date.today()
    limit = today + timedelta(days=days_ahead)
    rows = db.query(LoanInstallment).filter(LoanInstallment.status != "PAID", LoanInstallment.due_date <= limit).all()
    created = 0
    for item in rows:
        loan = db.get(Loan, item.loan_id)
        if not loan:
            continue
        member = db.get(Member, loan.member_id)
        if not member:
            continue
        ntype = "INSTALLMENT_OVERDUE" if item.due_date < today else "INSTALLMENT_DUE"
        exists = db.query(Notification).filter(Notification.user_id == member.user_id, Notification.type == ntype,
            Notification.reference_type == "LOAN_INSTALLMENT", Notification.reference_id == str(item.id)).first()
        if exists:
            continue
        title = "Parcela em atraso" if item.due_date < today else "Parcela próxima do vencimento"
        message = (f"A parcela {item.number} do empréstimo #{loan.id} venceu em {item.due_date.isoformat()}."
                   if item.due_date < today else
                   f"A parcela {item.number} do empréstimo #{loan.id} vence em {item.due_date.isoformat()}.")
        create_notification(db, member.user_id, ntype, title, message, "LOAN_INSTALLMENT", str(item.id))
        created += 1
    return created

# v0.44 communication center helpers
from datetime import datetime as _dt
from app.models import NotificationPreference, NotificationDelivery

CATEGORY_MAP = {
    "PAYMENT": "payment_alerts", "CONTRIBUTION": "payment_alerts",
    "LOAN": "loan_alerts", "INSTALLMENT": "loan_alerts",
    "COLLECTION": "collection_alerts", "ACCOUNT": "account_alerts",
}

def _category_enabled(pref: NotificationPreference, ntype: str) -> bool:
    upper = (ntype or "").upper()
    for key, attr in CATEGORY_MAP.items():
        if key in upper:
            return bool(getattr(pref, attr))
    return True

def get_or_create_preferences(db: Session, user_id: int) -> NotificationPreference:
    pref = db.query(NotificationPreference).filter(NotificationPreference.user_id == user_id).first()
    if not pref:
        pref = NotificationPreference(user_id=user_id)
        db.add(pref); db.flush()
    return pref

def create_communication(db: Session, user_id: int, ntype: str, title: str, message: str,
                         reference_type: str | None = None, reference_id: str | None = None,
                         channel: str = "IN_APP") -> Notification | None:
    pref = get_or_create_preferences(db, user_id)
    if not _category_enabled(pref, ntype):
        return None
    requested = channel.upper()
    if requested == "EMAIL" and not pref.email_enabled:
        return None
    if requested == "IN_APP" and not pref.in_app_enabled:
        return None
    if requested == "BOTH" and not (pref.in_app_enabled or pref.email_enabled):
        return None
    actual = requested
    if requested == "BOTH":
        if pref.in_app_enabled and pref.email_enabled: actual = "BOTH"
        elif pref.email_enabled: actual = "EMAIL"
        else: actual = "IN_APP"
    return create_notification(db, user_id, ntype, title, message, reference_type, reference_id, actual)

def record_delivery(db: Session, notification_id: int, channel: str, status: str, attempt: int = 1, error: str | None = None):
    row = NotificationDelivery(notification_id=notification_id, channel=channel, status=status,
                                attempt=attempt, error=error, sent_at=_now() if status == "SENT" else None)
    db.add(row)
    db.flush()
    return row
