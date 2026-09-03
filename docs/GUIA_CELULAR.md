# FRcaixinha 2.0 — Guia pelo celular

Este guia foi preparado para operar o projeto usando apenas Android + navegador. O caminho recomendado é GitHub para armazenar o código e GitHub Actions para gerar o APK. O backend pode ser executado primeiro em um VPS Linux com Docker.

## 1. Criar o repositório
1. Abra GitHub no navegador.
2. Crie um repositório privado chamado `FRcaixinha-2.0`.
3. Não envie `.env` nem tokens.
4. Envie o conteúdo desta pasta para o repositório.

## 2. Preparar o backend no VPS
Instale Docker e Docker Compose no Ubuntu 24.04. Depois:

```bash
git clone SEU_REPOSITORIO
cd FRcaixinha-2.0
cp .env.example .env
```

Edite `.env` e troque `JWT_SECRET` e `POSTGRES_PASSWORD`. Gere segredos longos e aleatórios; nunca use os exemplos em produção.

Suba os serviços:

```bash
docker compose up -d --build
```

Rode as migrations:

```bash
docker compose exec api alembic upgrade head
```

Verifique:

```bash
docker compose ps
docker compose logs --tail=100 api
```

A API de desenvolvimento fica em `http://IP_DO_SERVIDOR:8000`. Em produção, coloque HTTPS/reverse proxy antes de liberar acesso externo.

## 3. Primeiro acesso
Crie o primeiro administrador usando o seed/fluxo previsto no backend. Não use dados reais até concluir o ambiente de homologação.

## 4. Configurar o aplicativo Android
O aplicativo lê a URL da API por `API_URL`:

```bash
flutter build apk --release --dart-define=API_URL=https://api.seudominio.com/api
```

Pelo celular, não é necessário executar Flutter localmente. O workflow `Android APK` deste repositório faz o build na nuvem.

## 5. Gerar APK pelo GitHub
1. Abra o repositório.
2. Vá em **Actions**.
3. Selecione **Android APK**.
4. Execute **Run workflow**.
5. Configure a variável de repositório `API_URL` com a URL HTTPS da API, terminando em `/api`.
6. Aguarde o workflow.
7. Baixe o artefato `frcaixinha-release-apk` no próprio GitHub e instale o APK no Android.

## 6. Ordem recomendada de implantação
1. Ambiente de teste.
2. PostgreSQL + Redis.
3. Migrations.
4. Criar administrador.
5. Testar login.
6. Testar participantes e contribuições.
7. Testar pagamentos Mercado Pago em sandbox/homologação quando disponível.
8. Testar empréstimos e parcelas.
9. Testar reconciliação, fechamento e auditoria.
10. Configurar domínio + HTTPS.
11. Backup e restauração.
12. Somente depois migrar para produção.

## 7. Segurança
- Nunca publicar `.env`.
- Nunca colocar `JWT_SECRET`, token Mercado Pago ou senha SMTP no GitHub.
- Em produção, `APP_ENV=production` exige `ALLOWED_HOSTS` e `CORS_ORIGINS` explícitos.
- Não usar conta bancária pessoal para custodiar dinheiro de terceiros.
- Antes de operação financeira real, faça revisão jurídica/regulatória e de LGPD.
