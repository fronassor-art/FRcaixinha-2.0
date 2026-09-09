from datetime import datetime, timezone
import hashlib, json
from sqlalchemy.orm import Session
from app.models import User, Member, Contribution, Loan, LoanInstallment, CollectionAgreement, AgreementInstallment, Notification, PrivacyRequest, DataAccessLog, ConsentRecord, UserSession

def log_access(db: Session, actor_user_id: int | None, subject_user_id: int | None, action: str, resource: str, ip: str | None = None, user_agent: str | None = None):
    db.add(DataAccessLog(actor_user_id=actor_user_id, subject_user_id=subject_user_id, action=action, resource=resource, ip_address=ip, user_agent=user_agent))

def record_consent(db: Session, user_id: int, consent_type: str, version: str, granted: bool, source='APP', ip_address=None):
    row=ConsentRecord(user_id=user_id, consent_type=consent_type, version=version, granted=granted, source=source, ip_address=ip_address)
    db.add(row); return row

def export_user_data(db: Session, user_id: int):
    u=db.get(User,user_id)
    if not u: return None
    member=db.query(Member).filter(Member.user_id==user_id).first()
    out={"schema_version":"0.45","exported_at":datetime.now(timezone.utc).isoformat(),"user":{"id":u.id,"name":u.name,"email":u.email,"cpf":u.cpf,"phone":u.phone,"role":u.role,"accepted_terms_at":u.accepted_terms_at.isoformat() if u.accepted_terms_at else None,"created_at":u.created_at.isoformat()},"member":None,"contributions":[],"loans":[],"notifications":[],"consents":[]}
    if member:
        out["member"]={"id":member.id,"group_id":member.group_id,"status":member.status,"joined_at":member.joined_at.isoformat(),"declared_monthly_income":str(member.declared_monthly_income) if member.declared_monthly_income is not None else None}
        out["contributions"]= [{"id":c.id,"amount":str(c.amount),"status":c.status,"due_date":c.due_date.isoformat() if c.due_date else None,"paid_at":c.paid_at.isoformat() if c.paid_at else None} for c in db.query(Contribution).filter(Contribution.member_id==member.id).all()]
        loans=db.query(Loan).filter(Loan.member_id==member.id).all()
        for loan in loans:
            out["loans"].append({"id":loan.id,"status":loan.status,"principal":str(loan.principal),"amount":str(loan.amount),"created_at":loan.created_at.isoformat() if loan.created_at else None,"installments":[{"id":i.id,"number":i.number,"due_date":i.due_date.isoformat(),"amount":str(i.amount),"paid_amount":str(i.paid_amount),"penalty_amount":str(i.penalty_amount),"status":i.status,"paid_at":i.paid_at.isoformat() if i.paid_at else None} for i in db.query(LoanInstallment).filter(LoanInstallment.loan_id==loan.id).all()]})
    out["notifications"]= [{"id":n.id,"type":n.type,"title":n.title,"status":n.status,"read_at":n.read_at.isoformat() if n.read_at else None,"created_at":n.created_at.isoformat()} for n in db.query(Notification).filter(Notification.user_id==user_id).all()]
    out["consents"]= [{"id":c.id,"type":c.consent_type,"version":c.version,"granted":c.granted,"source":c.source,"created_at":c.created_at.isoformat()} for c in db.query(ConsentRecord).filter(ConsentRecord.user_id==user_id).all()]
    return out

def request_privacy(db: Session, user_id: int, request_type: str, reason: str | None):
    active=db.query(PrivacyRequest).filter(PrivacyRequest.user_id==user_id, PrivacyRequest.status.in_(["REQUESTED","APPROVED"]), PrivacyRequest.request_type==request_type).first()
    if active: return active, False
    row=PrivacyRequest(user_id=user_id,request_type=request_type,reason=reason)
    db.add(row); return row, True

def anonymize_user(db: Session, user_id: int, admin_id: int):
    u=db.get(User,user_id)
    if not u: return None
    marker=f"anon-{u.id}-{hashlib.sha256(f'{u.id}:{u.email}'.encode()).hexdigest()[:12]}"
    u.name="Usuário anonimizado"
    u.email=f"{marker}@anon.invalid"
    u.cpf=f"ANON-{u.id}-{hashlib.sha256(str(u.id).encode()).hexdigest()[:8]}"
    u.phone=None; u.is_active=False
    for s in db.query(UserSession).filter(UserSession.user_id==u.id, UserSession.revoked_at.is_(None)).all(): s.revoked_at=datetime.now(timezone.utc)
    m=db.query(Member).filter(Member.user_id==u.id).first()
    if m: m.status="INACTIVE"
    log_access(db,admin_id,user_id,"ANONYMIZE_USER","USER")
    return u
