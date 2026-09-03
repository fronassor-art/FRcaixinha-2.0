from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from app.api.deps import current_user, require_admin
from app.db.session import get_db
from app.models import User, Notification
from app.services.notifications_v12 import deliver_email_notification, queue_installment_reminders

router = APIRouter(prefix="/notifications", tags=["notifications"])

@router.get("")
def list_notifications(unread_only: bool = False, limit: int = Query(30, ge=1, le=100),
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return {"items": [{"id": n.id, "type": n.type, "title": n.title, "message": n.message,
                       "channel": n.channel, "status": n.status, "read": n.read_at is not None,
                       "reference_type": n.reference_type, "reference_id": n.reference_id,
                       "created_at": n.created_at.isoformat()} for n in rows]}

@router.post("/{notification_id}/read")
def mark_read(notification_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "Notificação não encontrada.")
    n.read_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": n.id, "read": True}

@router.post("/read-all")
def mark_all_read(user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    db.query(Notification).filter(Notification.user_id == user.id, Notification.read_at.is_(None)).update({"read_at": now}, synchronize_session=False)
    db.commit()
    return {"ok": True}

@router.get("/admin")
def admin_notifications(status: str | None = None, limit: int = Query(100, ge=1, le=500),
                        admin=Depends(require_admin), db: Session = Depends(get_db)):
    q = db.query(Notification)
    if status:
        q = q.filter(Notification.status == status.upper())
    rows = q.order_by(Notification.created_at.desc()).limit(limit).all()
    return {"items": [{"id": n.id, "user_id": n.user_id, "type": n.type, "title": n.title,
                       "channel": n.channel, "status": n.status, "attempts": n.attempts,
                       "last_error": n.last_error, "created_at": n.created_at.isoformat()} for n in rows]}

@router.post("/admin/{notification_id}/retry")
def retry_email(notification_id: int, background_tasks: BackgroundTasks,
                admin=Depends(require_admin), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n:
        raise HTTPException(404, "Notificação não encontrada.")
    if n.channel not in {"EMAIL", "BOTH"}:
        raise HTTPException(400, "Notificação não possui canal de e-mail.")
    n.status = "PENDING"
    n.last_error = None
    db.commit()
    background_tasks.add_task(deliver_email_notification, n.id)
    return {"id": n.id, "status": "PENDING"}


@router.post("/admin/run-reminders")
def run_reminders(days_ahead: int = Query(3, ge=0, le=30), admin=Depends(require_admin), db: Session = Depends(get_db)):
    created = queue_installment_reminders(db, days_ahead)
    db.commit()
    return {"created": created, "days_ahead": days_ahead}
