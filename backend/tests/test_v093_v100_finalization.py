from app.services.continuous_improvement_finalization_v093_100 import canonical, digest

def test_canonical_is_stable():
    assert canonical({'b':2,'a':1}) == '{"a":1,"b":2}'

def test_digest_is_sha256_hex():
    h=digest({'schema':'v1.0','status':'PASS'})
    assert len(h)==64 and all(c in '0123456789abcdef' for c in h)
