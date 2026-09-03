from __future__ import annotations
import hashlib, json
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import AllocationPolicy, Member, Contribution
from app.services.resource_allocation_v054 import allocate_resources

D=Decimal; CENT=D('0.01')

def money(v): return D(str(v or 0)).quantize(CENT, rounding=ROUND_DOWN)

def _default():
    return dict(name='Política padrão', quota_weight=D('1'), payment_history_weight=D('1'), tenure_weight=D('.25'), risk_weight=D('1'), review_factor=D('.5'), tie_breaker='OLDEST_MEMBER', version=1, active=True)

def get_policy(db: Session, group_id: int):
    p=db.query(AllocationPolicy).filter(AllocationPolicy.group_id==group_id).first()
    if p: return p
    return None

def _tenure_score(m):
    joined=getattr(m,'joined_at',None)
    if not joined: return D('0')
    if getattr(joined,'tzinfo',None) is None: joined=joined.replace(tzinfo=timezone.utc)
    days=max(0,(datetime.now(timezone.utc)-joined).days)
    return D(min(days,365))/D(365)

def _payment_score(db, member_id):
    rows=db.query(Contribution).filter(Contribution.member_id==member_id).all()
    if not rows: return D('0.5')
    paid=sum(1 for x in rows if str(x.status).upper() in ('PAID','CONFIRMED','SETTLED'))
    return D(paid)/D(len(rows))

def build_governance_allocation(db: Session, *, group_id: int, capacity=None, requested_amount=None):
    base=allocate_resources(db, group_id=group_id, capacity=capacity, requested_amount=requested_amount)
    policy=get_policy(db,group_id)
    cfg=_default() if policy is None else {k:getattr(policy,k) for k in ('name','quota_weight','payment_history_weight','tenure_weight','risk_weight','review_factor','tie_breaker','version','active')}
    if not cfg['active']:
        cfg=_default()
    items=base.get('items',[])
    members={m.id:m for m in db.query(Member).filter(Member.group_id==group_id).all()}
    scored=[]
    for item in items:
        m=members.get(item['member_id'])
        q=D(str(item.get('quota_units',1)))
        risk_factor=D('1') if item.get('risk_decision')=='ALLOW' else (D(str(cfg['review_factor'])) if item.get('risk_decision')=='REVIEW' else D('0'))
        payment=_payment_score(db,item['member_id'])
        tenure=_tenure_score(m)
        score=(q*D(str(cfg['quota_weight'])) + payment*D(str(cfg['payment_history_weight'])) + tenure*D(str(cfg['tenure_weight'])))*risk_factor*D(str(cfg['risk_weight']))
        x=dict(item); x.update(payment_score=payment,tenure_score=tenure,priority_score=score)
        scored.append(x)
    total=sum((x['priority_score'] for x in scored),D('0'))
    cap=money(base.get('capacity',0))
    if total>0:
        remaining=cap
        for x in scored:
            x['governed_amount']=money(cap*x['priority_score']/total)
            remaining-=x['governed_amount']
        while remaining>=CENT:
            eligible=[x for x in scored if x['priority_score']>0]
            if not eligible: break
            key=(lambda x:(-x['priority_score'], x['member_id'])) if cfg['tie_breaker']=='OLDEST_MEMBER' else (lambda x:(x['member_id'],))
            eligible.sort(key=key)
            eligible[0]['governed_amount']+=CENT; remaining-=CENT
    else:
        for x in scored: x['governed_amount']=D('0.00')
        remaining=cap
    total_alloc=money(sum((x['governed_amount'] for x in scored),D('0')))
    return {'schema':'v0.55','group_id':group_id,'policy':cfg,'capacity':cap,'allocated_total':total_alloc,'unallocated':money(remaining),'decision':'ALLOW' if total_alloc>0 else 'BLOCKED','items':scored,'note':'Política de governança é recomendação auditável; não cria, aprova ou libera empréstimos.'}

def snapshot_hash(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str).encode()).hexdigest()
