from fastapi import FastAPI
from app.core.logging_config import configure_logging
from app.core.production import configure_middlewares
from app.core.rate_limit import RedisRateLimitMiddleware
from app.api.auth import router as auth_router
from app.api.members import router as members_router
from app.api.contributions import router as contributions_router
from app.api.loans import router as loans_router
from app.api.payments import router as payments_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.core.config import settings
from app.api.admin import router as admin_router
from app.api.admin_finance import router as admin_finance_router
from app.api.admin_reports import router as admin_reports_router
from app.api.notifications import router as notifications_router
from app.api.loan_installment_payments import router as loan_installment_payments_router
from app.api.admin_reconciliation import router as admin_reconciliation_router
from app.api.admin_risk import router as admin_risk_router
from app.api.admin_credit_policy import router as admin_credit_policy_router
from app.api.admin_collections import router as admin_collections_router
from app.api.agreements_v039 import router as agreements_router
from app.api.agreement_installment_payments import router as agreement_installment_payments_router
from app.api.admin_governance import router as admin_governance_router
from app.api.member_portal_v043 import router as member_portal_router
from app.api.communication_v044 import router as communication_router
from app.api.privacy_v045 import router as privacy_router
from app.api.admin_security_v046 import router as admin_security_router
from app.api.admin_financial_risk_v047 import router as admin_financial_risk_router
from app.api.admin_approval_v048 import router as admin_approval_router
from app.api.admin_collection_recovery_v049 import router as admin_collection_recovery_router
from app.api.admin_executive_dashboard_v050 import router as admin_executive_dashboard_router
from app.api.admin_executive_risk_response_v077 import router as admin_executive_risk_response_router
from app.api.admin_executive_risk_governance_v079 import router as admin_executive_risk_governance_router
from app.api.admin_executive_risk_execution_v080 import router as admin_executive_risk_execution_router
from app.api.admin_executive_risk_effectiveness_v081 import router as admin_executive_risk_effectiveness_router
from app.api.admin_continuous_improvement_v082 import router as admin_continuous_improvement_router
from app.api.admin_continuous_improvement_v083 import router as admin_continuous_improvement_v083_router
from app.api.admin_continuous_improvement_v084 import router as admin_continuous_improvement_v084_router
from app.api.admin_continuous_improvement_v085 import router as admin_continuous_improvement_v085_router
from app.api.admin_continuous_improvement_v086 import router as admin_continuous_improvement_v086_router
from app.api.admin_continuous_improvement_v087 import router as admin_continuous_improvement_v087_router
from app.api.admin_continuous_improvement_v088 import router as admin_continuous_improvement_v088_router
from app.api.admin_continuous_improvement_v089 import router as admin_continuous_improvement_v089_router
from app.api.admin_continuous_improvement_v090 import router as admin_continuous_improvement_v090_router
from app.api.admin_continuous_improvement_v091 import router as admin_continuous_improvement_v091_router
from app.api.admin_continuous_improvement_v092 import router as admin_continuous_improvement_v092_router
from app.api.admin_continuous_improvement_finalization_v093_100 import router as admin_continuous_improvement_finalization_router
from app.api.admin_executive_risk_decision_v078 import router as admin_executive_risk_decision_router
from app.api.admin_financial_projection_v051 import router as admin_financial_projection_router
from app.api.admin_scenario_simulation_v052 import router as admin_scenario_simulation_router
from app.api.admin_capacity_optimizer_v053 import router as admin_capacity_optimizer_router
from app.api.admin_resource_allocation_v054 import router as admin_resource_allocation_router
from app.api.admin_allocation_governance_v055 import router as admin_allocation_governance_router
from app.api.admin_allocation_transparency_v056 import router as admin_allocation_transparency_router
from app.api.admin_allocation_decisions_v057 import router as admin_allocation_decisions_router
from app.api.admin_integrated_governance_v058 import router as admin_integrated_governance_router
from app.api.admin_secure_release_v059 import router as admin_secure_release_router
from app.api.admin_operational_control_v060 import router as admin_operational_control_router
from app.api.admin_command_center_v061 import router as admin_command_center_router
from app.api.admin_workflow_v062 import router as admin_workflow_router
from app.api.admin_workflow_sla_v063 import router as admin_workflow_sla_router
from app.api.admin_workflow_escalation_v064 import router as admin_workflow_escalation_router
from app.api.admin_workflow_orchestration_v065 import router as admin_workflow_orchestration_router
from app.api.admin_workflow_execution_v066 import router as admin_workflow_execution_router
from app.api.admin_workflow_evidence_v067 import router as admin_workflow_evidence_router
from app.api.admin_workflow_storage_v068 import router as admin_workflow_storage_router
from app.api.admin_workflow_integrity_v069 import router as admin_workflow_integrity_router
from app.api.admin_workflow_compliance_v070 import router as admin_workflow_compliance_router
from app.api.admin_workflow_incidents_v071 import router as admin_workflow_incidents_router
from app.api.admin_capa_v072 import router as admin_capa_router
from app.api.admin_capa_effectiveness_v073 import router as admin_capa_effectiveness_router
from app.api.admin_operational_risk_v074 import router as admin_operational_risk_router
from app.api.admin_operational_risk_alerts_v075 import router as admin_operational_risk_alerts_router
from app.api.admin_operational_risk_response_v076 import router as admin_operational_risk_response_router

configure_logging()
app = FastAPI(
    title="FRcaixinha API",
    version="2.0.0-v0.92",
    docs_url=None if settings.app_env.lower() == "production" else "/docs",
    redoc_url=None if settings.app_env.lower() == "production" else "/redoc",
    openapi_url=None if settings.app_env.lower() == "production" else "/openapi.json",
)
configure_middlewares(app)
app.add_middleware(RedisRateLimitMiddleware)

@app.middleware("http")
async def metrics_middleware(request, call_next):
    import time
    from app.core.metrics import HTTP_REQUESTS, HTTP_LATENCY
    started = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
    return response

app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(auth_router, prefix="/api")
app.include_router(members_router, prefix="/api")
app.include_router(contributions_router, prefix="/api")
app.include_router(loans_router, prefix="/api")
app.include_router(payments_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(admin_finance_router, prefix="/api")
app.include_router(admin_reports_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(loan_installment_payments_router, prefix="/api")
app.include_router(admin_reconciliation_router, prefix="/api")
app.include_router(admin_risk_router, prefix="/api")
app.include_router(admin_credit_policy_router, prefix="/api")
app.include_router(admin_collections_router, prefix="/api")
app.include_router(agreements_router, prefix="/api")
app.include_router(agreement_installment_payments_router, prefix="/api")
app.include_router(admin_governance_router, prefix="/api")
app.include_router(member_portal_router, prefix="/api")
app.include_router(communication_router, prefix="/api")
app.include_router(privacy_router, prefix="/api")
app.include_router(admin_security_router, prefix="/api")
app.include_router(admin_financial_risk_router, prefix="/api")
app.include_router(admin_approval_router, prefix="/api")
app.include_router(admin_collection_recovery_router, prefix="/api")
app.include_router(admin_executive_dashboard_router, prefix="/api")
app.include_router(admin_executive_risk_response_router, prefix="/api")
app.include_router(admin_executive_risk_decision_router, prefix="/api")
app.include_router(admin_executive_risk_governance_router, prefix="/api")
app.include_router(admin_executive_risk_execution_router, prefix="/api")
app.include_router(admin_executive_risk_effectiveness_router, prefix="/api")
app.include_router(admin_continuous_improvement_router, prefix="/api")
app.include_router(admin_continuous_improvement_v083_router, prefix="/api")
app.include_router(admin_continuous_improvement_v084_router, prefix="/api")
app.include_router(admin_continuous_improvement_v085_router, prefix="/api")
app.include_router(admin_continuous_improvement_v086_router, prefix="/api")
app.include_router(admin_continuous_improvement_v087_router, prefix="/api")
app.include_router(admin_continuous_improvement_v088_router, prefix="/api")
app.include_router(admin_continuous_improvement_v089_router, prefix="/api")
app.include_router(admin_continuous_improvement_v090_router, prefix="/api")
app.include_router(admin_continuous_improvement_v091_router, prefix="/api")
app.include_router(admin_continuous_improvement_v092_router, prefix="/api")
app.include_router(admin_continuous_improvement_finalization_router, prefix="/api")
app.include_router(admin_financial_projection_router, prefix="/api")
app.include_router(admin_scenario_simulation_router, prefix="/api")
app.include_router(admin_capacity_optimizer_router, prefix="/api")
app.include_router(admin_resource_allocation_router, prefix="/api")
app.include_router(admin_allocation_governance_router, prefix="/api")
app.include_router(admin_allocation_transparency_router, prefix="/api")
app.include_router(admin_allocation_decisions_router, prefix="/api")
app.include_router(admin_integrated_governance_router, prefix="/api")
app.include_router(admin_secure_release_router, prefix="/api")
app.include_router(admin_operational_control_router, prefix="/api")
app.include_router(admin_command_center_router, prefix="/api")
app.include_router(admin_workflow_router, prefix="/api")
app.include_router(admin_workflow_sla_router, prefix="/api")
app.include_router(admin_workflow_escalation_router, prefix="/api")
app.include_router(admin_workflow_orchestration_router, prefix="/api")
app.include_router(admin_workflow_execution_router, prefix="/api")
app.include_router(admin_workflow_evidence_router, prefix="/api")
app.include_router(admin_workflow_storage_router, prefix="/api")
app.include_router(admin_workflow_integrity_router, prefix="/api")
app.include_router(admin_workflow_compliance_router, prefix="/api")
app.include_router(admin_workflow_incidents_router, prefix="/api")
app.include_router(admin_capa_router, prefix="/api")
app.include_router(admin_capa_effectiveness_router, prefix="/api")
app.include_router(admin_operational_risk_router, prefix="/api")
app.include_router(admin_operational_risk_alerts_router, prefix="/api")
app.include_router(admin_operational_risk_response_router, prefix="/api")
