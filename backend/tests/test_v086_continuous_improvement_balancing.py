from app.services.continuous_improvement_balancing_v086 import candidate_order

def test_candidate_order_is_deterministic_and_prefers_relative_load():
    cs=[{'user_id':2,'max_active_items':5},{'user_id':1,'max_active_items':5},{'user_id':3,'max_active_items':10}]
    loads={1:2,2:1,3:3}; crit={1:0,2:1,3:0}
    assert [x['user_id'] for x in candidate_order(cs,loads,crit)]==[2,3,1]

def test_candidate_order_tie_breaks_by_critical_then_id():
    cs=[{'user_id':3,'max_active_items':5},{'user_id':1,'max_active_items':5},{'user_id':2,'max_active_items':5}]
    loads={1:2,2:2,3:2}; crit={1:1,2:0,3:0}
    assert [x['user_id'] for x in candidate_order(cs,loads,crit)]==[2,3,1]
