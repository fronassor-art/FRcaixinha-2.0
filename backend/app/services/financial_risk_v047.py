from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import FinancialRiskAssessment, Member, Loan, LoanInstallment, SecurityEvent, AuditLog

CENT = Decimal('0.01')

def money(v): return Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP)

def assess_loan(db: Session, loan: Loan, persist: bool = True):
    member = db.get(Member, loan.member_id)
    if not member: return {'status':'BLOCKED','score':100,'reasons':['Participante não encontrado.']}
    score = 0; reasons=[]; now=datetime.now(timezone.utc)
    overdue = db.query(LoanInstallment).join(Loan).filter(Loan.member_id==member.id, LoanInstallment.paid_at.is_(None), LoanInstallment.due_date < now.date()).count()
    if overdue:
        score += min(40, overdue*15); reasons.append(f'{overdue} parcela(s) em atraso.')
    active = db.query(Loan).filter(Loan.member_id==member.id, Loan.status=='ACTIVE').count()
    if active > 0:
        score += min(25, active*15); reasons.append(f'{active} empréstimo(s) ativo(s).')
    income = Decimal(member.declared_monthly_income or 0)
    if income > 0 and Decimal(loan.principal) > income:
        score += 20; reasons.append('Valor solicitado supera a renda mensal declarada.')
    elif income > 0 and Decimal(loan.principal) > income*Decimal('0.5'):
        score += 10; reasons.append('Valor solicitado supera 50% da renda mensal declarada.')
    sec = db.query(SecurityEvent).filter(SecurityEvent.user_id==member.user_id, SecurityEvent.created_at>=now-timedelta(hours=24)).all()
    critical = sum(1 for e in sec if e.severity=='CRITICAL')
    warning = sum(1 for e in sec if e.severity=='WARNING')
    score += min(30, critical*15 + warning*5)
    if critical: reasons.append(f'{critical} evento(s) de segurança crítico(s) nas últimas 24h.')
    elif warning: reasons.append(f'{warning} alerta(s) de segurança nas últimas 24h.')
    if score >= 70: status='BLOCKED'
    elif score >= 40: status='REVIEW'
    else: status='PASS'
    result={'status':status,'score':score,'reasons':reasons,'rules':{'overdue_installments':overdue,'active_loans':active,'declared_income':str(income) if income else None,'recent_critical_security_events':critical,'recent_warning_security_events':warning}}
    if persist:
        row=FinancialRiskAssessment(subject_type='LOAN', subject_id=str(loan.id), member_id=member.id, score=score, status=status, reasons=json.dumps(reasons, ensure_ascii=False), rules_json=json.dumps(result['rules'], ensure_ascii=False), created_at=now)
        db.add(row); db.add(AuditLog(actor_user_id=None, action='RISK_ASSESSMENT', entity_type='LOAN', entity_id=str(loan.id), details=json.dumps(result, ensure_ascii=False))); db.commit(); result['assessment_id']=row.id
    return result
