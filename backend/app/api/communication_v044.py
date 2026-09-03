from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.deps import current_user, require_admin
from app.db.session import get_db
from app.models import User, Member, Notification, NotificationPreference, NotificationDelivery
from app.services.notifications_v12 import get_or_create_preferences, create_communication, deliver_email_notification

router = APIRouter(prefix="/communications", tags=["communications"])

class PreferenceIn(BaseModel):
    in_app_enabled: bool = True
    email_enabled: bool = False
    payment_alerts: bool = True
    loan_alerts: bool = True
    collection_alerts: bool = True
    account_alerts: bool = True

class BroadcastIn(BaseModel):
    audience: str = Field("ALL", pattern="^(ALL|GROUP)$")
    group_id: int | None = None
    ntype: str = Field(..., min_length=1, max_length=60)
    title: str = Field(..., min_length=1, max_length=180)
    message: str = Field(..., min_length=1, max_length=5000)
    channel: str = Field("IN_APP", pattern="^(IN_APP|EMAIL|BOTH)$")

def _prefs_dict(p):
    return {"in_app_enabled": p.in_app_enabled, "email_enabled": p.email_enabled,
            "payment_alerts": p.payment_alerts, "loan_alerts": p.loan_alerts,
            "collection_alerts": p.collection_alerts, "account_alerts": p.account_alerts,
            "updated_at": p.updated_at.isoformat()}

@router.get("/preferences")
def get_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _prefs_dict(get_or_create_preferences(db, user.id))

@router.put("/preferences")
def update_preferences(body: PreferenceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_or_create_preferences(db, user.id)
    for k, v in body.model_dump().items(): setattr(p, k, v)
    p.updated_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(p)
    return _prefs_dict(p)

@router.get("/summary")
def notification_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    total = db.query(Notification).filter(Notification.user_id == user.id).count()
    unread = db.query(Notification).filter(Notification.user_id == user.id, Notification.read_at.is_(None)).count()
    failed = db.query(Notification).filter(Notification.user_id == user.id, Notification.status == "FAILED").count()
    return {"total": total, "unread": unread, "failed": failed}

@router.post("/admin/broadcast")
def broadcast(body: BroadcastIn, background_tasks: BackgroundTasks, admin=Depends(require_admin), db: Session = Depends(get_db)):
    if body.audience == "GROUP" and body.group_id is None:
        raise HTTPException(400, "group_id é obrigatório para audiência GROUP.")
    q = db.query(Member).join(User, User.id == Member.user_id).filter(User.is_active.is_(True))
    if body.audience == "GROUP": q = q.filter(Member.group_id == body.group_id)
    members = q.all()
    created, skipped = 0, 0
    for member in members:
        n = create_communication(db, member.user_id, body.ntype, body.title, body.message,
                                 "COMMUNICATION", None, body.channel)
        if n:
            created += 1
            if n.channel in {"EMAIL", "BOTH"}:
                background_tasks.add_task(deliver_email_notification, n.id)
        else:
            skipped += 1
    db.commit()
    return {"created": created, "skipped_by_preferences": skipped, "audience": body.audience,
            "group_id": body.group_id}

@router.get("/admin/deliveries")
def admin_deliveries(status: str | None = None, limit: int = Query(100, ge=1, le=500),
                     admin=Depends(require_admin), db: Session = Depends(get_db)):
    q = db.query(NotificationDelivery)
    if status: q = q.filter(NotificationDelivery.status == status.upper())
    rows = q.order_by(NotificationDelivery.created_at.desc()).limit(limit).all()
    return {"items": [{"id": r.id, "notification_id": r.notification_id, "channel": r.channel,
                        "attempt": r.attempt, "status": r.status, "error": r.error,
                        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                        "created_at": r.created_at.isoformat()} for r in rows]}
