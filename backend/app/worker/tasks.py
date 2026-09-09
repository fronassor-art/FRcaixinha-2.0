import logging
from datetime import date
from app.db.session import SessionLocal
from app.services.notifications_v12 import queue_installment_reminders
from app.services.loan_engine_v17 import accrue_overdue_penalties
from app.core.config import settings
from app.services.collections_v038 import run_collection_cycle
from app.services.collection_recovery_v049 import sync_cases
from app.services.executive_dashboard_v050 import persist_executive_dashboard
from app.services.workflow_escalation_v064 import sync_workflow_escalations
from app.services.workflow_orchestration_v065 import sync_workflow_orchestration
from app.services.workflow_execution_v066 import sync_execution_states
from app.services.workflow_evidence_integrity_v069 import verify_all
from app.services.workflow_compliance_v070 import persist_compliance_snapshot
from app.services.workflow_incidents_v071 import sync_incidents
from app.services.capa_effectiveness_v073 import sync_capa_recurrence
from app.services.operational_risk_v074 import persist_risk_snapshot
from app.services.operational_risk_alerts_v075 import sync_alerts
from app.services.operational_risk_response_v076 import sync_response_plans
from app.services.executive_risk_response_v077 import persist_dashboard
from app.services.executive_risk_governance_v079 import build_governance
from app.services.executive_risk_execution_v080 import create_execution
from app.services.executive_risk_effectiveness_v081 import create as create_effectiveness
from app.services.continuous_improvement_v082 import analyze as analyze_improvement
from app.services.continuous_improvement_v083 import create_plan as create_improvement_plan
from app.services.continuous_improvement_dashboard_v084 import persist_dashboard as persist_improvement_dashboard
from app.services.continuous_improvement_priority_v085 import persist as persist_improvement_priority
from app.services.continuous_improvement_balancing_v086 import persist as persist_improvement_balancing
from app.services.continuous_improvement_execution_v088 import create_from_decision as create_improvement_execution
from app.services.continuous_improvement_certification_v090 import certify as certify_improvement
from app.services.continuous_improvement_audit_v091 import persist as persist_improvement_audit
from app.services.continuous_improvement_executive_audit_v092 import persist_report as persist_executive_improvement_audit
from app.services.continuous_improvement_finalization_v093_100 import persist_all as persist_finalization
from app.models import ExecutiveRiskDecisionGovernance, ExecutiveRiskDecisionExecution

log = logging.getLogger(__name__)

def run_daily_tasks():
    db = SessionLocal()
    try:
        created = queue_installment_reminders(db, days_ahead=3)
        penalties = accrue_overdue_penalties(db, date.today(), settings.loan_daily_penalty_rate)
        collections = run_collection_cycle(db, date.today())
        recovery = sync_cases(db, date.today())
        workflow_escalation = sync_workflow_escalations(db, actor_id=None)
        workflow_orchestration = sync_workflow_orchestration(db, actor_id=None)
        workflow_execution = sync_execution_states(db, actor_id=None)
        workflow_integrity = verify_all(db, actor_id=None)
        workflow_incidents = sync_incidents(db, actor_id=None)
        capa_effectiveness = sync_capa_recurrence(db, actor_id=None)
        operational_risk_row, operational_risk = persist_risk_snapshot(db, generated_by=None, snapshot_date=date.today())
        operational_risk_alerts = sync_alerts(db, actor_id=None)
        operational_risk_response = sync_response_plans(db, actor_id=None)
        workflow_compliance_row, workflow_compliance = persist_compliance_snapshot(db, generated_by=None, snapshot_date=date.today())
        dashboard_row, dashboard = persist_executive_dashboard(db, None, date.today())
        executive_risk_response_row, executive_risk_response = persist_dashboard(db, None, date.today())
        governance_created = 0
        from app.models import ExecutiveRiskDecision, ExecutiveRiskDecisionGovernance
        for decision in db.query(ExecutiveRiskDecision).all():
            if not db.query(ExecutiveRiskDecisionGovernance).filter_by(decision_id=decision.id).first():
                build_governance(db, decision); governance_created += 1
        execution_created = 0
        effectiveness_created = 0
        improvement_created = 0
        for gov in db.query(ExecutiveRiskDecisionGovernance).filter(ExecutiveRiskDecisionGovernance.validation_status=='VALIDATED').all():
            if not db.query(ExecutiveRiskDecisionExecution).filter_by(governance_id=gov.id).first():
                create_execution(db, gov.id, actor_id=gov.validated_by); execution_created += 1
        for execution in db.query(ExecutiveRiskDecisionExecution).filter(ExecutiveRiskDecisionExecution.status=='VERIFIED').all():
            from app.models import ExecutiveRiskEffectiveness
            if not db.query(ExecutiveRiskEffectiveness).filter_by(execution_id=execution.id).first():
                create_effectiveness(db, execution.id, actor_id=None, criteria='Confirmar redução ou controle do risco identificado pela decisão.')
                effectiveness_created += 1
        improvement_created = len(analyze_improvement(db, actor_id=None))
        improvement_plans_created = 0
        from app.models import ContinuousImprovementRecommendation
        for rec in db.query(ContinuousImprovementRecommendation).filter(ContinuousImprovementRecommendation.status=='ACCEPTED').all():
            before = db.query(__import__('app.models', fromlist=['ContinuousImprovementPlan']).ContinuousImprovementPlan).filter_by(recommendation_id=rec.id).first()
            if not before:
                create_improvement_plan(db, rec.id, actor_id=None)
                improvement_plans_created += 1
        improvement_dashboard_row, improvement_dashboard = persist_improvement_dashboard(db, None, date.today())
        improvement_priority_row, improvement_priority = persist_improvement_priority(db, None)
        improvement_balancing_row, improvement_balancing = persist_improvement_balancing(db, None, date.today())
        execution_created = 0
        from app.models import ContinuousImprovementAssignmentDecision, ContinuousImprovementExecution
        for decision in db.query(ContinuousImprovementAssignmentDecision).filter(ContinuousImprovementAssignmentDecision.decision=='ACCEPT').all():
            if not db.query(ContinuousImprovementExecution).filter_by(decision_id=decision.id).first():
                try:
                    create_improvement_execution(db, decision.id, actor_id=None); execution_created += 1
                except ValueError:
                    log.warning('continuous_improvement_execution_not_created decision_id=%s', decision.id)
        certification_created = 0
        from app.models import ContinuousImprovementCertification
        for execution in db.query(ContinuousImprovementExecution).filter(ContinuousImprovementExecution.status=='VERIFIED').all():
            if not db.query(ContinuousImprovementCertification).filter_by(execution_id=execution.id).first():
                try:
                    # Certification is intentionally independent from assignee and verifier.
                    admins = [u.id for u in db.query(__import__('app.models',fromlist=['User']).User).filter_by(role='ADMIN', is_active=True).order_by(__import__('app.models',fromlist=['User']).User.id.asc()).all() if u.id not in {execution.assigned_to, execution.verified_by}]
                    if admins:
                        certify_improvement(db, execution.id, admins[0], 'Certificação automática diária do ciclo completo de melhoria.')
                        certification_created += 1
                except ValueError:
                    log.warning('continuous_improvement_certification_not_created execution_id=%s', execution.id)
        audit_created = 0
        from app.models import ContinuousImprovementAuditSnapshot
        for execution in db.query(ContinuousImprovementExecution).filter(ContinuousImprovementExecution.status=='VERIFIED').all():
            if db.query(ContinuousImprovementAuditSnapshot).filter_by(execution_id=execution.id).order_by(ContinuousImprovementAuditSnapshot.id.desc()).first() is None:
                try:
                    persist_improvement_audit(db, execution.id, None); audit_created += 1
                except ValueError:
                    log.warning('continuous_improvement_audit_not_created execution_id=%s', execution.id)
        executive_audit_row, executive_audit = persist_executive_improvement_audit(db, None)
        finalization = persist_finalization(db, None)
        db.commit()
        log.info('daily_tasks_completed reminders_created=%s penalties=%s penalty_total=%s', created, penalties['installments'], penalties['penalty_total'])
        return {'reminders_created': created, 'penalties': penalties, 'collections': collections, 'collection_recovery': recovery, 'workflow_escalation': workflow_escalation, 'workflow_orchestration': workflow_orchestration, 'workflow_execution': workflow_execution, 'workflow_integrity': workflow_integrity, 'workflow_compliance': {'id': workflow_compliance_row.id, 'status': workflow_compliance['status']}, 'workflow_incidents': workflow_incidents, 'capa_effectiveness': capa_effectiveness, 'operational_risk': {'id': operational_risk_row.id, 'status': operational_risk['status'], 'risk_score': operational_risk['risk_score']}, 'operational_risk_alerts': operational_risk_alerts, 'operational_risk_response': operational_risk_response, 'executive_dashboard': {'id': dashboard_row.id, 'status': dashboard['status']}, 'executive_risk_response': {'id': executive_risk_response_row.id, 'status': executive_risk_response['status']}, 'executive_risk_governance': {'created': governance_created}, 'executive_risk_execution': {'created': execution_created}, 'executive_risk_effectiveness': {'created': effectiveness_created}, 'continuous_improvement': {'created': improvement_created, 'plans_created': improvement_plans_created, 'dashboard': {'id': improvement_dashboard_row.id, 'status': improvement_dashboard['status']}, 'priority': {'id': improvement_priority_row.id, 'status': improvement_priority['counts']}, 'balancing': {'id': improvement_balancing_row.id, 'status': improvement_balancing['status'], 'unassigned': len(improvement_balancing['unassigned'])}, 'execution': {'created': execution_created}, 'certification': {'created': certification_created}, 'audit': {'created': audit_created}, 'executive_audit': {'id': executive_audit_row.id, 'status': executive_audit['status']}}}
    except Exception:
        db.rollback(); log.exception('daily_tasks_failed'); raise
    finally:
        db.close()
