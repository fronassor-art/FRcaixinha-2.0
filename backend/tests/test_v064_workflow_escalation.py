from datetime import datetime, timezone, timedelta
from app.services.workflow_escalation_v064 import LEVEL_RANK, PRIORITY_LEVEL

def test_escalation_levels_are_monotonic():
    assert LEVEL_RANK['NONE'] < LEVEL_RANK['HIGH'] < LEVEL_RANK['CRITICAL']

def test_priority_maps_to_safe_escalation():
    assert PRIORITY_LEVEL['CRITICAL'] == 'CRITICAL'
    assert PRIORITY_LEVEL['HIGH'] == 'HIGH'
    assert PRIORITY_LEVEL['MEDIUM'] == 'HIGH'
    assert PRIORITY_LEVEL['LOW'] == 'NONE'
