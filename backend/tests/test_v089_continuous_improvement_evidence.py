from app.services.continuous_improvement_evidence_v089 import canonical, digest, sanitize_filename

def test_evidence_hash_is_stable():
    a={'execution_id':3,'sha256':'abc','size_bytes':10}
    b={'size_bytes':10,'sha256':'abc','execution_id':3}
    assert canonical(a)==canonical(b)
    assert digest(a)==digest(b)
    assert len(digest(a))==64

def test_filename_is_sanitized_and_path_is_removed():
    assert sanitize_filename('../../comprovante final.pdf') == 'comprovante_final.pdf'

def test_manifest_hash_changes_when_file_changes():
    a=[{'id':1,'version':1,'original_name':'a.pdf','size_bytes':10,'sha256':'aaa'}]
    b=[{'id':1,'version':1,'original_name':'a.pdf','size_bytes':11,'sha256':'aaa'}]
    assert digest(a)!=digest(b)
