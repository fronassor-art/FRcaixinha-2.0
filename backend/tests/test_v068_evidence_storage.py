from app.services.workflow_evidence_storage_v068 import sanitize_filename, validate_upload_metadata


def test_filename_is_sanitized_to_basename():
    assert sanitize_filename('../../foto final.png') == 'foto_final.png'


def test_upload_metadata_accepts_supported_pdf():
    name, ctype = validate_upload_metadata('relatorio.pdf', 'application/pdf')
    assert name == 'relatorio.pdf'
    assert ctype == 'application/pdf'


def test_upload_metadata_rejects_disallowed_extension():
    try:
        validate_upload_metadata('script.exe', 'application/octet-stream')
        assert False, 'expected ValueError'
    except ValueError as exc:
        assert 'Extensão' in str(exc)
