# OCI A1 single-host topology

This directory defines a provider-specific Compose topology for one ARM64 OCI Ampere A1 VM. It complements the cloud-neutral `docker-compose.prod.yml`; it does not replace that contract or provision OCI resources. The backend image is pulled from GHCR by an approved `@sha256:` digest. The initial digest in `.env.oci-a1.example` was checked for anonymous pull access; each future release needs its own approved digest.

This topology is **not production approved**. Ingress, HTTPS, backups, restore tests, OCI Vault materialization, runtime smoke tests, and the G3D4 Redis/worker financial replay safety audit remain open. PostgreSQL and Redis have no published ports. Web also has no published port until a separate ingress package is approved.

## Host layout and prerequisites

Mount a persistent block volume at `FRCAIXINHA_DATA_ROOT` before using Compose. The example root is `/srv/frcaixinha`; verify that it is a separate mount with `mountpoint -q /srv/frcaixinha`. A directory on the VM boot filesystem does not meet this contract. Provision these directories on that volume before starting containers:

```text
/srv/frcaixinha/
├── postgres/
├── redis/
├── evidence/
├── backup-staging/
├── secrets/
└── config/
```

`postgres`, `redis`, and `evidence` are bind mounts with `create_host_path: false`. `backup-staging` and `config` are reserved only; this package does not implement backup. Evidence remains on one shared POSIX directory, mounted at `/var/lib/frcaixinha/workflow-evidence` in both web and worker. It is not replaced by object storage. The image runs the backend as UID 10001, so evidence must be writable by that UID. Check ownership and access for PostgreSQL and Redis data directories against the actual container users before launch.

Copy `.env.oci-a1.example` to `.env.oci-a1` and `app.env.example` to `app.env` on the deployment host. These local files are not release artifacts and must not be committed. Set the approved immutable backend image, data root, and secrets directory in `.env.oci-a1`. A release image must match `ghcr.io/fronassor-art/frcaixinha-backend@sha256:<64 hexadecimal characters>`; a tag alone is not an acceptable release reference. Compose itself checks only that the variable is nonempty, so run this host preflight before starting services:

```sh
set -e
set -a
. ops/oci-a1/.env.oci-a1
set +a
printf '%s\n' "$FRCAIXINHA_BACKEND_IMAGE" |
  grep -Eq '^ghcr\.io/fronassor-art/frcaixinha-backend@sha256:[0-9a-f]{64}$'
mountpoint -q "$FRCAIXINHA_DATA_ROOT"
```

`app.env` contains non-secret app settings. Fill `ALLOWED_HOSTS` and `CORS_ORIGINS` with the real host/origin before running web or worker. The blank example intentionally fails production settings validation. Keep the same `app.env` for both processes so financial settings do not drift. Do not put secrets in `app.env`.

## Secret files

Create the following files outside Git under `FRCAIXINHA_SECRETS_DIR` using the approved secret materialization process:

| File | Consumer | Contract |
| --- | --- | --- |
| `postgres_password` | PostgreSQL | `POSTGRES_PASSWORD_FILE`; must match the password in `database_url` |
| `database_url` | web and worker | `DATABASE_URL_FILE`; `postgresql+psycopg://` with Docker host `postgres` |
| `jwt_secret` | web and worker | `JWT_SECRET_FILE` |
| `redis_url` | worker | `REDIS_URL_FILE`; Docker host `redis` |

File-backed Compose secrets retain host file ownership and mode; Compose cannot remap them with `uid`, `gid`, or `mode`. Ensure the non-root backend user (UID 10001) can read its three secret files, and the PostgreSQL container can read `postgres_password`, without making the host secrets broadly readable. Do not place literal credentials in Git, the image, Compose, shell history, or `app.env`.

Mercado Pago, SMTP, and payout encryption secrets are not configured by this layer. The web process can start without Mercado Pago credentials; PIX cannot be enabled until its credentials and webhook configuration are supplied through a separate approved process. OCI Vault integration is not implemented here.

## Networks and service behavior

`db_internal` joins postgres, web, and worker. `redis_internal` joins only Redis and worker. Both are Docker internal networks. `app_egress` joins web and worker for outbound access. There are no host port mappings for PostgreSQL, Redis, web, or worker. A future ingress layer must provide public HTTPS and the Mercado Pago webhook path.

The web process runs the image's `sh ./start.sh`; the worker runs `python -m app.worker.main`. The web readiness check calls `http://127.0.0.1:8000/health/ready` inside its container and checks the database. The worker has no HTTP health endpoint in the application; a worker heartbeat monitor is a later gate.

The application web middleware uses Redis for best-effort rate limiting, and `/metrics` reads the worker heartbeat from Redis. This topology deliberately does not join web to `redis_internal`, so those two web features fall back when Redis cannot be reached. Production security and monitoring must address that before approval.

Redis AOF (`appendonly yes`, `appendfsync everysec`) provides operational durability only. **PostgreSQL remains the financial source of truth.** Redis has no published host port; it belongs only to the `redis_internal` network (`internal: true`), whose only clients in this topology are the worker and Redis itself. Because Redis authentication is not implemented in this package, `protected-mode no` allows the worker to connect from its separate container. This does not make Redis public. Redis authentication/ACL hardening remains required before production approval. The G3D4 package must audit and correct financial task re-execution safety around Redis locks before any production approval. `OCI_TOPOLOGY_READY` does not mean `PRODUCTION_APPROVED`.

- `REDIS_AUTHENTICATION=NOT_IMPLEMENTED`
- `REDIS_PROTECTED_MODE=DISABLED_BY_DESIGN_INSIDE_ISOLATED_DOCKER_NETWORK`
- `REDIS_PUBLIC_PORT=NONE`
- `REDIS_NETWORK_CLIENTS=worker only`
- `REDIS_FINANCIAL_SOURCE_OF_TRUTH=NO`

## Controlled startup and migration

Run these commands from the repository root on the deployment host, after mounting the persistent volume, preparing directories and secrets, filling `app.env`, and checking the immutable image reference. The secrets and app env files must already exist. The approved image is pulled; the backend is not built on the VM.

```sh
docker compose --env-file ops/oci-a1/.env.oci-a1 \
  -f ops/oci-a1/docker-compose.oci-a1.yml config --quiet

docker compose --env-file ops/oci-a1/.env.oci-a1 \
  -f ops/oci-a1/docker-compose.oci-a1.yml up -d postgres redis

docker compose --env-file ops/oci-a1/.env.oci-a1 \
  -f ops/oci-a1/docker-compose.oci-a1.yml ps

docker compose --env-file ops/oci-a1/.env.oci-a1 \
  -f ops/oci-a1/docker-compose.oci-a1.yml run --rm --no-deps web alembic upgrade head

docker compose --env-file ops/oci-a1/.env.oci-a1 \
  -f ops/oci-a1/docker-compose.oci-a1.yml run --rm --no-deps web alembic current

docker compose --env-file ops/oci-a1/.env.oci-a1 \
  -f ops/oci-a1/docker-compose.oci-a1.yml up -d web worker
```

Run `alembic upgrade head` **once** for a release, after PostgreSQL is healthy and before web/worker start. The expected revision for this package is `0103_pix_reconciliation_schema_a377b4r4`. Neither container startup command runs migrations. A first production database requires a separately approved migration and backup plan; this README does not authorize deployment.

Stop processes without deleting the bind-mounted data:

```sh
docker compose --env-file ops/oci-a1/.env.oci-a1 \
  -f ops/oci-a1/docker-compose.oci-a1.yml stop web worker
```

Do not use `docker compose down -v` in production. Backups, off-host copies, and restore tests still need their own implementation and validation.
