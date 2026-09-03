# FRcaixinha 2.0 — v1.0

Aplicação privada de gestão financeira coletiva, com backend FastAPI/PostgreSQL/Redis e aplicativo Flutter Android.

## Começar pelo celular
Leia `docs/GUIA_CELULAR.md`.

Arquitetura inicial com Docker:
- PostgreSQL 16
- Redis 7
- FastAPI
- Worker diário
- Flutter Android

## Desenvolvimento local do backend
```bash
cp .env.example .env
# ajuste JWT_SECRET e POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec api alembic upgrade head
```

## Android via GitHub Actions
Configure a variável de repositório `API_URL` com a URL HTTPS da API + `/api`. O workflow `.github/workflows/android-build.yml` gera o APK release.

## Produção
Não use os valores de exemplo. Configure secrets, HTTPS, backups, monitoramento e revisão jurídica/regulatória antes de movimentar recursos reais.
