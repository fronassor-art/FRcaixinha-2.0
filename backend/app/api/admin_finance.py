from calendar import monthrange
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import Contribution, LoanInstallment, LedgerEntry, Expense, MonthlyClosing, AuditLog
from app.services.ledger import post_entry
router=APIRouter(prefix="/admin/finance",tags=["admin-finance"])
def money(v): return str(Decimal(v or 0).quantize(Decimal("0.01")))
def bounds(d): return d.replace(day=1), d.replace(day=monthrange(d.year,d.month)[1])
def balance(db):
    c=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=="CREDIT").scalar() or 0)
    d=Decimal(db.query(func.coalesce(func.sum(LedgerEntry.amount),0)).filter(LedgerEntry.direction=="DEBIT").scalar() or 0)
    return c-d
class ExpenseIn(BaseModel):
    description:str=Field(min_length=2,max_length=200); amount:Decimal=Field(gt=0); expense_date:date; category:str=Field(default="GENERAL",min_length=1,max_length=80)
@router.get("/summary")
def summary(competence:date|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
    if competence:
        a,b=bounds(competence); cq=Contribution.competence.between(a,b); eq=Expense.expense_date.between(a,b)
    else: cq=True; eq=True
    contrib=Decimal(db.query(func.coalesce(func.sum(Contribution.amount),0)).filter(Contribution.status=="PAID",cq).scalar() or 0)
    exp=Decimal(db.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.status=="POSTED",eq).scalar() or 0)
    ints=sum((Decimal(i.interest) for i in db.query(LoanInstallment).filter(LoanInstallment.status=="PAID").all()),Decimal("0"))
    return {"competence":competence.isoformat() if competence else None,"contributions_paid":money(contrib),"expenses":money(exp),"interest_received":money(ints),"operating_net":money(contrib+ints-exp),"ledger_balance":money(balance(db))}
@router.get("/expenses")
def expenses(competence:date|None=None,category:str|None=None,admin=Depends(require_admin),db:Session=Depends(get_db)):
    q=db.query(Expense).filter(Expense.status=="POSTED")
    if competence:
        a,b=bounds(competence); q=q.filter(Expense.expense_date.between(a,b))
    if category:q=q.filter(Expense.category==category.upper())
    rows=q.order_by(Expense.expense_date.desc(),Expense.id.desc()).limit(500).all()
    return {"items":[{"id":e.id,"description":e.description,"amount":money(e.amount),"expense_date":e.expense_date.isoformat(),"category":e.category,"created_by":e.created_by} for e in rows]}
@router.post("/expenses",status_code=201)
def create_expense(data:ExpenseIn,admin=Depends(require_admin),db:Session=Depends(get_db)):
    e=Expense(description=data.description.strip(),amount=data.amount.quantize(Decimal("0.01")),expense_date=data.expense_date,category=data.category.strip().upper(),created_by=admin.id)
    db.add(e); db.flush(); post_entry(db,"CAIXINHA","DEBIT",e.amount,"EXPENSE",str(e.id))
    db.add(AuditLog(actor_user_id=admin.id,action="EXPENSE_CREATED",entity_type="EXPENSE",entity_id=str(e.id),details=e.description)); db.commit(); db.refresh(e)
    return {"id":e.id,"status":e.status}
@router.get("/closings")
def closings(admin=Depends(require_admin),db:Session=Depends(get_db)):
    rows=db.query(MonthlyClosing).order_by(MonthlyClosing.competence.desc()).limit(120).all()
    return {"items":[{"id":c.id,"competence":c.competence.isoformat(),"status":c.status,"total_contributions":money(c.total_contributions),"total_expenses":money(c.total_expenses),"total_interest_received":money(c.total_interest_received),"ledger_balance":money(c.ledger_balance),"closed_by":c.closed_by,"closed_at":c.closed_at.isoformat() if c.closed_at else None} for c in rows]}
@router.post("/closings/{competence}")
def close_month(competence:date,admin=Depends(require_admin),db:Session=Depends(get_db)):
    from app.services.monthly_closing_v040 import close_month_v040 as do_close
    try:
        existing, snap, h = do_close(db, competence, admin.id)
        db.add(AuditLog(actor_user_id=admin.id,action="MONTH_CLOSED_V040",entity_type="MONTHLY_CLOSING",entity_id=str(existing.id),details=h))
        db.commit(); db.refresh(existing)
        return {"id":existing.id,"competence":existing.competence.isoformat(),"status":existing.status,"ledger_balance":money(existing.ledger_balance),"snapshot_hash":existing.snapshot_hash,"snapshot":snap}
    except ValueError as e:
        db.rollback(); raise HTTPException(409,str(e))

@router.get("/closings/{competence}/verify")
def verify_month(competence:date,admin=Depends(require_admin),db:Session=Depends(get_db)):
    from app.services.reconciliation_v040 import build_advanced_reconciliation
    c=db.query(MonthlyClosing).filter(MonthlyClosing.competence==competence.replace(day=1)).first()
    if not c or c.status != 'CLOSED': raise HTTPException(404,'Fechamento não encontrado.')
    result=build_advanced_reconciliation(db,c.competence)
    return {"competence":c.competence.isoformat(),"status":"PASS" if result["snapshot_hash"]==json.loads(c.snapshot_json).get("reconciliation_hash") else "FAIL","stored_hash":c.snapshot_hash,"current_hash":result["snapshot_hash"],"snapshot":result["snapshot"]}

@router.get('/loan-engine/overdue')
def loan_engine_overdue(admin=Depends(require_admin), db: Session = Depends(get_db)):
    from datetime import date
    from decimal import Decimal
    rows = db.query(LoanInstallment).filter(LoanInstallment.due_date < date.today(), LoanInstallment.status != 'PAID').order_by(LoanInstallment.due_date).all()
    return {'items': [{
        'id': i.id, 'loan_id': i.loan_id, 'number': i.number, 'due_date': i.due_date.isoformat(),
        'amount': money(i.amount), 'penalty_amount': money(i.penalty_amount),
        'paid_amount': money(i.paid_amount), 'remaining': money(Decimal(i.amount)+Decimal(i.penalty_amount or 0)-Decimal(i.paid_amount or 0)),
        'days_overdue': (date.today()-i.due_date).days, 'status': i.status
    } for i in rows]}
