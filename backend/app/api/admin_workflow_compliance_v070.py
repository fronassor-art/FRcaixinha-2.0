from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import json
from app.api.deps import require_admin
from app.db.session import get_db
from app.models import WorkflowComplianceSnapshot
from app.services.workflow_compliance_v070 import build_compliance, persist_compliance_snapshot, latest_compliance

router = APIRouter(prefix='/admin/workflow-compliance', tags=['workflow-compliance-v070'])

@router.get('')
def current(admin=Depends(require_admin), db: Session = Depends(get_db)):
    row = latest_compliance(db)
    if row:
        return {'id': row.id, 'snapshot_date': row.snapshot_date.isoformat(), 'status': row.status,
                'snapshot_hash': row.snapshot_hash, 'snapshot': json.loads(row.snapshot_json)}
    snapshot = build_compliance(db)
    return {'id': None, 'snapshot_date': snapshot['generated_at'][:10], 'status': snapshot['status'],
            'snapshot_hash': __import__('hashlib').sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(), 'snapshot': snapshot}

@router.post('/snapshot')
def snapshot(admin=Depends(require_admin), db: Session = Depends(get_db)):
    row, result = persist_compliance_snapshot(db, generated_by=admin.id)
    db.commit()
    return {'id': row.id, 'snapshot_date': row.snapshot_date.isoformat(), 'status': row.status,
            'snapshot_hash': row.snapshot_hash, 'snapshot': result}

@router.get('/history')
def history(limit: int = 30, admin=Depends(require_admin), db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = db.query(WorkflowComplianceSnapshot).order_by(WorkflowComplianceSnapshot.snapshot_date.desc()).limit(limit).all()
    return [{'id': r.id, 'snapshot_date': r.snapshot_date.isoformat(), 'status': r.status,
             'snapshot_hash': r.snapshot_hash, 'created_at': r.created_at.isoformat()} for r in rows]

@router.get('/check')
def check(admin=Depends(require_admin), db: Session = Depends(get_db)):
    return build_compliance(db)
