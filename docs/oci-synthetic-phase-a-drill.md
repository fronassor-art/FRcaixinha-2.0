# Synthetic Phase A backup and restore drill

This harness creates its own disposable PostgreSQL 16 container. It migrates
only that container, inserts one obviously synthetic workflow evidence file,
creates a genuine Phase A backup, and restores it into a second empty database
in the same container. It never calls Google Drive, the API, or the worker.

## Prerequisites and command

Use a local Docker daemon available through `/var/run/docker.sock`, Python with
`backend/requirements.txt`, PostgreSQL 16 `pg_dump` and `pg_restore`, and
`age`/`age-keygen` v1.2.1. The Docker daemon must already have the
`postgres:16-alpine` image; the harness uses `--pull=never`. Create two distinct
mode-0700 directories outside the repository and run from `backend`:

```sh
python -m app.synthetic_phase_a_drill \
  --package-root /private/synthetic-phase-a-packages \
  --identity-root /private/synthetic-phase-a-identities
```

The harness takes no database address, password, OAuth credential, or age key
from the operator. It refuses an inherited `DATABASE_URL_FILE`, builds a fresh
child environment with `APP_ENV=test`, and sets both `DATABASE_URL` and `PG*`
for the same randomly named test database on a Docker-assigned loopback port.
Before migration, backup, and restore, it checks those settings and compares
the server system identifier obtained over the published port with the one
obtained inside the exact container it created. It requires server and client
version 16. The restore target is a separate empty `frcaixinha_restore_*`
database. Child commands run outside `backend` so its `.env` is not loaded.

The private age identity is generated for this run and stays in the identity
directory. It is never included in the Phase A package. The output JSON gives
the final package path, identity path, backup ID, and size/SHA-256 of a
deterministic transport envelope. Only after the restore and existing read-only
integrity checks pass are the package and identity moved to their final paths.
Keep both paths private and preserve the identity separately; losing it makes
the package unusable. Neither path belongs in Git or Google Drive together.

The Backend CI job runs this drill with temporary runner directories. Its
output is evidence that the harness works on an isolated PostgreSQL 16 runner;
the CI job does not publish its identity or package as artifacts. A CI package
does not constitute a persistent local backup for a later transport test.
