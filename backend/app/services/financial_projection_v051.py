import hashlib, json
from calendar import monthrange
from datetime import date
from decimal import Decimal
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import FinancialProjectionSnapshot, Group, Member, Contribution, LoanInstallment, Expense, LedgerEntry

ZERO=Decimal('0.00')
SCENARIOS={'CONSERVATIVE':Decimal('0.75'),'BASE':Decimal('1.00'),'OPTIMISTIC':Decimal('1.10')}

def money(v): return f"{Decimal(v or 0).quantize(Decimal('0.01')):.2f}"
def add_month(d,n):
    y=d.year+(d.month-1+n)//12; m=(d.month-1+n)%12+1; return date(y,m,1)
def month_end(d): return date(d.year,d.month,monthrange(d.year,d.month)[1])

def build_projection(db:Session, as_of:date|None=None, horizon_months:int=12, scenario:str='BASE'):
    as_of=as_of or date.today(); scenario=scenario.upper()
    if scenario not in SCENARIOS: raise ValueError('Cenário inválido.')
    if not 1<=horizon_months<=36: raise ValueError('Horizonte deve estar entre 1 e 36 meses.')
    factor=SCENARIOS[scenario]
    credits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='CREDIT').scalar() or 0)
    debits=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=='DEBIT').scalar() or 0)
    opening=credits-debits
    groups=db.query(Group).filter(Group.active.is_(True)).all()
    monthly_contrib=ZERO
    for g in groups:
        members_in_group=db.query(Member).filter(Member.group_id==g.id,Member.status=='ACTIVE').count()
        monthly_contrib += Decimal(g.monthly_amount or 0) * members_in_group
    avg_exp=Decimal(db.query(func.coalesce(func.avg(Expense.amount),0)).filter(Expense.status=='POSTED',Expense.expense_date>=date(as_of.year, max(1,as_of.month-2),1)).scalar() or 0)
    if avg_exp==0: avg_exp=Decimal('0')
    inst=db.query(LoanInstallment).filter(LoanInstallment.paid_at.is_(None),LoanInstallment.status.notin_(['PAID','AGREED'])).all()
    rows=[]; running=opening
    for i in range(horizon_months):
        m=add_month(date(as_of.year,as_of.month,1),i)
        scheduled=sum((max(ZERO,Decimal(x.amount or 0)-Decimal(x.paid_amount or 0)+Decimal(x.penalty_amount or 0)-Decimal(x.paid_penalty_amount or 0)) for x in inst if x.due_date.year==m.year and x.due_date.month==m.month),ZERO)
        contrib=monthly_contrib*factor
        receiv=scheduled*factor
        expense=avg_exp*(Decimal('1.25') if scenario=='CONSERVATIVE' else Decimal('0.90') if scenario=='OPTIMISTIC' else Decimal('1.00'))
        net=contrib+receiv-expense; running += net
        rows.append({'month':m.isoformat(),'opening_cash':money(running-net),'expected_contributions':money(contrib),'expected_loan_receipts':money(receiv),'expected_expenses':money(expense),'net_cash_flow':money(net),'projected_cash':money(running)})
    min_cash=min((Decimal(r['projected_cash']) for r in rows),default=opening)
    negative=min_cash<0
    return {'schema':'v0.51','as_of_date':as_of.isoformat(),'horizon_months':horizon_months,'scenario':scenario,'status':'ATTENTION' if negative else 'PASS','assumptions':{'contribution_factor':str(factor),'expense_basis':'recent posted expense average','new_loan_disbursements_excluded':'true','future_penalties_excluded':'true'},'starting_cash':money(opening),'minimum_projected_cash':money(min_cash),'ending_projected_cash':money(running),'projection':rows}

def persist_projection(db:Session, generated_by:int|None=None, as_of:date|None=None, horizon_months:int=12, scenario:str='BASE'):
    data=build_projection(db,as_of,horizon_months,scenario); raw=json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False); h=hashlib.sha256(raw.encode()).hexdigest()
    row=db.query(FinancialProjectionSnapshot).filter_by(as_of_date=date.fromisoformat(data['as_of_date']),horizon_months=horizon_months,scenario=scenario.upper()).first()
    if row: row.status=data['status']; row.snapshot_json=raw; row.snapshot_hash=h; row.generated_by=generated_by
    else: row=FinancialProjectionSnapshot(as_of_date=date.fromisoformat(data['as_of_date']),horizon_months=horizon_months,scenario=scenario.upper(),status=data['status'],snapshot_json=raw,snapshot_hash=h,generated_by=generated_by); db.add(row)
    db.flush(); return row,data
