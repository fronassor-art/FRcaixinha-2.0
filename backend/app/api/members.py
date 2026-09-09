from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import User, Member, Group, Quota
from app.api.deps import current_user, require_admin

router = APIRouter(prefix="/members", tags=["members"])

@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    member = db.query(Member).filter(Member.user_id == user.id).first()
    return {
        "id": user.id, "name": user.name, "email": user.email,
        "role": user.role,
        "member": None if not member else {
            "id": member.id, "group_id": member.group_id, "status": member.status,
            "quota_units": str(member.quota.units) if member.quota else None
        }
    }

@router.post("/assign/{user_id}/{group_id}")
def assign_member(user_id: int, group_id: int, admin=Depends(require_admin), db: Session=Depends(get_db)):
    user = db.get(User, user_id); group = db.get(Group, group_id)
    if not user or not group:
        from fastapi import HTTPException
        raise HTTPException(404, "Usuário ou grupo não encontrado.")
    existing = db.query(Member).filter(Member.user_id == user_id).first()
    if existing:
        raise HTTPException(409, "Usuário já é membro.")
    member = Member(user_id=user_id, group_id=group_id)
    db.add(member); db.flush()
    db.add(Quota(member_id=member.id, units=1))
    db.commit()
    return {"member_id": member.id, "status": "ACTIVE"}

@router.get("/me/statement")
def my_statement(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.services.reports_v10 import member_statement
    member = db.query(Member).filter(Member.user_id == user.id).first()
    if not member:
        from fastapi import HTTPException
        raise HTTPException(404, "Participante não encontrado.")
    result = member_statement(db, member.id)
    if not result:
        from fastapi import HTTPException
        raise HTTPException(404, "Extrato não encontrado.")
    return result
