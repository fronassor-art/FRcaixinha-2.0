from __future__ import annotations
import hashlib, json
from decimal import Decimal, ROUND_DOWN
from datetime import date
from sqlalchemy.orm import Session
from app.models import Member, Group, Loan
from app.services.capacity_optimizer_v053 import optimize_capacity
from app.services.approval_engine_v048 import evaluate_loan_pipeline

D=Decimal
CENT=D('0.01')

def money(v): return D(str(v or 0)).quantize(CENT, rounding=ROUND_DOWN)

def allocate_resources(db: Session, *, group_id: int, capacity: Decimal | None = None, requested_amount: Decimal | None = None):
    group=db.get(Group, group_id)
    if not group: raise ValueError('Grupo não encontrado.')
    if capacity is None:
        cap=optimize_capacity(db, group_id=group_id)['capacity']
    else: cap=money(capacity)
    cap=max(D('0'),cap)
    if requested_amount is not None: cap=min(cap,max(D('0'),money(requested_amount)))
    members=db.query(Member).filter(Member.group_id==group_id, Member.status=='ACTIVE').order_by(Member.id).all()
    rows=[]
    for m in members:
        active=db.query(Loan).filter(Loan.member_id==m.id, Loan.status.in_(['APPROVED','DISBURSED','ACTIVE'])).all()
        exposure=sum((D(x.principal) for x in active),D('0'))
        max_room=None if group.max_member_exposure is None else max(D('0'),D(str(group.max_member_exposure))-exposure)
        quota_units=D(str(m.quota.units)) if getattr(m,'quota',None) else D('1')
        # Risk-aware allocation: only PASS gets full weight; REVIEW remains eligible only for a reduced share.
        probe=Loan(member_id=m.id, principal=D('0.01'), monthly_rate=D('0'), installments=1, status='REQUESTED')
        try: risk=evaluate_loan_pipeline(db,probe,persist_risk=False,include_release=False)
        except Exception: risk={'decision':'REVIEW'}
        decision=risk.get('decision','REVIEW')
        weight=quota_units if decision=='ALLOW' else (quota_units*D('0.5') if decision=='REVIEW' else D('0'))
        rows.append({'member_id':m.id,'quota_units':quota_units,'weight':weight,'risk_decision':decision,'exposure':money(exposure),'member_room':money(max_room) if max_room is not None else None})
    total_weight=sum((r['weight'] for r in rows),D('0'))
    if cap<=0 or total_weight<=0:
        return {'schema':'v0.54','decision':'BLOCKED','group_id':group_id,'capacity':money(cap),'allocated_total':money(0),'items':rows,'reason':'Nenhum participante elegível para alocação.'}
    remaining=cap
    # Pro-rata first pass, capped by individual exposure room and max loan amount.
    for r in rows:
        raw=cap*r['weight']/total_weight
        limit=r['member_room']
        if group.max_loan_amount is not None:
            limit=D(str(group.max_loan_amount)) if limit is None else min(limit,D(str(group.max_loan_amount)))
        if limit is not None: raw=min(raw,limit)
        r['recommended_amount']=money(raw)
        remaining-=r['recommended_amount']
    # Redistribute residual cents fairly among participants with remaining room.
    while remaining >= CENT:
        candidates=[]
        for r in rows:
            room=r.get('member_room')
            limit=D(str(group.max_loan_amount)) if group.max_loan_amount is not None else None
            current=r['recommended_amount']
            if room is not None: limit=room if limit is None else min(limit,room)
            if r['weight']>0 and (limit is None or current+CENT<=limit): candidates.append(r)
        if not candidates: break
        candidates.sort(key=lambda x:(x['recommended_amount']/x['weight'] if x['weight'] else D('999999'), x['member_id']))
        candidates[0]['recommended_amount']+=CENT; remaining-=CENT
    for r in rows: r['recommended_amount']=money(r.get('recommended_amount',0))
    return {'schema':'v0.54','decision':'ALLOW' if any(r['recommended_amount']>0 for r in rows) else 'BLOCKED','group_id':group_id,'capacity':money(cap),'allocated_total':money(sum((r['recommended_amount'] for r in rows),D('0'))),'unallocated':money(remaining),'items':rows,'method':'PRO_RATA_QUOTA_RISK_AWARE','note':'Recomendação de alocação; não cria nem aprova empréstimos.'}

def snapshot_hash(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str).encode()).hexdigest()
