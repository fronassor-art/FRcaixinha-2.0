import logging
import uuid
from datetime import datetime, timezone
from app.models import Cycle, CycleParticipation, SchedulerRunUnit
from app.services.cycle_foundation import ensure_contributions_for_entry
from app.services.cycle_participation import evaluate_delinquency, materialize_active_charges
from app.services.late_charge_v1 import financial_civil_date
from app.services import scheduler_runs
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
DAILY_JOB_KEY = "worker_daily_tasks"
DAILY_RUN_LEASE_SECONDS = 300
DAILY_CYCLE_JOB_KEY = "worker_cycle_participation_daily"
CYCLE_RUN_LEASE_SECONDS = 300
lock_run_for_execution = scheduler_runs.lock_run_for_execution


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_cycle_participation_tasks(scheduled_for: datetime | None = None) -> dict[str, int]:
    """Process a durable participation snapshot with one transaction per unit."""
    scheduled_for = scheduled_for or datetime.now(timezone.utc)
    financial_date = financial_civil_date(scheduled_for)
    lease_owner = f"cycle:{uuid.uuid4().hex}"
    db = SessionLocal()
    try:
        run, _ = scheduler_runs.get_or_create_run(
            db,
            job_key=DAILY_CYCLE_JOB_KEY,
            financial_date=financial_date,
            scheduled_for=scheduled_for,
        )
        run_id = run.id
        if not scheduler_runs.claim_run(
            db,
            run_id,
            lease_owner=lease_owner,
            now=_utc_now(),
            lease_seconds=CYCLE_RUN_LEASE_SECONDS,
        ):
            db.rollback()
            log.info("cycle_scheduler_run_not_claimed run_id=%s", run_id)
            return {"processed": 0, "blocked": 0}
        # Claim state is durable before snapshot and unit work starts.
        db.commit()

        try:
            lease_validated_at = scheduler_runs.lock_run_for_execution(
                db, run_id, lease_owner=lease_owner,
            )
            units = db.query(SchedulerRunUnit).filter_by(
                run_id=run_id,
            ).order_by(SchedulerRunUnit.id).all()
            if not units:
                # The first committed unit set is the immutable run snapshot.
                # Empty snapshots are finalized atomically so later arrivals
                # cannot be added to an already-observed empty run.
                snapshot = db.query(
                    CycleParticipation.id, CycleParticipation.cycle_id,
                ).filter(
                    CycleParticipation.status == "ACTIVE",
                ).order_by(CycleParticipation.id).all()
                for participation_id, cycle_id in snapshot:
                    scheduler_runs.get_or_create_unit(
                        db,
                        run_id=run_id,
                        unit_key=f"cycle:{cycle_id}:participation:{participation_id}",
                    )
                units = db.query(SchedulerRunUnit).filter_by(
                    run_id=run_id,
                ).order_by(SchedulerRunUnit.id).all()
            if not units:
                scheduler_runs.mark_run_succeeded_after_locked_execution(
                    db,
                    run_id,
                    lease_owner=lease_owner,
                    lease_validated_at=lease_validated_at,
                    completed_at=_utc_now(),
                )
                db.commit()
                return {"processed": 0, "blocked": 0}
            unit_snapshot = [(unit.id, unit.unit_key) for unit in units]
            db.commit()
        except Exception:
            db.rollback()
            try:
                scheduler_runs.mark_run_failed(
                    db, run_id, lease_owner=lease_owner,
                    error_code="execution_failed", failed_at=_utc_now(),
                )
                db.commit()
            except scheduler_runs.SchedulerLeaseLost:
                db.rollback()
            except Exception:
                db.rollback()
                log.error(
                    "cycle_scheduler_snapshot_failure_checkpoint_failed "
                    "run_id=%s error_code=execution_failed",
                    run_id,
                )
            raise

        processed = blocked = 0
        lost_owner = False
        for unit_id, unit_key in unit_snapshot:
            try:
                # Renew only a still-valid lease between unit transactions.
                # Expired/reclaimed ownership is never revived.
                scheduler_runs.renew_run_lease(
                    db,
                    run_id,
                    lease_owner=lease_owner,
                    lease_seconds=CYCLE_RUN_LEASE_SECONDS,
                )
                db.commit()
            except scheduler_runs.SchedulerLeaseLost:
                db.rollback()
                lost_owner = True
                break

            if not scheduler_runs.claim_unit(
                db,
                unit_id,
                lease_owner=lease_owner,
                now=_utc_now(),
                lease_seconds=CYCLE_RUN_LEASE_SECONDS,
            ):
                unit = db.get(SchedulerRunUnit, unit_id)
                succeeded = unit is not None and unit.status == "SUCCEEDED"
                db.rollback()
                if succeeded:
                    continue
                try:
                    scheduler_runs.lock_run_for_execution(
                        db, run_id, lease_owner=lease_owner,
                    )
                    db.rollback()
                except scheduler_runs.SchedulerLeaseLost:
                    db.rollback()
                    lost_owner = True
                    break
                # The parent is ours, but this unit did not become claimable.
                # Process other units and let final durable state mark the run
                # FAILED while any unit remains incomplete.
                continue
            db.commit()

            try:
                lease_validated_at = scheduler_runs.lock_unit_for_execution(
                    db, unit_id, lease_owner=lease_owner,
                )
                try:
                    prefix, cycle_id_value, kind, participation_id_value = unit_key.split(":")
                    if prefix != "cycle" or kind != "participation":
                        raise ValueError("invalid cycle participation unit key")
                    cycle_id = int(cycle_id_value)
                    participation_id = int(participation_id_value)
                except (ValueError, TypeError):
                    raise ValueError("invalid cycle participation unit key")
                row = db.query(CycleParticipation).filter(
                    CycleParticipation.id == participation_id,
                ).populate_existing().one_or_none()
                did_process = False
                result = None
                if row is not None and row.status == "ACTIVE":
                    cycle = db.get(Cycle, cycle_id) if row.cycle_id == cycle_id else None
                    if cycle is not None and financial_date >= cycle.start_date:
                        ensure_contributions_for_entry(
                            db,
                            member_id=row.member_id,
                            cycle_id=row.cycle_id,
                            entry_date=financial_date,
                        )
                        materialize_active_charges(
                            db,
                            member_id=row.member_id,
                            cycle_id=row.cycle_id,
                            effective_at=scheduled_for,
                        )
                        result = evaluate_delinquency(
                            db,
                            member_id=row.member_id,
                            cycle_id=row.cycle_id,
                            effective_at=scheduled_for,
                        )
                        did_process = True
                scheduler_runs.mark_unit_succeeded_after_locked_execution(
                    db,
                    unit_id,
                    lease_owner=lease_owner,
                    lease_validated_at=lease_validated_at,
                    completed_at=_utc_now(),
                )
                db.commit()
                if did_process:
                    processed += 1
                    blocked += result.status == "BLOCKED_DELINQUENCY"
            except Exception:
                db.rollback()
                try:
                    scheduler_runs.mark_unit_failed(
                        db,
                        unit_id,
                        lease_owner=lease_owner,
                        error_code="execution_failed",
                        failed_at=_utc_now(),
                    )
                    db.commit()
                except scheduler_runs.SchedulerLeaseLost:
                    db.rollback()
                    lost_owner = True
                    log.warning(
                        "cycle_scheduler_unit_owner_lost run_id=%s unit_id=%s",
                        run_id, unit_id,
                    )
                    break
                except Exception:
                    db.rollback()
                    log.error(
                        "cycle_scheduler_unit_failure_checkpoint_failed "
                        "run_id=%s unit_id=%s error_code=execution_failed",
                        run_id,
                        unit_id,
                    )
                log.error(
                    "cycle_participation_task_failed run_id=%s unit_id=%s "
                    "error_code=execution_failed",
                    run_id,
                    unit_id,
                )

        if lost_owner:
            return {"processed": processed, "blocked": blocked}

        try:
            scheduler_runs.renew_run_lease(
                db,
                run_id,
                lease_owner=lease_owner,
                lease_seconds=CYCLE_RUN_LEASE_SECONDS,
            )
            db.commit()
            lease_validated_at = scheduler_runs.lock_run_for_execution(
                db, run_id, lease_owner=lease_owner,
            )
            unit_states = db.query(SchedulerRunUnit.status).filter_by(
                run_id=run_id,
            ).all()
            if all(status == "SUCCEEDED" for (status,) in unit_states):
                scheduler_runs.mark_run_succeeded_after_locked_execution(
                    db,
                    run_id,
                    lease_owner=lease_owner,
                    lease_validated_at=lease_validated_at,
                    completed_at=_utc_now(),
                )
            else:
                scheduler_runs.mark_run_failed(
                    db,
                    run_id,
                    lease_owner=lease_owner,
                    error_code="execution_failed",
                    failed_at=_utc_now(),
                )
            db.commit()
        except scheduler_runs.SchedulerLeaseLost:
            db.rollback()
            log.warning("cycle_scheduler_run_owner_lost run_id=%s", run_id)
        return {"processed": processed, "blocked": blocked}
    finally:
        db.close()

def run_daily_tasks(scheduled_for: datetime | None = None):
    scheduled_for = scheduled_for or _utc_now()
    financial_date = financial_civil_date(scheduled_for)
    lease_owner = f"daily:{uuid.uuid4().hex}"
    db = SessionLocal()
    try:
        run, _ = scheduler_runs.get_or_create_run(
            db,
            job_key=DAILY_JOB_KEY,
            financial_date=financial_date,
            scheduled_for=scheduled_for,
        )
        run_id = run.id
        if not scheduler_runs.claim_run(
            db,
            run_id,
            lease_owner=lease_owner,
            now=_utc_now(),
            lease_seconds=DAILY_RUN_LEASE_SECONDS,
        ):
            status = run.status
            db.rollback()
            log.info("daily_tasks_not_claimed run_id=%s status=%s", run_id, status)
            return {"scheduler_run": status, "executed": False}

        # Persist the claim before opening the long execution transaction.
        db.commit()
        try:
            lease_validated_at = lock_run_for_execution(db, run_id, lease_owner=lease_owner)
            result = _execute_daily_effects(db, financial_date)
            scheduler_runs.mark_run_succeeded_after_locked_execution(
                db,
                run_id,
                lease_owner=lease_owner,
                lease_validated_at=lease_validated_at,
                completed_at=_utc_now(),
            )
            db.commit()
            result["scheduler_run"] = "SUCCEEDED"
            result["executed"] = True
            return result
        except Exception:
            db.rollback()
            try:
                scheduler_runs.mark_run_failed(
                    db,
                    run_id,
                    lease_owner=lease_owner,
                    error_code="execution_failed",
                    failed_at=_utc_now(),
                )
                db.commit()
            except scheduler_runs.SchedulerLeaseLost:
                db.rollback()
            except Exception:
                db.rollback()
                log.exception("daily_scheduler_failure_checkpoint_failed run_id=%s", run_id)
            log.exception("daily_tasks_failed run_id=%s", run_id)
            raise
    finally:
        db.close()


def _execute_daily_effects(db, financial_date):
    try:
        created = queue_installment_reminders(db, days_ahead=3, financial_date=financial_date)
        penalties = accrue_overdue_penalties(db, financial_date, settings.loan_daily_penalty_rate)
        collections = run_collection_cycle(db, financial_date)
        recovery = sync_cases(db, financial_date)
        workflow_escalation = sync_workflow_escalations(db, actor_id=None)
        workflow_orchestration = sync_workflow_orchestration(db, actor_id=None)
        workflow_execution = sync_execution_states(db, actor_id=None)
        workflow_integrity = verify_all(db, actor_id=None)
        workflow_incidents = sync_incidents(db, actor_id=None)
        capa_effectiveness = sync_capa_recurrence(db, actor_id=None)
        operational_risk_row, operational_risk = persist_risk_snapshot(db, generated_by=None, snapshot_date=financial_date)
        operational_risk_alerts = sync_alerts(db, actor_id=None)
        operational_risk_response = sync_response_plans(db, actor_id=None)
        workflow_compliance_row, workflow_compliance = persist_compliance_snapshot(db, generated_by=None, snapshot_date=financial_date)
        dashboard_row, dashboard = persist_executive_dashboard(db, None, financial_date)
        executive_risk_response_row, executive_risk_response = persist_dashboard(db, None, financial_date)
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
        improvement_dashboard_row, improvement_dashboard = persist_improvement_dashboard(db, None, financial_date)
        improvement_priority_row, improvement_priority = persist_improvement_priority(db, None, snapshot_date=financial_date)
        improvement_balancing_row, improvement_balancing = persist_improvement_balancing(db, None, financial_date)
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
        finalization = persist_finalization(db, None, financial_date=financial_date)
        log.info('daily_tasks_completed reminders_created=%s penalties=%s penalty_total=%s', created, penalties['installments'], penalties['penalty_total'])
        return {'reminders_created': created, 'penalties': penalties, 'collections': collections, 'collection_recovery': recovery, 'workflow_escalation': workflow_escalation, 'workflow_orchestration': workflow_orchestration, 'workflow_execution': workflow_execution, 'workflow_integrity': workflow_integrity, 'workflow_compliance': {'id': workflow_compliance_row.id, 'status': workflow_compliance['status']}, 'workflow_incidents': workflow_incidents, 'capa_effectiveness': capa_effectiveness, 'operational_risk': {'id': operational_risk_row.id, 'status': operational_risk['status'], 'risk_score': operational_risk['risk_score']}, 'operational_risk_alerts': operational_risk_alerts, 'operational_risk_response': operational_risk_response, 'executive_dashboard': {'id': dashboard_row.id, 'status': dashboard['status']}, 'executive_risk_response': {'id': executive_risk_response_row.id, 'status': executive_risk_response['status']}, 'executive_risk_governance': {'created': governance_created}, 'executive_risk_execution': {'created': execution_created}, 'executive_risk_effectiveness': {'created': effectiveness_created}, 'continuous_improvement': {'created': improvement_created, 'plans_created': improvement_plans_created, 'dashboard': {'id': improvement_dashboard_row.id, 'status': improvement_dashboard['status']}, 'priority': {'id': improvement_priority_row.id, 'status': improvement_priority['counts']}, 'balancing': {'id': improvement_balancing_row.id, 'status': improvement_balancing['status'], 'unassigned': len(improvement_balancing['unassigned'])}, 'execution': {'created': execution_created}, 'certification': {'created': certification_created}, 'audit': {'created': audit_created}, 'executive_audit': {'id': executive_audit_row.id, 'status': executive_audit['status']}}}
    except Exception:
        log.exception('daily_effects_failed')
        raise
