from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / 'backend' / 'alembic' / 'versions'


def _revisions():
    rows = {}
    for path in VERSIONS.glob('*.py'):
        text = path.read_text()
        rev = re.search(r'revision\s*=\s*[\'\"]([^\'\"]+)', text)
        down = re.search(r'down_revision\s*=\s*[\'\"]([^\'\"]+)', text)
        if rev:
            rows[rev.group(1)] = down.group(1) if down else None
    return rows


def test_alembic_chain_has_single_head():
    rows = _revisions()
    referenced = {v for v in rows.values() if v}
    assert set(rows) - referenced == {'0011_penalty_allocation_v029'}


def test_environment_templates_are_placeholders():
    for name in ('.env.production.example', '.env.staging.example'):
        text = (ROOT / name).read_text().lower()
        assert 'change_me' in text or 'test-change_me' in text
        assert 'mercadopago_access_token=' in text or 'mercado_pago_access_token=' in text


def test_release_artifacts_exist():
    required = [
        ROOT / 'docs' / 'v0.23-release-candidate.md',
        ROOT / 'scripts' / 'rc_v023.sh',
        ROOT / 'docker-compose.prod.yml',
        ROOT / 'ops' / 'prometheus.yml',
        ROOT / 'ops' / 'alertmanager' / 'alertmanager.yml',
    ]
    assert all(p.exists() for p in required)
