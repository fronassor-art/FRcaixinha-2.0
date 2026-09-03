from datetime import datetime, timezone, timedelta
from app.services.capa_effectiveness_v073 import canonical, review_hash

def test_canonical_deterministic_v073():
    assert canonical({'z':3,'a':1}) == canonical({'a':1,'z':3})

def test_review_hash_is_sha256_shape():
    class R:
        id=1; capa_id=2; result='OK'; score=90; reviewed_by=7; notes='x'; reviewed_at=datetime(2026,1,1,tzinfo=timezone.utc)
    h=review_hash(R())
    assert len(h)==64 and all(c in '0123456789abcdef' for c in h)
