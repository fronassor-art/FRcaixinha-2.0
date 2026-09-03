from app.services.operational_risk_alerts_v075 import fingerprint, _derive

def test_v075_fingerprint_is_stable():
    assert fingerprint('RISK_TREND','2026-09-03') == fingerprint('RISK_TREND','2026-09-03')
    assert fingerprint('RISK_TREND','2026-09-03') != fingerprint('RECURRENCE_TREND','2026-09-03')

def test_v075_predictive_alerts_for_high_risk():
    data={'risk_score':75,'current':{'critical_incidents':2,'recurrences':3,'ineffective_reviews':2},'trends':[{'metric':'recurrences','direction':'UP'},{'metric':'ineffective_reviews','direction':'UP'},{'metric':'critical_incidents','direction':'STABLE'}]}
    alerts=_derive(data)
    kinds=[a[0] for a in alerts]
    assert 'CRITICAL_RISK' in kinds
    assert 'RECURRENCE_TREND' in kinds
    assert 'CAPA_EFFECTIVENESS' in kinds

def test_v075_no_alert_when_low_and_stable():
    data={'risk_score':5,'current':{'critical_incidents':0,'recurrences':0,'ineffective_reviews':0},'trends':[{'metric':'recurrences','direction':'STABLE'},{'metric':'ineffective_reviews','direction':'STABLE'},{'metric':'critical_incidents','direction':'STABLE'}]}
    assert _derive(data)==[]
