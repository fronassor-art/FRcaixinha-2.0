from __future__ import annotations
import hashlib, json
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import (OperationalControlSnapshot, Loan, LoanInstallment, Contribution,
    SecureReleaseAuthorization, FinancialRiskAssessment, FinancialReconciliation, Payment,
    WebhookEvent, CollectionCase, PaymentPromise, IntegratedGovernanceSnapshot)
from app.services.collections_v038 import collections_summary
from app.services.collection_recovery_v049 import collection_recovery_summary
from app.services.ledger import verify_ledger_chain
from app.services.executive_dashboard_v050 import build_executive_dashboard


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def sha(data): return hashlib.sha256(canonical(data).encode()).hexdigest()

def build_operational_control(db: Session, today: date | None = None):
    today = today or date.today()
    now = datetime.now(timezone.utc)
    actions = []
    def action(code, severity, title, count, detail):
        if count:
            actions.append({'code':code,'severity':severity,'title':title,'count':int(count),'detail':detail})

    pending_requests = db.query(Loan).filter(Loan.status=='REQUESTED').count()
    action('LOAN_REQUESTS','HIGH','Solicitações de empréstimo aguardando decisão',pending_requests,'Revisar governança e política de crédito antes da decisão.')
    releases = db.query(SecureReleaseAuthorization).filter(SecureReleaseAuthorization.status=='AUTHORIZED').count()
    action('SECURE_RELEASE_CONFIRM','HIGH','Liberações aguardando segunda confirmação',releases,'A segunda confirmação deve ser feita por administrador diferente.')
    expiring = db.query(SecureReleaseAuthorization).filter(SecureReleaseAuthorization.status=='AUTHORIZED', SecureReleaseAuthorization.expires_at <= now).count()
    action('SECURE_RELEASE_EXPIRED','HIGH','Autorizações de liberação expiradas',expiring,'Revalidar a governança antes de autorizar novamente.')
    overdue = collections_summary(db,today)
    action('DELINQUENCY','HIGH','Parcelas vencidas',overdue.get('overdue_installments',0),f"Saldo vencido: R$ {overdue.get('overdue_balance','0.00')}")
    recovery = collection_recovery_summary(db)
    action('RECOVERY_DUE','HIGH','Promessas de pagamento vencidas ou para hoje',recovery.get('promises_due_or_late',0),'Executar acompanhamento de recuperação.')
    action('RECOVERY_CASES','MEDIUM','Casos de recuperação abertos',recovery.get('open_cases',0),'Revisar casos e próximos passos de cobrança.')
    blocked = db.query(FinancialRiskAssessment).filter(FinancialRiskAssessment.status=='BLOCKED').count()
    action('RISK_BLOCKED','HIGH','Avaliações de risco bloqueadas',blocked,'Não liberar sem nova avaliação ou exceção formal.')
    recon = db.query(FinancialReconciliation).order_by(FinancialReconciliation.created_at.desc()).first()
    recon_bad = 1 if (not recon or recon.status!='PASS') else 0
    action('RECONCILIATION','CRITICAL','Reconciliação financeira não está PASS',recon_bad,'Executar reconciliação antes de fechamento/liberação crítica.')
    ledger = verify_ledger_chain(db)
    ledger_bad = 1 if ledger.get('status') != 'PASS' else 0
    action('LEDGER_INTEGRITY','CRITICAL','Integridade do Ledger requer atenção',ledger_bad,'Interromper operações críticas e investigar a cadeia.')
    pending_webhooks = db.query(WebhookEvent).filter(WebhookEvent.processed==False).count()
    action('WEBHOOK_BACKLOG','MEDIUM','Webhooks pendentes de processamento',pending_webhooks,'Verificar fila/idempotência do provedor.')
    payment_pending = db.query(Payment).filter(Payment.status=='PENDING').count()
    action('PAYMENT_PENDING','MEDIUM','Pagamentos pendentes',payment_pending,'Verificar confirmação e conciliação com o provedor.')
    gov_review = db.query(IntegratedGovernanceSnapshot).filter(IntegratedGovernanceSnapshot.final_decision=='REVIEW').count()
    action('GOVERNANCE_REVIEW','MEDIUM','Governanças integradas em revisão',gov_review,'Revisar capacidade, risco e alocação antes da decisão.')
    action('COLLECTION_CASES','MEDIUM','Casos de recuperação abertos',recovery.get('open_cases',0),'Acompanhar carteira de recuperação.')

    severity_rank={'CRITICAL':0,'HIGH':1,'MEDIUM':2,'LOW':3}
    actions.sort(key=lambda x:(severity_rank[x['severity']], -x['count'], x['code']))
    status='CRITICAL' if any(a['severity']=='CRITICAL' for a in actions) else 'ATTENTION' if actions else 'PASS'
    executive=build_executive_dashboard(db,today)
    return {'schema':'v0.60','snapshot_date':today.isoformat(),'status':status,
            'summary':{'action_count':len(actions),'critical':sum(a['severity']=='CRITICAL' for a in actions),'high':sum(a['severity']=='HIGH' for a in actions),'medium':sum(a['severity']=='MEDIUM' for a in actions)},
            'actions':actions,'executive_dashboard':executive,
            'controls':{'ledger_integrity':ledger,'reconciliation':None if not recon else {'id':recon.id,'status':recon.status,'competence':recon.competence.isoformat(),'snapshot_hash':recon.snapshot_hash},'pending_webhooks':pending_webhooks,'pending_payments':payment_pending},
            'note':'Centro operacional é ferramenta de monitoramento; não altera registros financeiros nem libera operações automaticamente.'}

def persist_operational_control(db: Session, actor_id: int | None = None, today: date | None = None):
    data=build_operational_control(db,today)
    raw=canonical(data); h=hashlib.sha256(raw.encode()).hexdigest()
    row=db.query(OperationalControlSnapshot).filter(OperationalControlSnapshot.snapshot_date==date.fromisoformat(data['snapshot_date'])).first()
    if row:
        row.status=data['status']; row.action_count=data['summary']['action_count']; row.snapshot_json=raw; row.snapshot_hash=h; row.generated_by=actor_id
    else:
        row=OperationalControlSnapshot(snapshot_date=date.fromisoformat(data['snapshot_date']),status=data['status'],action_count=data['summary']['action_count'],snapshot_json=raw,snapshot_hash=h,generated_by=actor_id); db.add(row)
    db.flush(); return row,data
