from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import OperationalActionRecord, AuditLog

CATALOG={
 'LOAN_REQUESTS':('/admin/loans','Revisar solicitações de empréstimo','HIGH'),
 'SECURE_RELEASE_CONFIRM':('/admin/secure-release/history','Confirmar liberações seguras','HIGH'),
 'SECURE_RELEASE_EXPIRED':('/admin/secure-release/history','Revalidar autorizações expiradas','HIGH'),
 'DELINQUENCY':('/admin/collections','Tratar inadimplência','HIGH'),
 'RECOVERY_DUE':('/admin/collection-recovery/promises','Acompanhar promessas vencidas','HIGH'),
 'RECOVERY_CASES':('/admin/collection-recovery/cases','Acompanhar recuperação','MEDIUM'),
 'RISK_BLOCKED':('/admin/financial-risk/assessments','Revisar bloqueios de risco','HIGH'),
 'RECONCILIATION':('/admin/reconciliation','Executar reconciliação','CRITICAL'),
 'LEDGER_INTEGRITY':('/admin/ledger/integrity','Investigar integridade do Ledger','CRITICAL'),
 'WEBHOOK_BACKLOG':('/admin/finance','Processar pendências de webhook','MEDIUM'),
 'PAYMENT_PENDING':('/admin/finance','Verificar pagamentos pendentes','MEDIUM'),
 'GOVERNANCE_REVIEW':('/admin/integrated-governance/history','Revisar governanças','MEDIUM'),
 'COLLECTION_CASES':('/admin/collection-recovery/cases','Acompanhar casos de recuperação','MEDIUM'),
 'WORKFLOW_ESCALATION':('/admin/workflow-escalation/actions','Tratar escalonamentos de workflow','HIGH'),
}

def enrich(actions):
    out=[]
    for a in actions:
        route,title,default_severity=CATALOG.get(a['code'],('/admin/operational-control','Abrir centro operacional',a['severity']))
        x=dict(a); x.update({'action_route':route,'action_label':title,'requires_confirmation':a['severity']=='CRITICAL'})
        out.append(x)
    return out

def get_action(db:Session, code:str):
    if code not in CATALOG: raise ValueError('Ação operacional desconhecida.')
    return {'code':code,'route':CATALOG[code][0],'label':CATALOG[code][1],'default_severity':CATALOG[code][2],'requires_confirmation':CATALOG[code][2]=='CRITICAL'}

def acknowledge(db:Session, *, code:str, actor_id:int, note:str|None=None, snapshot_id:int|None=None):
    get_action(db,code)
    row=OperationalActionRecord(action_code=code,status='ACKNOWLEDGED',snapshot_id=snapshot_id,acknowledged_by=actor_id,acknowledged_at=datetime.now(timezone.utc),note=note)
    db.add(row)
    db.add(AuditLog(actor_user_id=actor_id,action='OPERATIONAL_ACTION_ACKNOWLEDGED',entity_type='OPERATIONAL_ACTION',entity_id=code,details=note or ''))
    db.flush(); return row
