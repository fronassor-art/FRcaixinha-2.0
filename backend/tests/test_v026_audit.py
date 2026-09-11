from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def revisions():
    rows = {}
    for path in (ROOT / "backend" / "alembic" / "versions").glob("*.py"):
        text = path.read_text()
        rev = re.search(r'(?m)^revision(?:\s*:[^=]+)?\s*=\s*[\'"]([^\'"]+)', text)
        down = re.search(r'(?m)^down_revision(?:\s*:[^=]+)?\s*=\s*[\'"]([^\'"]+)', text)
        if rev:
            rows[rev.group(1)] = down.group(1) if down else None
    return rows


def test_security_chain_is_present_and_single_head():
    import subprocess

    result = subprocess.run(
        ["alembic", "heads"],
        capture_output=True,
        text=True,
        check=True,
    )

    heads = [
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    assert heads == ["d04267d34476"]

    rows = revisions()
    assert rows["0006_security_v11"] == "0005_financial_operations"
    assert rows["0007_notifications"] == "0006_security_v11"

def test_production_hardening_contracts():
    compose = (ROOT / "docker-compose.prod.yml").read_text()
    main = (ROOT / "backend/app/main.py").read_text()
    auth = (ROOT / "backend/app/api/auth.py").read_text()
    nginx = (ROOT / "ops/nginx/nginx.production.conf.template").read_text()

    assert 'DATABASE_URL_FILE' in compose
    assert 'JWT_SECRET_FILE' in compose
    assert 'MERCADO_PAGO_ACCESS_TOKEN_FILE' in compose
    assert 'location = /metrics' in nginx
    assert 'docs_url=None if settings.app_env.lower() == "production"' in main
    assert 'f"Use este token temporário' not in auth
