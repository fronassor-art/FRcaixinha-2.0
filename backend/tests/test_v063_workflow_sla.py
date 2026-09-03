from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from app.services.workflow_sla_v063 import sla_hours, enrich_task_sla

def task(priority='HIGH',status='PENDING',due=None):
    now=datetime.now(timezone.utc)
    return SimpleNamespace(priority=priority,status=status,due_at=due,created_at=now)

def test_priority_sla_windows():
    assert sla_hours('CRITICAL')==4
    assert sla_hours('HIGH')==12
    assert sla_hours('MEDIUM')==24
    assert sla_hours('LOW')==72

def test_overdue_detection():
    t=task('HIGH',due=datetime.now(timezone.utc)-timedelta(minutes=1))
    assert enrich_task_sla(t)['overdue'] is True

def test_completed_not_overdue():
    t=task('CRITICAL','COMPLETED',datetime.now(timezone.utc)-timedelta(days=1))
    assert enrich_task_sla(t)['overdue'] is False
