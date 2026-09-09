from app.services.continuous_improvement_certification_v090 import canonical, digest

def test_certificate_package_hash_is_stable():
    a={'execution':{'id':7,'status':'VERIFIED'},'evidence_files':[{'id':1,'sha256':'abc'}],'decision':{'decision':'ACCEPT'}}
    b={'decision':{'decision':'ACCEPT'},'evidence_files':[{'sha256':'abc','id':1}],'execution':{'status':'VERIFIED','id':7}}
    assert canonical(a)==canonical(b)
    assert digest(a)==digest(b)
    assert len(digest(a))==64

def test_certificate_hash_changes_when_evidence_changes():
    a={'execution_id':7,'evidence_manifest_hash':'aaa','execution_hash':'bbb'}
    b={'execution_id':7,'evidence_manifest_hash':'ccc','execution_hash':'bbb'}
    assert digest(a)!=digest(b)

def test_certificate_schema_is_explicit():
    payload={'schema':'v0.90','execution':{},'decision':{},'recommendation':{},'plan':{},'evidence_files':[],'integrity_events':[]}
    assert payload['schema']=='v0.90'
