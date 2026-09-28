from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
DOC = (ROOT / "docs/v0.13-production.md").read_text(encoding="utf-8")


def _service(name: str, next_name: str | None = None) -> str:
    start = COMPOSE.index(f"\n  {name}:\n") + 1
    end = COMPOSE.index(f"\n  {next_name}:\n", start) if next_name else COMPOSE.index("\nsecrets:\n", start)
    return COMPOSE[start:end]


def test_production_compose_declares_separate_web_and_worker_same_image():
    web = _service("web", "worker")
    worker = _service("worker")

    assert "x-backend: &backend" in COMPOSE
    assert "<<: *backend" in web
    assert "<<: *backend" in worker
    assert 'command: ["python", "-m", "app.worker.main"]' in worker
    assert "CMD [\"sh\", \"./start.sh\"]" in (ROOT / "backend/Dockerfile").read_text()


def test_production_compose_uses_external_database_and_redis_secrets():
    web = _service("web", "worker")
    worker = _service("worker")

    assert "DATABASE_URL_FILE: /run/secrets/database_url" in web
    assert "JWT_SECRET_FILE: /run/secrets/jwt_secret" in web
    assert "DATABASE_URL_FILE: /run/secrets/database_url" in worker
    assert "JWT_SECRET_FILE: /run/secrets/jwt_secret" in worker
    assert "REDIS_URL_FILE: /run/secrets/redis_url" in worker
    assert "image: postgres" not in COMPOSE
    assert "image: redis" not in COMPOSE
    assert "POSTGRES_PASSWORD" not in COMPOSE


def test_production_web_and_worker_share_persistent_evidence_storage():
    web = _service("web", "worker")
    worker = _service("worker")
    mount = "workflow_evidence:/var/lib/frcaixinha/workflow-evidence"

    assert mount in web
    assert mount in worker
    assert re.search(r"(?m)^  workflow_evidence:\s*$", COMPOSE)
    assert "WORKFLOW_EVIDENCE_STORAGE_ROOT: /var/lib/frcaixinha/workflow-evidence" in web
    assert "WORKFLOW_EVIDENCE_STORAGE_ROOT: /var/lib/frcaixinha/workflow-evidence" in worker


def test_migrations_are_a_manual_single_release_step_not_process_startup():
    assert "alembic upgrade head" not in COMPOSE
    assert "alembic upgrade head" in DOC
    assert "run --rm --no-deps web alembic upgrade head" in DOC
    assert "Alembic upgrade" not in (ROOT / "backend/start.sh").read_text()
    assert "alembic" not in (ROOT / "backend/app/worker/main.py").read_text()


def test_production_example_has_no_fake_host_or_optional_mp_placeholder():
    example = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assert "api.example.com" not in example
    assert "app.example.com" not in example
    assert "MERCADO_PAGO_ACCESS_TOKEN=\n" in example
    assert "MERCADO_PAGO_WEBHOOK_SECRET=\n" in example
    assert "ALLOWED_HOSTS=\n" in example
    assert "CORS_ORIGINS=\n" in example
