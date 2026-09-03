from __future__ import annotations
import hashlib, json
from decimal import Decimal
from sqlalchemy.orm import Session
from app.models import Loan, Member, Group, AllocationTransparencySnapshot, AllocationDecisionRecord
from app.services.approval_engine_v048 import evaluate_loan_pipeline
from app.services.capacity_optimizer_v053 import optimize_capacity
from app.services.allocation_governance_v055 import build_governance_allocation

D=Decimal

def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",",":"), ensure_ascii=False, default=str)

def sha(data): return hashlib.sha256(canonical(data).encode()).hexdigest()

def evaluate_integrated(db: Session, loan_id: int, *, horizon_months=12, scenario='BASE'):
    loan=db.get(Loan, loan_id)
    if not loan: raise ValueError('Empréstimo não encontrado.')
    member=db.get(Member, loan.member_id)
    if not member: raise ValueError('Participante não encontrado.')
    group=db.get(Group, member.group_id)
    if not group: raise ValueError('Grupo não encontrado.')
    pipeline=evaluate_loan_pipeline(db, loan, persist_risk=False, include_release=True)
    capacity=optimize_capacity(db, group_id=group.id, member_id=member.id, horizon_months=horizon_months, scenario=scenario)
    governed=build_governance_allocation(db, group_id=group.id, capacity=capacity.get('capacity'), requested_amount=loan.principal)
    member_item=next((x for x in governed.get('items',[]) if x.get('member_id')==member.id), None)
    requested=D(str(loan.principal))
    cap=D(str(capacity.get('capacity',0)))
    allocation=D(str(member_item.get('governed_amount',0))) if member_item else D('0')
    checks={
      'approval_pipeline': pipeline['decision'] == 'ALLOW',
      'capacity': cap >= requested,
      'allocation_recommendation': allocation >= requested,
      'no_blocking_errors': not pipeline.get('errors'),
    }
    if pipeline['decision']=='BLOCK' or pipeline.get('errors'):
        final='BLOCK'
    elif not checks['capacity'] or not checks['allocation_recommendation']:
        final='REVIEW'
    elif pipeline['decision']=='REVIEW':
        final='REVIEW'
    else: final='ALLOW'
    result={'schema':'v0.58','loan_id':loan.id,'group_id':group.id,'member_id':member.id,'requested_amount':requested,
      'scenario':scenario.upper(),'horizon_months':horizon_months,'final_decision':final,'checks':checks,
      'approval_pipeline':pipeline,'capacity':capacity,'allocation':governed,
      'member_allocation':member_item,'release_ready': final=='ALLOW',
      'note':'Motor integrado de governança é pré-decisório e não cria, aprova ou libera empréstimos.'}
    result['integrity_hash']=sha(result)
    return result

def persist_snapshot(db: Session, *, result: dict, actor_id: int):
    from app.models import IntegratedGovernanceSnapshot
    row=IntegratedGovernanceSnapshot(group_id=result['group_id'], loan_id=result['loan_id'], member_id=result['member_id'],
      final_decision=result['final_decision'], scenario=result['scenario'], horizon_months=result['horizon_months'],
      snapshot_json=canonical(result), snapshot_hash=result['integrity_hash'], generated_by=actor_id)
    db.add(row); db.flush(); return row
