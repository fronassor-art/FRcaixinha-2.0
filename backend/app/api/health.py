from fastapi import APIRouter
from sqlalchemy import text
from app.db.session import engine
from app.core.config import settings
router = APIRouter()

@router.get("/health")
def health():
    return {"status": "ok", "version": "v0.31"}

@router.get("/health/ready")
def readiness():
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    if not db_ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail={"status":"not_ready","database":False})
    return {"status":"ready","database":True}
