from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import SecureReleaseAuthorization
from app.services.secure_release_v059 import create_authorization, confirm_and_release

router = APIRouter(prefix='/admin/secure-release', tags=['secure-release-v059'])

def serialize(r):
    return {'id': r.id, 'loan_id': r.loan_id, 'group_id': r.group_id, 'status': r.status,
            'governance_hash': r.governance_hash, 'authorized_by': r.authorized_by,
            'authorized_at': r.authorized_at.isoformat() if r.authorized_at else None,
            'expires_at': r.expires_at.isoformat() if r.expires_at else None,
            'confirmed_by': r.confirmed_by, 'confirmed_at': r.confirmed_at.isoformat() if r.confirmed_at else None,
            'confirmation_count': r.confirmation_count, 'executed_at': r.executed_at.isoformat() if r.executed_at else None,
            'execution_hash': r.execution_hash}

@router.post('/loans/{loan_id}/authorize')
def authorize(loan_id: int, horizon_months: int = Query(12, ge=1, le=36), scenario: str = Query('BASE'), admin=Depends(require_admin), db: Session=Depends(get_db)):
    try:
        row, result = create_authorization(db, loan_id=loan_id, actor_id=admin.id, horizon_months=horizon_months, scenario=scenario)
        db.commit(); db.refresh(row)
        return {'authorization': serialize(row), 'governance': result}
    except ValueError as e:
        db.rollback(); detail = e.args[0] if e.args else str(e)
        raise HTTPException(409 if isinstance(detail, dict) else 400, detail=detail)

@router.post('/{authorization_id}/confirm')
def confirm(authorization_id: int, admin=Depends(require_admin), db: Session=Depends(get_db)):
    try:
        row, loan, current = confirm_and_release(db, authorization_id=authorization_id, actor_id=admin.id)
        db.commit(); db.refresh(row)
        return {'authorization': serialize(row), 'loan': {'id': loan.id, 'status': loan.status, 'disbursed_at': loan.disbursed_at.isoformat() if loan.disbursed_at else None}, 'governance': current}
    except ValueError as e:
        db.rollback(); detail = e.args[0] if e.args else str(e)
        raise HTTPException(409 if isinstance(detail, dict) else 400, detail=detail)

@router.get('/history')
def history(status: str | None = None, loan_id: int | None = None, limit: int = Query(50, ge=1, le=200), admin=Depends(require_admin), db: Session=Depends(get_db)):
    q = db.query(SecureReleaseAuthorization).order_by(SecureReleaseAuthorization.created_at.desc())
    if status: q = q.filter(SecureReleaseAuthorization.status == status.upper())
    if loan_id is not None: q = q.filter(SecureReleaseAuthorization.loan_id == loan_id)
    return {'items': [serialize(r) for r in q.limit(limit).all()]}

@router.get('/{authorization_id}')
def detail(authorization_id: int, admin=Depends(require_admin), db: Session=Depends(get_db)):
    r = db.get(SecureReleaseAuthorization, authorization_id)
    if not r: raise HTTPException(404, 'Autorização não encontrada.')
    return {'authorization': serialize(r), 'governance': json.loads(r.governance_snapshot)}
