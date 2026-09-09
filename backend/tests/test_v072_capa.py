from app.services.capa_v072 import canonical

def test_canonical_deterministic():
    assert canonical({'b':2,'a':1}) == canonical({'a':1,'b':2})
