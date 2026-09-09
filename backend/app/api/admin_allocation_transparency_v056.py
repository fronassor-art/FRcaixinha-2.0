from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import AllocationTransparencySnapshot
from app.services.allocation_transparency_v056 import explanation_hash

router=APIRouter(prefix='/admin/allocation-transparency', tags=['allocation-transparency-v056'])

def _row(r):
    return {'id':r.id,'resource_allocation_snapshot_id':r.resource_allocation_snapshot_id,'group_id':r.group_id,'policy_version':r.policy_version,'explanation_hash':r.explanation_hash,'created_at':r.created_at.isoformat()}

@router.get('/history')
def history(group_id:int|None=None, limit:int=Query(30,ge=1,le=200), admin=Depends(require_admin), db:Session=Depends(get_db)):
    q=db.query(AllocationTransparencySnapshot).order_by(AllocationTransparencySnapshot.created_at.desc())
    if group_id is not None: q=q.filter(AllocationTransparencySnapshot.group_id==group_id)
    return {'items':[_row(r) for r in q.limit(limit).all()]}

@router.get('/member/{member_id}')
def member_explanations(member_id:int, group_id:int|None=None, limit:int=Query(30,ge=1,le=200), admin=Depends(require_admin), db:Session=Depends(get_db)):
    q=db.query(AllocationTransparencySnapshot).order_by(AllocationTransparencySnapshot.created_at.desc())
    if group_id is not None: q=q.filter(AllocationTransparencySnapshot.group_id==group_id)
    rows=[]
    for r in q.limit(200).all():
        data=json.loads(r.explanation_json)
        matches=[x for x in data.get('items',[]) if x.get('member_id')==member_id]
        if matches:
            rows.append({'metadata':_row(r),'policy_snapshot':json.loads(r.policy_snapshot_json),'member':matches[0]})
            if len(rows)>=limit: break
    return {'items':rows}

@router.get('/{snapshot_id}')
def detail(snapshot_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=db.get(AllocationTransparencySnapshot, snapshot_id)
    if not r: raise HTTPException(404, 'Explicação de transparência não encontrada.')
    data=json.loads(r.explanation_json)
    if explanation_hash(data) != r.explanation_hash: raise HTTPException(409, 'Integridade da explicação comprometida.')
    return {'metadata':_row(r),'policy_snapshot':json.loads(r.policy_snapshot_json),'inputs':json.loads(r.input_snapshot_json),'explanation':data}
