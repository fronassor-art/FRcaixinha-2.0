import hashlib, json
from datetime import date
from decimal import Decimal
from app.services.financial_projection_v051 import build_projection

D=Decimal

def money(v): return f'{D(v).quantize(D("0.01")):.2f}'

def simulate(db, as_of=None, horizon_months=12, scenario='BASE', contribution_factor=1, delinquency_rate=0, new_loan_disbursements=0, extra_expenses=0, reserve_floor=0):
    if not 1 <= horizon_months <= 36: raise ValueError('Horizonte deve estar entre 1 e 36 meses.')
    for name,v in [('contribution_factor',contribution_factor),('delinquency_rate',delinquency_rate),('new_loan_disbursements',new_loan_disbursements),('extra_expenses',extra_expenses),('reserve_floor',reserve_floor)]:
        if D(str(v)) < 0: raise ValueError(f'{name} não pode ser negativo.')
    if D(str(delinquency_rate)) > 1: raise ValueError('delinquency_rate deve estar entre 0 e 1.')
    base=build_projection(db, as_of=as_of, horizon_months=horizon_months, scenario=scenario)
    rows=[]; cash=D(base['starting_cash']); min_cash=cash; capacity=[]
    for r in base['projection']:
        contrib=D(r['expected_contributions'])*D(str(contribution_factor))
        receipts=D(r['expected_loan_receipts']) * (D('1')-D(str(delinquency_rate)))
        expenses=D(r['expected_expenses'])+D(str(extra_expenses))
        disb=D(str(new_loan_disbursements))
        net=contrib+receipts-expenses-disb; opening=cash; cash+=net; min_cash=min(min_cash,cash)
        available=max(D('0'),cash-D(str(reserve_floor)))
        rows.append({'month':r['month'],'opening_cash':money(opening),'contributions':money(contrib),'loan_receipts':money(receipts),'new_loan_disbursements':money(disb),'expenses':money(expenses),'net_cash_flow':money(net),'projected_cash':money(cash),'available_above_reserve':money(available),'reserve_floor':money(reserve_floor)})
        capacity.append(available)
    status='BLOCKED' if min_cash < D(str(reserve_floor)) else 'PASS'
    return {'schema':'v0.52','as_of_date':base['as_of_date'],'scenario':scenario.upper(),'horizon_months':horizon_months,'status':status,'inputs':{'contribution_factor':str(contribution_factor),'delinquency_rate':str(delinquency_rate),'new_loan_disbursements_monthly':str(new_loan_disbursements),'extra_expenses_monthly':str(extra_expenses),'reserve_floor':str(reserve_floor)},'starting_cash':base['starting_cash'],'minimum_projected_cash':money(min_cash),'ending_projected_cash':money(cash),'minimum_available_above_reserve':money(min(capacity) if capacity else 0),'simulation':rows}

def snapshot_hash(data): return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
