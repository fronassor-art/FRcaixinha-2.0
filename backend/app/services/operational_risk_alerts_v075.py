from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import OperationalRiskAlert, OperationalRiskTrendSnapshot, AuditLog

def utcnow(): return datetime.now(timezone.utc)
def canonical(v): return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def fingerprint(alert_type, snapshot_date): return hashlib.sha256(f'{alert_type}:{snapshot_date}'.encode()).hexdigest()

def _derive(data):
    score=int(data.get('risk_score',0)); cur=data.get('current',{}); trends={x['metric']:x for x in data.get('trends',[])}
    alerts=[]
    rising=sum(1 for k in ('critical_incidents','recurrences','ineffective_reviews') if trends.get(k,{}).get('direction')=='UP')
    if score>=70 or cur.get('critical_incidents',0)>=2:
        alerts.append(('CRITICAL_RISK','CRITICAL',70,'Risco operacional crítico','Score/volume crítico identificado.','Abrir ou priorizar incidente e CAPA para análise humana.'))
    elif score>=30 or rising>=2:
        alerts.append(('RISK_TREND','ATTENTION',30,'Tendência de risco operacional','Indicadores de risco estão elevados ou em alta.','Designar responsável e acompanhar os controles que impulsionaram a tendência.'))
    if cur.get('recurrences',0)>=2 and trends.get('recurrences',{}).get('direction')=='UP':
        alerts.append(('RECURRENCE_TREND','HIGH',30,'Reincidência em crescimento','Reincidências aumentaram na janela atual.','Revisar eficácia das CAPAs e avaliar reabertura/análise de causa.'))
    if cur.get('ineffective_reviews',0)>=2 and trends.get('ineffective_reviews',{}).get('direction')=='UP':
        alerts.append(('CAPA_EFFECTIVENESS','HIGH',20,'Efetividade de CAPA em deterioração','Revisões com baixa efetividade estão aumentando.','Reavaliar critérios de eficácia e ações corretivas pendentes.'))
    return alerts

def sync_alerts(db: Session, actor_id=None, now=None):
    now=now or utcnow(); snap=db.query(OperationalRiskTrendSnapshot).order_by(OperationalRiskTrendSnapshot.snapshot_date.desc()).first()
    if not snap: return {'created':0,'updated':0,'items':[]}
    data=json.loads(snap.snapshot_json); created=updated=0; items=[]
    for alert_type,severity,threshold,title,description,action in _derive(data):
        fp=fingerprint(alert_type,snap.snapshot_date.isoformat())
        row=db.query(OperationalRiskAlert).filter_by(fingerprint=fp).first()
        if not row:
            row=OperationalRiskAlert(fingerprint=fp,alert_type=alert_type,severity=severity,status='OPEN',risk_score=int(data.get('risk_score',0)),threshold=threshold,title=title,description=description,recommended_action=action,source_snapshot_id=snap.id,created_at=now,updated_at=now)
            db.add(row); db.flush(); created+=1
            db.add(AuditLog(actor_user_id=actor_id,action='OPERATIONAL_RISK_ALERT_CREATED',entity_type='OperationalRiskAlert',entity_id=str(row.id),details=f'type={alert_type};severity={severity}'))
        else:
            row.risk_score=int(data.get('risk_score',0)); row.source_snapshot_id=snap.id; row.updated_at=now; updated+=1
        items.append({'id':row.id,'type':alert_type,'severity':severity,'status':row.status,'risk_score':row.risk_score})
    return {'created':created,'updated':updated,'items':items}

def list_alerts(db: Session, status=None, limit=100):
    q=db.query(OperationalRiskAlert).order_by(OperationalRiskAlert.created_at.desc())
    if status: q=q.filter(OperationalRiskAlert.status==status)
    return q.limit(limit).all()

def acknowledge_alert(db: Session, alert, actor_id, now=None):
    if alert.status=='RESOLVED': raise ValueError('Alerta já resolvido.')
    now=now or utcnow(); alert.status='ACKNOWLEDGED'; alert.acknowledged_by=actor_id; alert.acknowledged_at=now; alert.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='OPERATIONAL_RISK_ALERT_ACKNOWLEDGED',entity_type='OperationalRiskAlert',entity_id=str(alert.id),details='Alerta reconhecido.'))
    return alert

def resolve_alert(db: Session, alert, actor_id, now=None):
    now=now or utcnow(); alert.status='RESOLVED'; alert.resolved_at=now; alert.updated_at=now
    db.add(AuditLog(actor_user_id=actor_id,action='OPERATIONAL_RISK_ALERT_RESOLVED',entity_type='OperationalRiskAlert',entity_id=str(alert.id),details='Alerta resolvido manualmente.'))
    return alert
