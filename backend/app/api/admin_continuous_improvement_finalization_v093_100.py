from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.services.continuous_improvement_finalization_v093_100 import *
router=APIRouter(prefix='/admin/continuous-improvement-finalization',tags=['continuous-improvement-v093-v100'])
@router.get('/dashboard')
def dashboard(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_dashboard(db)
@router.get('/action-queue')
def action_queue(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_queue(db)
@router.get('/kpi')
def kpi(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_kpi(db)
@router.get('/sla')
def sla(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_sla(db)
@router.get('/compliance')
def compliance(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_compliance(db)
@router.get('/export')
def export_json(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_export(db)
@router.get('/export.csv',response_class=PlainTextResponse)
def export_csv_endpoint(admin=Depends(require_admin),db:Session=Depends(get_db)): return export_csv(db)
@router.get('/production-readiness')
def readiness(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_readiness(db)
@router.get('/release')
def release(admin=Depends(require_admin),db:Session=Depends(get_db)): return build_release(db)
@router.post('/sync')
def sync(admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=persist_all(db,admin.id); db.commit(); return rows
