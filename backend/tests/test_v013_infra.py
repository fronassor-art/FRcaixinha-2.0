from pathlib import Path
from app.core.config import Settings

def test_production_settings_lists():
    s = Settings(database_url='sqlite://', jwt_secret='x', allowed_hosts='api.example.com,localhost', cors_origins='https://app.example.com')
    assert s.allowed_hosts_list == ['api.example.com','localhost']
    assert s.cors_origins_list == ['https://app.example.com']

def test_production_files_exist():
    root = Path(__file__).parents[2]
    for rel in ['docker-compose.prod.yml','backend/Dockerfile','.env.production.example','ops/nginx/nginx.conf','scripts/backup_postgres.sh']:
        assert (root / rel).exists()
