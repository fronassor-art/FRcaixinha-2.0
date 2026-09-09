import json
from types import SimpleNamespace
import app.services.allocation_transparency_v056 as mod

def sample():
    return {'schema':'v0.55','group_id':7,'capacity':'100.00','allocated_total':'100.00','unallocated':'0.00','decision':'ALLOW','policy':{'name':'P','quota_weight':'1','payment_history_weight':'1','tenure_weight':'0.25','risk_weight':'1','review_factor':'0.5','tie_breaker':'OLDEST_MEMBER','version':4,'active':True},'items':[{'member_id':10,'quota_units':'2','payment_score':'0.8','tenure_score':'0.4','risk_decision':'ALLOW','priority_score':'2.9','governed_amount':'70.00','exposure':'20.00','member_room':'80.00'}]}

def test_policy_version_is_frozen_in_explanation():
    x=mod.build_explanation(sample())
    assert x['policy_snapshot']['version']==4
    assert x['items'][0]['priority_score']=='2.9'

def test_hash_changes_when_explanation_changes():
    x=mod.build_explanation(sample()); h=mod.explanation_hash(x)
    x['policy_snapshot']['version']=5
    assert h != mod.explanation_hash(x)

def test_member_explanation_contains_exact_inputs():
    x=mod.build_explanation(sample())
    item=x['items'][0]
    assert item['member_id']==10
    assert item['payment_score']=='0.8'
    assert item['tenure_score']=='0.4'
    assert item['governed_amount']=='70.00'

def test_canonical_hash_is_order_independent():
    assert mod.explanation_hash({'b':2,'a':1})==mod.explanation_hash({'a':1,'b':2})
