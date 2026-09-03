from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import OperationalRiskAlert
from app.services.operational_risk_alerts_v075 import sync_alerts, list_alerts, acknowledge_alert, resolve_alert
router=APIRouter(prefix='/admin/operational-risk-alerts',tags=['operational-risk-alerts-v075'])
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    out=sync_alerts(db,admin.id); db.commit(); return out
@router.get('')
def current(status:str|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=list_alerts(db,status); return {'items':[{'id':r.id,'type':r.alert_type,'severity':r.severity,'status':r.status,'risk_score':r.risk_score,'threshold':r.threshold,'title':r.title,'description':r.description,'recommended_action':r.recommended_action,'created_at':r.created_at.isoformat()} for r in rows]}
@router.post('/{alert_id}/acknowledge')
def acknowledge(alert_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(OperationalRiskAlert,alert_id)
    if not row: raise HTTPException(404,'Alerta não encontrado.')
    acknowledge_alert(db,row,admin.id); db.commit(); return {'id':row.id,'status':row.status}
@router.post('/{alert_id}/resolve')
def resolve(alert_id:int,admin=Depends(require_admin),db:Session=Depends(get_db)):
    row=db.get(OperationalRiskAlert,alert_id)
    if not row: raise HTTPException(404,'Alerta não encontrado.')
    resolve_alert(db,row,admin.id); db.commit(); return {'id':row.id,'status':row.status}
