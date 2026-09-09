from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from app.services.workflow_orchestration_v065 import orchestration_score, queue_status

def test_score_combines_priority_sla_and_escalation():
    assert orchestration_score(SimpleNamespace(priority='CRITICAL'), 'OVERDUE', 'CRITICAL') > orchestration_score(SimpleNamespace(priority='HIGH'), 'ON_TRACK', 'NONE')

def test_queue_routes_execution_and_escalation():
    assert queue_status(SimpleNamespace(status='IN_EXECUTION', assigned_to=2), 'NONE') == 'IN_PROGRESS'
    assert queue_status(SimpleNamespace(status='ASSIGNED', assigned_to=2), 'NONE') == 'ASSIGNED'
    assert queue_status(SimpleNamespace(status='PENDING', assigned_to=None), 'NONE') == 'READY'
    assert queue_status(SimpleNamespace(status='PENDING', assigned_to=None), 'CRITICAL') == 'ESCALATED'
    assert queue_status(SimpleNamespace(status='COMPLETED', assigned_to=2), 'CRITICAL') == 'COMPLETED'
