from datetime import date
from io import BytesIO
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from app.api.deps import require_admin
from app.db.session import get_db
from app.services.reports_v10 import monthly_report, member_statement, loan_report, delinquency_report
from app.services.reports_v042 import monthly_accountability, annual_accountability, participant_performance, persist_report, csv_text

router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])

def pdf_response(title, rows):
    buf=BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,rightMargin=36,leftMargin=36,topMargin=36,bottomMargin=36)
    styles=getSampleStyleSheet(); story=[Paragraph(title,styles["Title"]),Spacer(1,12)]
    data=[[str(a),str(b)] for a,b in rows]
    table=Table(data,colWidths=[210,300]); table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.lightgrey),("GRID",(0,0),(-1,-1),0.5,colors.grey),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),("TOPPADDING",(0,0),(-1,-1),6)
    ]))
    story.append(table); doc.build(story); buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{title.lower().replace(" ","_")}.pdf"'})

@router.get("/monthly")
def monthly(competence: date, admin=Depends(require_admin), db: Session=Depends(get_db)):
    return monthly_report(db, competence)

@router.get("/monthly.pdf")
def monthly_pdf(competence: date, admin=Depends(require_admin), db: Session=Depends(get_db)):
    r=monthly_report(db, competence)
    return pdf_response(f"Relatório mensal - {r['competence']}", [("Competência",r["competence"]),("Contribuições pagas",r["contributions_paid"]),("Despesas",r["expenses"]),("Juros recebidos",r["interest_received"]),("Resultado operacional",r["operating_result"]),("Créditos no período",r["ledger_credits_in_period"]),("Débitos no período",r["ledger_debits_in_period"])])

@router.get("/member/{member_id}/statement")
def statement(member_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=member_statement(db,member_id)
    if not r: raise HTTPException(404,"Membro não encontrado.")
    return r

@router.get("/member/{member_id}/statement.pdf")
def statement_pdf(member_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=member_statement(db,member_id)
    if not r: raise HTTPException(404,"Membro não encontrado.")
    rows=[("Participante",r["member"]["name"]),("CPF",r["member"]["cpf"]),("Status",r["member"]["status"]),("Contribuições pagas",r["totals"]["contributions_paid"]),("Pagamentos de empréstimos",r["totals"]["loan_payments"]),("Saldo de empréstimos",r["totals"]["loan_outstanding"])]
    return pdf_response(f"Extrato - {r['member']['name']}",rows)

@router.get("/loans")
def loans(admin=Depends(require_admin), db:Session=Depends(get_db)):
    return loan_report(db)

@router.get("/accountability/monthly")
def accountability_monthly(competence: date, admin=Depends(require_admin), db:Session=Depends(get_db)):
    return monthly_accountability(db, competence)

@router.get("/accountability/monthly.csv")
def accountability_monthly_csv(competence: date, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=monthly_accountability(db, competence)
    return StreamingResponse(BytesIO(csv_text(r).encode()),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="prestacao_contas_{competence.isoformat()}.csv"'})

@router.get("/accountability/annual")
def accountability_annual(year:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    return annual_accountability(db, year)

@router.get("/accountability/annual.csv")
def accountability_annual_csv(year:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=annual_accountability(db, year)
    return StreamingResponse(BytesIO(csv_text(r).encode()),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="prestacao_contas_{year}.csv"'})

@router.post("/accountability/snapshot")
def accountability_snapshot(competence:date, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=monthly_accountability(db, competence); row=persist_report(db,'MONTHLY',competence,r,admin.id); db.commit()
    return {'id':row.id,'snapshot_hash':row.snapshot_hash,'report':r}

@router.get("/participant/{member_id}/performance")
def participant_performance_report(member_id:int, admin=Depends(require_admin), db:Session=Depends(get_db)):
    r=participant_performance(db,member_id)
    if not r: raise HTTPException(404,'Membro não encontrado.')
    return r

@router.get("/delinquency")
def delinquency(admin=Depends(require_admin), db:Session=Depends(get_db)):
    return delinquency_report(db)
