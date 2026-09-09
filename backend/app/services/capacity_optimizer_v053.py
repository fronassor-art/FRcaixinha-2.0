from __future__ import annotations
import hashlib, json
from decimal import Decimal
from datetime import date
from sqlalchemy.orm import Session
from app.models import Group, Member, Loan
from app.services.risk_v036 import cash_balance, exposure
from app.services.scenario_simulator_v052 import simulate

D=Decimal
CENT=D('0.01')

def money(v): return D(v or 0).quantize(CENT)

def optimize_capacity(db: Session, *, group_id: int | None = None, member_id: int | None = None,
                      as_of: date | None = None, horizon_months: int = 12,
                      scenario: str = 'BASE', contribution_factor=1, delinquency_rate=0,
                      extra_expenses=0, reserve_floor=None, precision='0.01', max_search=10000000):
    if member_id is not None:
        member=db.get(Member, member_id)
        if not member: raise ValueError('Participante não encontrado.')
        group=db.get(Group, member.group_id)
    elif group_id is not None:
        group=db.get(Group, group_id); member=None
    else:
        group=None; member=None
    if group_id is not None and not group: raise ValueError('Grupo não encontrado.')
    if member_id is not None and group_id is not None and member.group_id != group_id: raise ValueError('Participante não pertence ao grupo informado.')
    floor=D(str(reserve_floor)) if reserve_floor is not None else D(str(group.min_cash_reserve if group else 0))
    if floor < 0: raise ValueError('reserve_floor não pode ser negativo.')
    balance=cash_balance(db)
    global_exp=exposure(db)
    member_exp=exposure(db, member_id) if member_id else None
    direct_room=max(D('0'), balance-floor)
    global_room=None
    if group and group.max_global_exposure is not None: global_room=max(D('0'),D(str(group.max_global_exposure))-global_exp)
    member_room=None
    if member and group and group.max_member_exposure is not None: member_room=max(D('0'),D(str(group.max_member_exposure))-member_exp)
    immediate_room=direct_room
    if global_room is not None: immediate_room=min(immediate_room,global_room)
    if member_room is not None: immediate_room=min(immediate_room,member_room)
    if group and group.max_loan_amount is not None: immediate_room=min(immediate_room,D(str(group.max_loan_amount)))

    def passes(x):
        data=simulate(db,as_of=as_of,horizon_months=horizon_months,scenario=scenario,contribution_factor=contribution_factor,delinquency_rate=delinquency_rate,new_loan_disbursements=x,extra_expenses=extra_expenses,reserve_floor=floor)
        if data['status'] != 'PASS': return False,data
        if group and group.max_global_exposure is not None and global_exp + x > D(str(group.max_global_exposure)): return False,data
        return True,data

    # Search in whole cents so the reported capacity is an executable monetary amount.
    lo_c=0
    hi_c=max(0, int((min(D(str(max_search)), immediate_room) * 100).to_integral_value()))
    baseline_ok, best_data=passes(D('0'))
    if not baseline_ok:
        return {'schema':'v0.53','decision':'BLOCKED','reason':'Cenário-base já viola a reserva mínima.','capacity':money(0),'immediate_capacity':money(immediate_room),'monthly_capacity':money(0),'bottlenecks':['RESERVE_BASELINE'],'horizon_months':horizon_months,'scenario':scenario.upper()}
    best_c=0
    while lo_c <= hi_c:
        mid_c=(lo_c+hi_c)//2
        ok,data=passes(D(mid_c)/100)
        if ok:
            best_c=mid_c; best_data=data; lo_c=mid_c+1
        else:
            hi_c=mid_c-1
    lo=D(best_c)/100
    monthly=money(lo)
    capacity=min(immediate_room,monthly)
    bottlenecks=[]
    if monthly < immediate_room: bottlenecks.append('PROJECTED_CASH')
    if global_room is not None and global_room <= capacity: bottlenecks.append('GLOBAL_EXPOSURE')
    if member_room is not None and member_room <= capacity: bottlenecks.append('MEMBER_EXPOSURE')
    if group and group.max_loan_amount is not None and D(str(group.max_loan_amount)) <= capacity: bottlenecks.append('MAX_LOAN_AMOUNT')
    if not bottlenecks: bottlenecks.append('RESERVE')
    return {'schema':'v0.53','decision':'ALLOW' if capacity>0 else 'BLOCKED','as_of_date':best_data['as_of_date'],
            'horizon_months':horizon_months,'scenario':scenario.upper(),'capacity':money(capacity),
            'monthly_capacity_by_projection':monthly,'immediate_capacity':money(immediate_room),
            'current_cash':money(balance),'reserve_floor':money(floor),'global_exposure':money(global_exp),
            'global_exposure_room':money(global_room) if global_room is not None else None,
            'member_exposure':money(member_exp) if member_exp is not None else None,
            'member_exposure_room':money(member_room) if member_room is not None else None,
            'bottlenecks':bottlenecks,'simulation_at_capacity':best_data}

def snapshot_hash(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
